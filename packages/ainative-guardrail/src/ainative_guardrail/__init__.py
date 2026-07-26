"""ainative-guardrail —— Agent运行期护栏：模型路由、预算/连续调用限制、健康监控。"""

from __future__ import annotations

from ainative_guardrail.backpressure import QueueBacklogMonitor, RateLimitedConsumer
from ainative_guardrail.budget_middleware import (
    ConsecutiveCallGuardMiddleware,
    ConsecutiveRetryGuardMiddleware,
    MCPCallLimiterMiddleware,
    TokenBudgetMiddleware,
    TokenCounter,
)
from ainative_guardrail.health_monitor import GuardHealthMonitorMiddleware
from ainative_guardrail.idempotency import (
    DEFAULT_TTL_SECONDS,
    DuplicateOperationError,
    IdempotencyRecord,
    IdempotencyStatus,
    InMemoryIdempotencyStore,
    idempotent_operation,
)
from ainative_guardrail.limits import AgentLimit, AgentLimits
from ainative_guardrail.model_router import ModelRouterMiddleware

__version__ = "0.1.0"

__all__ = [
    "DEFAULT_TTL_SECONDS",
    "AgentLimit",
    "AgentLimits",
    "ConsecutiveCallGuardMiddleware",
    "ConsecutiveRetryGuardMiddleware",
    "DuplicateOperationError",
    "GuardHealthMonitorMiddleware",
    "IdempotencyRecord",
    "IdempotencyStatus",
    "InMemoryIdempotencyStore",
    "MCPCallLimiterMiddleware",
    "ModelRouterMiddleware",
    "QueueBacklogMonitor",
    "RateLimitedConsumer",
    "TokenBudgetMiddleware",
    "TokenCounter",
    "idempotent_operation",
]
