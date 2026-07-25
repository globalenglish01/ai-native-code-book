"""把一次LLM调用的用量记录到`UsageSink`——通过LangChain回调机制自动采集。

改造自真实项目里验证过的"usage tracker"设计：不需要调用方在每次
`model.invoke(...)`之后手动读取结果、拼装事件、调用sink——只需要在
构造模型时传入`usage_sink`，`build_model`/`build_agent_model`会自动
挂上一个回调处理器，每次调用结束后自动提取token用量并记录。

**关键实现约束**：通过`init_chat_model(model_id, callbacks=[handler], ...)`
在构造时把回调传给底层模型，而不是用`model.with_config(callbacks=[...])`——
后者返回的是`RunnableBinding`，不是`BaseChatModel`的子类，会破坏
`ModelFallbackMiddleware`依赖的"primary必须是货真价实的BaseChatModel"这个
类型不变量（ch53-01教训的同一类问题）。
"""

from __future__ import annotations

import time
from typing import Any
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult

from ainative_core.protocols import UsageSink


def _extract_usage(response: LLMResult) -> tuple[int, int]:
    """从`LLMResult`里提取(input_tokens, output_tokens)，缺失时都记为0。"""
    input_tokens = 0
    output_tokens = 0
    for generation_list in response.generations:
        for generation in generation_list:
            message = getattr(generation, "message", None)
            usage = getattr(message, "usage_metadata", None) if message is not None else None
            if isinstance(usage, dict):
                input_tokens += usage.get("input_tokens") or 0
                output_tokens += usage.get("output_tokens") or 0
    return input_tokens, output_tokens


def _extract_model_name(response: LLMResult) -> str | None:
    """尽力从`LLMResult.llm_output`里取出具体模型名（不同供应商字段名不一致，
    找不到就返回`None`，由调用方决定fallback成什么）。"""
    llm_output = response.llm_output or {}
    for key in ("model_name", "model"):
        value = llm_output.get(key)
        if isinstance(value, str) and value:
            return value
    return None


class UsageTrackingCallbackHandler(BaseCallbackHandler):
    """每次LLM调用结束后，把用量事件写进注入的`UsageSink`。

    Args:
        sink: 用量记录的目标。
        agent_name: 记入事件的agent标识（哪个agent发起的这次调用）。
        provider: 记入事件的供应商标识（比如"anthropic"）。
        model_id: 记入事件的model_id（比如"anthropic:claude-sonnet-4-5"）——
            优先于从`LLMResult`里尝试提取到的模型名，因为调用方在构造时
            已经明确知道自己传的是哪个model_id，不需要依赖不同供应商
            response里字段名不一致的猜测。
    """

    def __init__(self, sink: UsageSink, *, agent_name: str, provider: str, model_id: str) -> None:
        super().__init__()
        self._sink = sink
        self._agent_name = agent_name
        self._provider = provider
        self._model_id = model_id

    def on_llm_end(self, response: LLMResult, *, run_id: UUID, **kwargs: Any) -> None:
        input_tokens, output_tokens = _extract_usage(response)
        self._sink.record({
            "agent_name": self._agent_name,
            "provider": self._provider,
            "model": _extract_model_name(response) or self._model_id,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "timestamp": time.time(),
        })
