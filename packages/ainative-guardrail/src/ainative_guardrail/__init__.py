"""ainative-guardrail —— Agent运行期护栏：模型路由、预算/连续调用限制、健康监控。"""

from __future__ import annotations

# 下面这一长串`from 模块 import 名字`，是把本包内每个文件（backpressure.py、
# budget_middleware.py等）里定义好的类/函数，重新"搬"到`ainative_guardrail`
# 这个包的顶层——这样别的项目使用本包时，既可以写精确到具体文件的
# `from ainative_guardrail.budget_middleware import TokenBudgetMiddleware`，
# 也可以更省事地直接写`from ainative_guardrail import TokenBudgetMiddleware`，
# 不需要关心这个类具体是在哪个内部文件里实现的。
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

# `__version__` 是Python社区的一个通用约定——一个包如果定义了这个变量，
# 别的代码就能通过 `ainative_guardrail.__version__` 读到"这是第几个
# 版本"，不需要单独去解析pyproject.toml文件。
__version__ = "0.1.0"

# `__all__` 是Python的另一个特殊约定变量——它是一个字符串列表，明确
# 声明"当别的代码写`from ainative_guardrail import *`（导入这个包里
# 所有公开内容）时，具体应该导入哪些名字"。这样可以精确控制这个包
# "对外公开的接口清单"是什么，即使包内部还导入/定义了别的没有列在
# 这里的东西，也不会被`import *`意外带出去。这里的列表是按字母顺序
# 排列的（大写字母在ASCII顺序里排在小写字母之前，所以`idempotent_
# operation`这个小写开头的函数名排在最后）。
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
