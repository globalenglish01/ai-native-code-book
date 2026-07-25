"""按每轮对话复杂度打分，路由到便宜/快速模型或主力模型。

改造自真实生产项目里验证过的评分式模型路由中间件。核心机制不变：
LangChain中间件的`wrap_model_call(request, handler)`允许通过
`handler(request.override(model=...))`按次替换模型——`ModelFallbackMiddleware`
证明了这个机制，本中间件用同样的手法做"主动选择更便宜模型"而不是
"失败后被动降级"。

提取时的改动：把原版直接`from app.agents._utils.model_factory import
build_model, _prod_safe_model_id`（项目专属耦合+本地dev-shim防护逻辑）
换成统一走`ainative_core.model_factory.build_model`，不再依赖任何
项目专属的生产环境护栏分支（该护栏逻辑是TestAgentPythonProject自己的
本地开发工具防护，不属于通用框架职责）。

ch67-01教训：任何承担成本控制职责的路由机制，必须确保系统里不存在能够
绕开它的替代路径——本中间件构造"便宜模型"时统一走`build_model()`，
不裸调`init_chat_model`，避免重蹈"多处直接调用底层接口绕开护栏"的覆辙。
"""

from __future__ import annotations

import logging
import os
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, ToolMessage

from ainative_core.config import ProviderConfig
from ainative_core.model_factory import (
    STRUCTURED_AGENT_TEMPERATURE,
    build_model,
    temperature_kwargs,
)

logger = logging.getLogger(__name__)

_COMPLEX_KEYWORDS = (
    "debug", "fix", "analyze", "why", "error", "failed", "broken",
    "investigate", "root cause", "stuck", "diagnose",
)
_SIMPLE_KEYWORDS = (
    "list", "show", "check", "say", "reply", "ok", "yes", "no",
    "confirm", "status", "summarize",
)

_ANALYTICAL_TOOLS = frozenset({"browser_evaluate", "browser_console_messages", "browser_network_requests"})
_PRIMITIVE_TOOLS = frozenset({
    "browser_click", "browser_navigate", "browser_type", "browser_press_key", "browser_hover",
    "read_file", "ls", "glob", "grep", "write_file", "edit_file",
})


def _extract_text(content: Any) -> str:
    if isinstance(content, str):
        return content.lower()
    if isinstance(content, list):
        parts = [
            str(block.get("text", ""))
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        return " ".join(parts).lower()
    return ""


class ModelRouterMiddleware(AgentMiddleware):
    """按复杂度打分，把简单轮次路由到便宜模型，复杂轮次留给主力模型。

    Args:
        parent_agent_name: 仅用于日志标识，区分是哪个agent的路由决策。
        config: 供应商配置，留空则从环境变量构造。
        cheap_model_id: 便宜模型的`provider:model_id`，留空则用`config.cheap_model_id`。
        threshold: 路由阈值，score小于等于该值时路由到便宜模型。默认0，
            意味着至少需要一个"简单"信号才会被路由到便宜模型。
    """

    def __init__(
        self,
        parent_agent_name: str,
        *,
        config: ProviderConfig | None = None,
        cheap_model_id: str | None = None,
        threshold: int | None = None,
    ) -> None:
        super().__init__()
        cfg = config or ProviderConfig.from_env()
        cheap_id = cheap_model_id or cfg.cheap_model_id
        self._cheap_model: BaseChatModel = build_model(
            cheap_id,
            config=cfg,
            temperature=STRUCTURED_AGENT_TEMPERATURE,
            extra_kwargs=temperature_kwargs(cheap_id, STRUCTURED_AGENT_TEMPERATURE),
        )
        self._threshold = (
            threshold if threshold is not None
            else int(os.environ.get("AGENT_MODEL_ROUTER_THRESHOLD", "0"))
        )
        self._parent_name = parent_agent_name

    def _score(self, request: ModelRequest) -> tuple[int, dict[str, int]]:
        msgs = list(getattr(request, "messages", None) or [])
        score = 0
        signals: dict[str, int] = {}

        n = len(msgs)
        if n <= 3:
            score -= 2
            signals["history_short"] = -2
        elif n >= 10:
            score += 2
            signals["history_long"] = 2

        last = msgs[-1] if msgs else None
        if isinstance(last, ToolMessage):
            if getattr(last, "status", None) == "error":
                score += 3
                signals["last_tool_error"] = 3
            tname = last.name or ""
            if tname in _ANALYTICAL_TOOLS:
                score += 3
                signals[f"analytical_tool:{tname}"] = 3
            elif tname in _PRIMITIVE_TOOLS:
                score -= 1
                signals[f"simple_tool:{tname}"] = -1

        tool_count = sum(1 for m in msgs if isinstance(m, ToolMessage))
        if tool_count < 3:
            score -= 1
            signals["few_tool_calls"] = -1
        elif tool_count > 8:
            score += 2
            signals["many_tool_calls"] = 2

        last_human = next(
            (m for m in reversed(msgs) if isinstance(m, HumanMessage) or getattr(m, "type", None) == "human"),
            None,
        )
        if last_human is not None:
            txt = _extract_text(getattr(last_human, "content", ""))
            if any(k in txt for k in _COMPLEX_KEYWORDS):
                score += 4
                signals["complex_keywords"] = 4
            elif len(txt) < 200 and any(k in txt for k in _SIMPLE_KEYWORDS):
                score -= 2
                signals["simple_keywords"] = -2

        return score, signals

    def _route(self, request: ModelRequest) -> tuple[BaseChatModel | None, int, dict[str, int]]:
        score, signals = self._score(request)
        if score <= self._threshold:
            return self._cheap_model, score, signals
        return None, score, signals

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        override, score, signals = self._route(request)
        if override is not None:
            logger.info("[ModelRouter:%s] score=%d → cheap model. signals=%s", self._parent_name, score, signals)
            return handler(request.override(model=override))
        logger.debug("[ModelRouter:%s] score=%d → default model. signals=%s", self._parent_name, score, signals)
        return handler(request)

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        override, score, signals = self._route(request)
        if override is not None:
            logger.info("[ModelRouter:%s] score=%d → cheap model. signals=%s", self._parent_name, score, signals)
            return await handler(request.override(model=override))
        logger.debug("[ModelRouter:%s] score=%d → default model. signals=%s", self._parent_name, score, signals)
        return await handler(request)
