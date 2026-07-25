"""综合健康度预警——多层护栏各自独立触发终止阈值，但缺乏"任务是否正在滑向
失控"的综合视角。

改造自真实项目里验证过的设计：在每轮model call时计算「递归步数/recursion_limit」
「累计输入token/token预算」相对各自上限的占比，当**两个及以上维度**同时超过
可配置比例（默认0.7）时打一条结构化WARNING，供运维提前关注。

设计约束（保持原版定位不变）：
- 只监控预警，不改变任何护栏的终止判断/阈值——纯旁路观测。
- 每个run最多告警一次（去抖），避免刷屏。
- 递归步数用"本中间件被调用次数"近似，不读取框架内部计数，保持零耦合。

复用`budget_middleware.TokenCounter`（ch10-01已修复累计值/估算值混用问题），
而不是重新实现一份token计数逻辑。
"""

from __future__ import annotations

import logging
import os
from collections.abc import Awaitable, Callable

from langchain.agents.middleware.types import AgentMiddleware, ModelRequest, ModelResponse

from ainative_guardrail.budget_middleware import TokenCounter

logger = logging.getLogger(__name__)


def _default_warn_ratio() -> float:
    """综合健康度预警阈值，可用 AINATIVE_GUARD_HEALTH_WARN_RATIO 覆盖，默认0.7。"""
    try:
        v = float(os.environ.get("AINATIVE_GUARD_HEALTH_WARN_RATIO", "0.7"))
        return v if 0.0 < v <= 1.0 else 0.7
    except (TypeError, ValueError):
        return 0.7


class GuardHealthMonitorMiddleware(AgentMiddleware):
    """当多个护栏维度同时接近上限时提前告警，纯旁路观测不改变任何终止判断。

    Args:
        recursion_limit: 本次运行的递归步数上限（用于计算占比，不强制终止）。
        token_budget: 本次运行的输入token预算上限（同上）。
        warn_ratio: 单一维度视为"接近上限"的占比阈值，默认0.7。
    """

    def __init__(
        self,
        *,
        recursion_limit: int,
        token_budget: int,
        warn_ratio: float | None = None,
    ) -> None:
        super().__init__()
        self._recursion_limit = max(1, int(recursion_limit))
        self._token_budget = max(1, int(token_budget))
        self._warn_ratio = warn_ratio if warn_ratio is not None else _default_warn_ratio()
        self._steps = 0
        self._warned = False
        self._counter = TokenCounter()

    def _evaluate(self, request: ModelRequest) -> dict | None:
        self._steps += 1
        messages = getattr(request, "messages", None) or []
        tokens = self._counter.count(list(messages))
        dims = {
            "recursion": (self._steps, self._recursion_limit, self._steps / self._recursion_limit),
            "tokens": (tokens, self._token_budget, tokens / self._token_budget),
        }
        hot = {k: v for k, v in dims.items() if v[2] >= self._warn_ratio}
        if len(hot) >= 2 and not self._warned:
            self._warned = True
            desc = "，".join(f"{k} {cur}/{lim}（{ratio:.0%}）" for k, (cur, lim, ratio) in dims.items())
            logger.warning(
                "[GuardHealth] 任务接近多重护栏边界（%d 个维度超过 %.0f%%）：%s",
                len(hot), self._warn_ratio * 100, desc,
                extra={
                    "guard_health_event": "multi_guard_near_limit",
                    "hot_dimensions": list(hot.keys()),
                    "warn_ratio": self._warn_ratio,
                    "dimensions": {k: {"current": c, "limit": l, "ratio": r} for k, (c, l, r) in dims.items()},
                },
            )
            return dims
        return None

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        self._evaluate(request)
        return handler(request)

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        self._evaluate(request)
        return await handler(request)
