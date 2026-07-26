"""租户级资源配额——共享资源（任务队列、连接池）按租户粒度公平调度。

改造自checklist E类"多租户资源/容量隔离子类"真实事故：一个系统即使已经
具备"按用户限流"和"按组织设置成本预算"两层意图，如果底层真正共享的
计算资源（任务队列、数据库连接池）完全不区分租户，一个任务量大的租户
仍然可以不成比例地挤占共享资源、拖慢其他租户——**"数据隔离"和"资源/
容量隔离"是两个必须分别评估的不同维度，不能因为做好了前者就默认后者
也已经解决**。

设计沿用`ainative_guardrail.limits.AgentLimits`的风格（按key查表、
未注册的key回退到默认值），但查询维度换成`tenant_id`，语义换成"当前
并发占用是否超出这个租户的配额上限"而不是静态参数查询。
"""

# 让类型注解可以延迟解析（比如函数里写`-> TenantQuota`时，这个类还没
# 定义完也不会报错），详见context.py里的详细解释，这里不再重复。
from __future__ import annotations

# dataclass——自动生成`__init__`等样板代码的装饰器，详见context.py里的
# 详细解释：只需要声明"这个类有哪些字段"，不用自己手写构造函数。
from dataclasses import dataclass

# 模块级的公共常量：写在函数外面、全大写命名，是Python里"这是一个不
# 应该被随便改动的固定值"的传统写法惯例（Python本身不强制禁止修改，
# 全靠命名约定自觉遵守）。这两个值分别是"没有单独注册配额的租户"默认
# 能用多少个任务槽位、多少条连接池连接。
DEFAULT_MAX_CONCURRENT_JOBS = 10
DEFAULT_MAX_POOL_CONNECTIONS = 5


# @dataclass(frozen=True)——自动生成构造函数，并且"冻结"这个类的实例，
# 一旦创建出来字段就不能再被修改。一份配额定义一旦确定，不应该被代码
# 中途悄悄改动，只能通过`TenantResourceTracker.register_quota()`整个
# 替换成一份新的`TenantQuota`——这样"配额到底是多少"永远有清晰的、
# 集中在一处的修改入口，不会有代码在别处偷偷改了某个字段。
@dataclass(frozen=True)
class TenantQuota:
    """单个租户的一组资源配额上限。"""

    # `字段名: 类型 = 默认值`——两个字段都有默认值，意味着
    # `TenantQuota()`（不传任何参数）也能正常构造出一份"默认配额"。
    max_concurrent_jobs: int = DEFAULT_MAX_CONCURRENT_JOBS
    max_pool_connections: int = DEFAULT_MAX_POOL_CONNECTIONS


class QuotaExceededError(RuntimeError):
    """租户尝试占用的资源超过其配额上限时抛出。"""


