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
    ) -> None:
        self._quotas[tenant_id] = TenantQuota(
            max_concurrent_jobs=max_concurrent_jobs, max_pool_connections=max_pool_connections,
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

    def usage_report(self) -> dict[str, dict[str, int]]:
        """所有当前有活跃占用的租户的资源使用快照——供运维/仪表盘查看
        "有没有某个租户不成比例占用共享资源"。"""
        tenant_ids = set(self._active_jobs) | set(self._active_connections)
        return {
            tenant_id: {
                "active_jobs": self._active_jobs.get(tenant_id, 0),
                "active_connections": self._active_connections.get(tenant_id, 0),
            }
            for tenant_id in tenant_ids
        }
