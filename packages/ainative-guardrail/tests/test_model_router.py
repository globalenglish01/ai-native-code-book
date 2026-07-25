from __future__ import annotations

import pytest
from ainative_core.config import ProviderConfig
from ainative_guardrail.model_router import ModelRouterMiddleware, _extract_text
from langchain_core.messages import HumanMessage, ToolMessage


class _FakeRequest:
    def __init__(self, messages):
        self.messages = messages
        self._overridden_model = None

    def override(self, model):
        self._overridden_model = model
        return self


def test_short_history_and_simple_keyword_routes_to_cheap_model():
    mw = ModelRouterMiddleware("test_agent", config=ProviderConfig(anthropic_api_key="test-key"))
    request = _FakeRequest([HumanMessage(content="show me the status")])

    result_holder = {}

    def handler(req):
        result_holder["model"] = getattr(req, "_overridden_model", None)
        return "response"

    mw.wrap_model_call(request, handler)
    assert result_holder["model"] is mw._cheap_model


def test_complex_keyword_routes_to_default_model():
    mw = ModelRouterMiddleware("test_agent", config=ProviderConfig(anthropic_api_key="test-key"))
    messages = [HumanMessage(content="please debug and analyze why this failed")] * 1
    request = _FakeRequest(messages)

    result_holder = {}

    def handler(req):
        result_holder["model"] = getattr(req, "_overridden_model", None)
        return "response"

    mw.wrap_model_call(request, handler)
    assert result_holder["model"] is None


def test_extract_text_flattens_content_block_list():
    content = [{"type": "text", "text": "Hello"}, {"type": "image", "url": "x"}, {"type": "text", "text": "World"}]
    assert _extract_text(content) == "hello world"


def test_extract_text_returns_empty_string_for_unsupported_type():
    assert _extract_text(12345) == ""


def test_long_history_biases_toward_default_model():
    mw = ModelRouterMiddleware("test_agent", config=ProviderConfig(anthropic_api_key="test-key"))
    # 10+ messages triggers the "history_long" +2 signal.
    messages = [HumanMessage(content="ok")] * 10
    _score, signals = mw._score(_FakeRequest(messages))
    assert signals.get("history_long") == 2


def test_primitive_tool_biases_toward_cheap_model():
    mw = ModelRouterMiddleware("test_agent", config=ProviderConfig(anthropic_api_key="test-key"))
    messages = [ToolMessage(content="clicked", tool_call_id="1", name="browser_click", status="success")]
    _score, signals = mw._score(_FakeRequest(messages))
    assert signals.get("simple_tool:browser_click") == -1


def test_many_tool_calls_biases_toward_default_model():
    mw = ModelRouterMiddleware("test_agent", config=ProviderConfig(anthropic_api_key="test-key"))
    messages = [ToolMessage(content="x", tool_call_id=str(i), name="t", status="success") for i in range(9)]
    _score, signals = mw._score(_FakeRequest(messages))
    assert signals.get("many_tool_calls") == 2


@pytest.mark.asyncio
async def test_awrap_model_call_routes_to_cheap_model_for_simple_turn():
    mw = ModelRouterMiddleware("test_agent", config=ProviderConfig(anthropic_api_key="test-key"))
    request = _FakeRequest([HumanMessage(content="show me the status")])

    result_holder = {}

    async def handler(req):
        result_holder["model"] = getattr(req, "_overridden_model", None)
        return "response"

    await mw.awrap_model_call(request, handler)
    assert result_holder["model"] is mw._cheap_model


@pytest.mark.asyncio
async def test_awrap_model_call_uses_default_model_for_complex_turn():
    mw = ModelRouterMiddleware("test_agent", config=ProviderConfig(anthropic_api_key="test-key"))
    request = _FakeRequest([HumanMessage(content="please debug and analyze why this failed")])

    result_holder = {}

    async def handler(req):
        result_holder["model"] = getattr(req, "_overridden_model", None)
        return "response"

    await mw.awrap_model_call(request, handler)
    assert result_holder["model"] is None


def test_last_tool_error_biases_toward_default_model():
    mw = ModelRouterMiddleware("test_agent", config=ProviderConfig(anthropic_api_key="test-key"), threshold=-10)
    messages = [
        ToolMessage(content="boom", tool_call_id="1", name="browser_evaluate", status="error"),
    ]
    request = _FakeRequest(messages)

    result_holder = {}

    def handler(req):
        result_holder["model"] = getattr(req, "_overridden_model", None)
        return "response"

    mw.wrap_model_call(request, handler)
    assert result_holder["model"] is None
