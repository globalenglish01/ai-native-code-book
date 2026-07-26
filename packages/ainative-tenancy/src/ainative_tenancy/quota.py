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

from __future__ import annotations

from dataclasses import dataclass

DEFAULT_MAX_CONCURRENT_JOBS = 10
DEFAULT_MAX_POOL_CONNECTIONS = 5


@dataclass(frozen=True)
class TenantQuota:
    """单个租户的一组资源配额上限。"""

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
        self._quotas: dict[str, TenantQuota] = {}
        self._active_jobs: dict[str, int] = {}
        self._active_connections: dict[str, int] = {}

    def register_quota(
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
        self._quotas[tenant_id] = TenantQuota(
            max_concurrent_jobs=max_concurrent_jobs, max_pool_connections=max_pool_connections,
        )
        return (
            self._active_jobs.get(tenant_id, 0) > max_concurrent_jobs
            or self._active_connections.get(tenant_id, 0) > max_pool_connections
        )

    def _quota_for(self, tenant_id: str) -> TenantQuota:
        return self._quotas.get(tenant_id, TenantQuota())

    def acquire_job_slot(self, tenant_id: str) -> None:
        """占用一个任务队列名额；超出该租户配额时抛`QuotaExceededError`，
        不静默排队等待——上层应该据此决定拒绝/延迟这次提交，而不是让任务
        无声地堆积在一条全组织共享的FIFO队列里（这正是checklist E类真实
        事故里`agent_jobs`表的设计缺口）。"""
        quota = self._quota_for(tenant_id)
        current = self._active_jobs.get(tenant_id, 0)
        if current >= quota.max_concurrent_jobs:
            raise QuotaExceededError(
                f"tenant '{tenant_id}' has reached its concurrent job quota "
                f"({current}/{quota.max_concurrent_jobs})"
            )
        self._active_jobs[tenant_id] = current + 1

    def release_job_slot(self, tenant_id: str) -> None:
        self._active_jobs[tenant_id] = max(0, self._active_jobs.get(tenant_id, 0) - 1)

    def acquire_connection(self, tenant_id: str) -> None:
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
        tenant_ids = set(self._active_jobs) | set(self._active_connections)
        report: dict[str, dict[str, int | bool]] = {}
        for tenant_id in tenant_ids:
            quota = self._quota_for(tenant_id)
            active_jobs = self._active_jobs.get(tenant_id, 0)
            active_connections = self._active_connections.get(tenant_id, 0)
            report[tenant_id] = {
                "active_jobs": active_jobs,
                "active_connections": active_connections,
                "is_over_quota": (
                    active_jobs > quota.max_concurrent_jobs or active_connections > quota.max_pool_connections
                ),
            }
        return report