class TenantResourceTracker:
    """跟踪各租户对共享资源（任务队列名额、连接池连接数）的当前占用量，
    在真正开始占用之前做配额检查——而不是先占用、事后才发现超配额。

    用法::

        tracker = TenantResourceTracker()
        tracker.register_quota("tenant-42", max_concurrent_jobs=20)
        tracker.acquire_job_slot("tenant-42")   # 通过则计数+1
        try:
            ...
        finally:
            tracker.release_job_slot("tenant-42")
    """

    def __init__(self) -> None:
        # 三个内部字典，都是"租户ID -> 某个数值/对象"的映射，是这个类
        # 全部状态的真正存储位置：
        # - `_quotas`：每个租户各自登记的配额上限（没登记过的租户不会
        #   出现在这个字典里，查询时统一交给下面`_quota_for`处理兜底）。
        self._quotas: dict[str, TenantQuota] = {}
        # - `_active_jobs`：每个租户当前占用了多少个任务槽位。
        self._active_jobs: dict[str, int] = {}
        # - `_active_connections`：每个租户当前占用了多少条连接池连接。
        self._active_connections: dict[str, int] = {}

    def register_quota(
        # 函数参数列表里的这个单独的`*`，表示它后面的参数（这里是
        # `max_concurrent_jobs`/`max_pool_connections`）必须用"参数名=值"
        # 的方式调用，不能只按位置传参——这样调用处代码本身自带说明性
        # （一眼看出这两个数字分别是什么含义），也避免以后给函数加新
        # 参数时打乱位置对应关系。
        self, tenant_id: str, *, max_concurrent_jobs: int = DEFAULT_MAX_CONCURRENT_JOBS,
        max_pool_connections: int = DEFAULT_MAX_POOL_CONNECTIONS,
    ) -> bool:
        """登记（或更新）一个租户的配额上限。

        Returns:
            这个租户当前已经占用的资源量是否已经超过刚设置的新配额——
            比如一个租户已经持有3个任务名额，管理员在事故处置期间把配额
            临时调低到1，这个操作本身不会强制释放已经在跑的3个任务
            （不合理地打断正在执行的工作），但调用方必须能明确知道
            "这次调低配额没有立刻生效于已占用的资源"，而不是得到一个
            静默成功、看起来配额已经按新数字生效的假象。
        """
        # 直接用新传入的两个数字，构造一份全新的`TenantQuota`，整个
        # 覆盖掉这个租户原来登记的那份（如果之前登记过的话）——这是
        # "配额只能整体替换、不能局部修改单个字段"这个设计原则的体现。
        self._quotas[tenant_id] = TenantQuota(
            max_concurrent_jobs=max_concurrent_jobs, max_pool_connections=max_pool_connections,
        )
        # `dict.get(key, 默认值)`——去字典里查这个key对应的值，查不到
        # 就返回给定的默认值（这里是0，即"这个租户目前没有任何占用记录"）
        # ，不会像`dict[key]`那样在查不到时直接抛异常崩溃。
        # 这里检查"调低配额之前，这个租户已经占用的资源"是否超过了
        # "刚设置的新配额"——`or`表示只要任务槽位或连接池两者之一超了，
        # 就返回True，提醒调用方"这次调低没有立刻让已占用资源合规"。
        return (
            self._active_jobs.get(tenant_id, 0) > max_concurrent_jobs
            or self._active_connections.get(tenant_id, 0) > max_pool_connections
        )

    def _quota_for(self, tenant_id: str) -> TenantQuota:
        # 私有辅助方法（名字前面的下划线表示"仅供本类内部使用"）：查询
        # 一个租户的配额，如果这个租户压根没调用过`register_quota`登记
        # 过，就用`TenantQuota()`（全部走默认值）兜底——这就是模块顶部
        # docstring里说的"未注册的key回退到默认值"这一设计。
        return self._quotas.get(tenant_id, TenantQuota())

    def acquire_job_slot(self, tenant_id: str) -> None:
        """占用一个任务队列名额；超出该租户配额时抛`QuotaExceededError`，
        不静默排队等待——上层应该据此决定拒绝/延迟这次提交，而不是让任务
        无声地堆积在一条全组织共享的FIFO队列里（这正是checklist E类真实
        事故里`agent_jobs`表的设计缺口）。"""
        # 先查出这个租户的配额上限（没登记过就是默认值）。
        quota = self._quota_for(tenant_id)
        # 再查出这个租户当前已经占用了多少个任务槽位（没有记录就是0）。
        current = self._active_jobs.get(tenant_id, 0)
        if current >= quota.max_concurrent_jobs:
            # 已经占用的数量达到或超过了上限——不能再占用新的了，直接
            # 抛异常拒绝，把当前占用/上限的具体数字都写进错误信息里，
            # 方便排查为什么这次提交被拒绝。
            raise QuotaExceededError(
                f"tenant '{tenant_id}' has reached its concurrent job quota "
                f"({current}/{quota.max_concurrent_jobs})"
            )
        # 通过检查——真正把这个租户的占用计数加1，记录"多占用了一个槽位"。
        self._active_jobs[tenant_id] = current + 1

    def release_job_slot(self, tenant_id: str) -> None:
        # `max(0, ...)`——防御性写法：把占用计数减1之后，强制不能低于0
        # （即使调用方多调用了一次release、没有对应的acquire，占用计数
        # 也不会变成负数这种没有物理意义的值）。
        self._active_jobs[tenant_id] = max(0, self._active_jobs.get(tenant_id, 0) - 1)

    def acquire_connection(self, tenant_id: str) -> None:
        # 逻辑和`acquire_job_slot`完全对称，只是换成了连接池这一种资源
        # ——两种资源（任务槽位/连接池连接）各自独立计数、独立配额，
        # 互不影响（这也是`test_connection_quota_is_tracked_independently_from_job_quota`
        # 这个测试专门验证的行为）。
        quota = self._quota_for(tenant_id)
        current = self._active_connections.get(tenant_id, 0)
        if current >= quota.max_pool_connections:
            raise QuotaExceededError(
                f"tenant '{tenant_id}' has reached its connection pool quota "
                f"({current}/{quota.max_pool_connections})"
            )
        self._active_connections[tenant_id] = current + 1

    def release_connection(self, tenant_id: str) -> None:
        self._active_connections[tenant_id] = max(0, self._active_connections.get(tenant_id, 0) - 1)

    def active_job_count(self, tenant_id: str) -> int:
        # 简单的查询方法：直接读取内部字典，查不到就返回0。
        return self._active_jobs.get(tenant_id, 0)

    def active_connection_count(self, tenant_id: str) -> int:
        return self._active_connections.get(tenant_id, 0)

    def usage_report(self) -> dict[str, dict[str, int | bool]]:
        """所有当前有活跃占用的租户的资源使用快照——供运维/仪表盘查看
        "有没有某个租户不成比例占用共享资源"。

        `is_over_quota`字段专门覆盖`register_quota()`一次性返回值之外的
        场景：配额被调低时已占用的资源不会被强制释放（不合理地打断正在
        执行的工作），这个字段让"事后"查看快照的仪表盘/巡检脚本也能持续
        发现这种状态，而不是只有当时调用`register_quota()`的那次调用点
        才知道。
        """
        # `set(A) | set(B)`——集合的"并集"运算：把两个字典的key各自转成
        # 集合，再合并去重，得到"要么在任务占用记录里出现过、要么在连接
        # 占用记录里出现过"的全部租户ID——这样即使一个租户只占用过任务
        # 槽位、从没占用过连接（或反过来），也不会被这份报告遗漏。
        tenant_ids = set(self._active_jobs) | set(self._active_connections)
        report: dict[str, dict[str, int | bool]] = {}
        # 遍历每一个"有过活跃占用"的租户，分别算出它的快照数据。
        for tenant_id in tenant_ids:
            quota = self._quota_for(tenant_id)
            active_jobs = self._active_jobs.get(tenant_id, 0)
            active_connections = self._active_connections.get(tenant_id, 0)
            report[tenant_id] = {
                "active_jobs": active_jobs,
                "active_connections": active_connections,
                # 任务槽位或连接池连接，只要有一项当前占用超过了配额
                # 上限，就标记这个租户"超配额"——这正是应对"配额被调低
                # 后已占用资源没有被强制释放"这种情况的持续可见性设计。
                "is_over_quota": (
                    active_jobs > quota.max_concurrent_jobs or active_connections > quota.max_pool_connections
                ),
            }
        return report
