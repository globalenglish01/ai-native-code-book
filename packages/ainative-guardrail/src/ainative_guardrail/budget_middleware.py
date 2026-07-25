"""Agent运行期预算/节流中间件——防止单次运行成本失控。

改造自真实生产项目里被验证过的一组纯内存计数器中间件，全部继承
`langchain.agents.middleware.types.AgentMiddleware`，互相之间零耦合，
可以独立选用、任意组合。

提取时修正了原版两处已被发现但尚未修复的真实逻辑缺陷：

1. **`_count_input_tokens` 累计值/估算值混用**：原版把"从`usage_metadata`
   拿到的真实累计token数"和"没有`usage_metadata`时的字符数估算增量"强行
   放进同一个变量用`max()`比较，会被对话早期一条无元数据的大段消息永久
   虚高。本版拆成`last_known_cumulative`（最近一次真实累计值）+ 只对
   该条消息**之后**的新消息做字符数估算增量，语义上不再混淆。
2. **`ConsecutiveRetryGuardMiddleware`合成短路消息污染计数器**：原版
   `wrap_tool_call`把自己短路拦截时生成的合成`ToolMessage`（status="error"）
   又传回`_record`当作"新的一次真实失败"继续累加，导致`status()`快照
   随着AI反复尝试已经被拦截的工具无限膨胀、失真。本版短路路径不再
   调用`_record`——计数器在真正命中上限的那一刻就已经达到`_max`，
   之后的短路不需要、也不应该继续增长这个计数。

`MCPCallLimiterMiddleware`的调用计数发生在`wrap_tool_call`最外层——如果
和`HumanInTheLoopMiddleware`一起使用，需要注意LangChain中间件是按列表
顺序从外到内包裹的：把`MCPCallLimiterMiddleware`排在人工审核中间件
**之后**（更内层），才能保证"用户主动拒绝要求重试"不会被计入调用上限。
这是一个组合/排序注意事项，不是本类自身的实现缺陷。
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

logger = logging.getLogger(__name__)


class TokenCounter:
    """把"真实累计值"和"字符数估算增量"分开维护，避免语义混淆（ch10-01修复）。

    `TokenBudgetMiddleware`和`ainative_guardrail.health_monitor.GuardHealthMonitorMiddleware`
    共用同一个计数实现，避免两处各自维护一份、可能出现行为不一致的token计数逻辑。
    """

    def __init__(self) -> None:
        self._last_known_cumulative = 0
        self._estimated_increment = 0
        self._counted_message_ids: set[int] = set()

    @property
    def last_known_cumulative(self) -> int:
        """最近一次从`usage_metadata`拿到的真实累计输入token数（不含估算增量）。

        注意：这**不是**当前总计——如果自上次真实累计值之后又有新消息只经过
        了字符数估算（还没有反映在任何`usage_metadata`里），这部分不计入这个
        属性。需要"当前最佳估计总量"（与`count()`用于短路判定的口径一致）时，
        用`current_total`。
        """
        return self._last_known_cumulative

    @property
    def current_total(self) -> int:
        """当前最佳估计的总token数（真实累计值 + 尚未被真实值覆盖的估算增量）——
        与`count()`用于短路判定的口径完全一致，适合`status()`这类只读快照使用。
        """
        return self._last_known_cumulative + self._estimated_increment

    def count(self, messages: list) -> int:
        for m in messages or []:
            msg_id = id(m)
            if msg_id in self._counted_message_ids:
                continue
            meta = getattr(m, "usage_metadata", None)
            if meta and isinstance(meta, dict):
                v = meta.get("input_tokens") or meta.get("prompt_tokens")
                if isinstance(v, int):
                    # 这条消息本身就代表"截至目前的真实累计输入token数"——
                    # 之前所有靠字符数估算的增量已经被这个真实值覆盖，清零重来。
                    self._last_known_cumulative = max(self._last_known_cumulative, v)
                    self._estimated_increment = 0
                self._counted_message_ids.add(msg_id)
                continue
            self._counted_message_ids.add(msg_id)
            content = getattr(m, "content", "")
            if isinstance(content, str):
                self._estimated_increment += len(content) // 4
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict):
                        text = block.get("text") or block.get("input") or ""
                        if isinstance(text, str):
                            self._estimated_increment += len(text) // 4
        return self._last_known_cumulative + self._estimated_increment


class MCPCallLimiterMiddleware(AgentMiddleware):
    """限制某个工具在一次运行内最多被调用几次。

    Args:
        per_tool_limit: ``工具名 -> 最大调用次数``的映射，未列出的工具不受限。
        default_limit: 未列在`per_tool_limit`里的工具的兜底上限（`None`表示不限）。
    """

    def __init__(
        self,
        per_tool_limit: dict[str, int] | None = None,
        default_limit: int | None = None,
    ) -> None:
        super().__init__()
        self._per_tool_limit = per_tool_limit or {}
        self._default_limit = default_limit
        self._counters: dict[str, int] = {}

    def _bump(self, name: str) -> int:
        self._counters[name] = self._counters.get(name, 0) + 1
        return self._counters[name]

    def _limit_for(self, name: str) -> int | None:
        return self._per_tool_limit.get(name, self._default_limit)

    def status(self) -> dict[str, Any]:
        """只读状态快照：各工具 count/limit 的最大占比。"""
        worst_tool, max_ratio = "", 0.0
        for name, count in self._counters.items():
            limit = self._limit_for(name)
            if limit:
                ratio = count / limit
                if ratio > max_ratio:
                    max_ratio, worst_tool = ratio, name
        return {"counters": dict(self._counters), "max_ratio": max_ratio, "worst_tool": worst_tool}

    def _maybe_short_circuit(self, request: ToolCallRequest) -> ToolMessage | None:
        name = request.tool_call.get("name", "")
        limit = self._limit_for(name)
        if limit is None:
            return None
        count = self._bump(name)
        if count <= limit:
            return None
        logger.warning(
            "[MCPCallLimiter] %s exceeded cap %d (this call #%d) — short-circuiting",
            name, limit, count,
        )
        return ToolMessage(
            content=(
                f"[budget] Call cap of {limit} reached for tool '{name}'. "
                f"Use what you already have in this conversation; do NOT "
                f"call '{name}' again in this run."
            ),
            tool_call_id=request.tool_call.get("id", ""),
            name=name,
            status="error",
        )

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        short = self._maybe_short_circuit(request)
        return short if short is not None else handler(request)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        short = self._maybe_short_circuit(request)
        return short if short is not None else await handler(request)


class ConsecutiveRetryGuardMiddleware(AgentMiddleware):
    """同一个工具连续失败达到上限后短路拦截，逼停无效重试循环。

    Args:
        max_consecutive_errors: 同一工具连续失败几次后触发短路。
    """

    def __init__(self, max_consecutive_errors: int = 2) -> None:
        super().__init__()
        self._max = max_consecutive_errors
        self._errors: dict[str, int] = {}
        self._last_tool: str = ""

    def status(self) -> dict[str, Any]:
        """只读状态快照：最严重工具的 errors/max 占比。"""
        worst_tool, worst = "", 0
        for name, cnt in self._errors.items():
            if cnt > worst:
                worst, worst_tool = cnt, name
        return {
            "errors": dict(self._errors),
            "max": self._max,
            "max_ratio": (worst / self._max if self._max else 0.0),
            "worst_tool": worst_tool,
        }

    def _short_circuit_message(self, request: ToolCallRequest, count: int) -> ToolMessage:
        name = request.tool_call.get("name", "")
        return ToolMessage(
            content=(
                f"[budget] '{name}' has failed {count} times in a row. "
                f"Stop retrying with variations of the same args. "
                f"Either try a different approach, or finalize the run."
            ),
            tool_call_id=request.tool_call.get("id", ""),
            name=name,
            status="error",
        )

    def _record_real_result(self, request: ToolCallRequest, result: Any) -> None:
        """只记录真正执行过的调用结果——短路生成的合成消息不应传入这里（ch12-01修复）。"""
        name = request.tool_call.get("name", "")
        if not name:
            return
        if self._last_tool and self._last_tool != name and self._last_tool in self._errors:
            self._errors[self._last_tool] = 0
        self._last_tool = name
        status = getattr(result, "status", None) if isinstance(result, ToolMessage) else None
        if status == "error":
            self._errors[name] = self._errors.get(name, 0) + 1
        elif status == "success":
            self._errors[name] = 0

    def _maybe_short_circuit(self, request: ToolCallRequest) -> ToolMessage | None:
        name = request.tool_call.get("name", "")
        if not name:
            return None
        count = self._errors.get(name, 0)
        if count < self._max:
            return None
        logger.warning("[RetryGuard] short-circuiting %s (errors=%d max=%d)", name, count, self._max)
        return self._short_circuit_message(request, count)

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        short = self._maybe_short_circuit(request)
        if short is not None:
            return short
        result = handler(request)
        self._record_real_result(request, result)
        return result

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        short = self._maybe_short_circuit(request)
        if short is not None:
            return short
        result = await handler(request)
        self._record_real_result(request, result)
        return result


class ConsecutiveCallGuardMiddleware(AgentMiddleware):
    """连续多次"停滞类"工具调用（只读探索、无实际进展）后短路拦截。

    Args:
        stall_tools: 视为"停滞/探索"的工具名集合。
        progress_tools: 视为"确实在推进任务"的工具名集合，出现即清零停滞计数。
        max_stall_calls: 连续停滞调用超过几次后触发短路。
    """

    DEFAULT_STALL: frozenset[str] = frozenset({"ls", "glob", "read_file", "grep"})
    DEFAULT_PROGRESS: frozenset[str] = frozenset(
        {"write_file", "edit_file", "run_tests"}
    )

    def __init__(
        self,
        *,
        stall_tools: frozenset[str] | None = None,
        progress_tools: frozenset[str] | None = None,
        max_stall_calls: int = 3,
    ) -> None:
        super().__init__()
        self._stall = stall_tools if stall_tools is not None else self.DEFAULT_STALL
        self._progress = progress_tools if progress_tools is not None else self.DEFAULT_PROGRESS
        self._max = max_stall_calls
        self._stall_count = 0

    def status(self) -> dict[str, Any]:
        return {
            "stall_count": self._stall_count,
            "max": self._max,
            "ratio": (self._stall_count / self._max if self._max else 0.0),
        }

    def _evaluate(self, request: ToolCallRequest) -> ToolMessage | None:
        name = request.tool_call.get("name", "")
        if name in self._progress:
            self._stall_count = 0
            return None
        if name in self._stall:
            self._stall_count += 1
            count = self._stall_count
            if count > self._max:
                logger.warning(
                    "[CallGuard] %d consecutive stall calls (last: %s) — short-circuiting",
                    count, name,
                )
                stall_names = ", ".join(sorted(self._stall))
                return ToolMessage(
                    content=(
                        f"[budget] You have called exploration tools "
                        f"({stall_names}) {count} times in a row without making "
                        f"progress. Stop exploring and take a concrete action, "
                        f"or produce a final summary of what you found."
                    ),
                    tool_call_id=request.tool_call.get("id", ""),
                    name=name,
                    status="error",
                )
        return None

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        short = self._evaluate(request)
        return short if short is not None else handler(request)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        short = self._evaluate(request)
        return short if short is not None else await handler(request)


class TokenBudgetMiddleware(AgentMiddleware):
    """累计输入token超过预算后，短路整个模型调用循环。

    Args:
        max_total_input_tokens: 单次运行的输入token预算上限。
    """

    def __init__(self, max_total_input_tokens: int = 200_000) -> None:
        super().__init__()
        self._budget = max_total_input_tokens
        self._counter = TokenCounter()

    def status(self) -> dict[str, Any]:
        spent = self._counter.current_total
        return {"spent": spent, "budget": self._budget, "ratio": (spent / self._budget if self._budget else 0.0)}

    def _check(self, request: ModelRequest) -> ModelResponse | None:
        messages = getattr(request, "messages", None) or []
        spent = self._counter.count(list(messages))
        if spent < self._budget:
            return None
        logger.warning("[TokenBudget] spent=%d budget=%d — short-circuiting", spent, self._budget)
        return ModelResponse(
            result=[
                AIMessage(
                    content=(
                        f"[budget] Token budget exhausted "
                        f"({spent}/{self._budget} input tokens). "
                        f"Stopping agent run. Please open a new conversation "
                        f"if more work is needed."
                    )
                )
            ],
            structured_response=None,
        )

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        short = self._check(request)
        return short if short is not None else handler(request)

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        short = self._check(request)
        return short if short is not None else await handler(request)
