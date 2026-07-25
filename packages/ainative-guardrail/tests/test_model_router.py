from __future__ import annotations

from ainative_core.config import ProviderConfig
from ainative_guardrail.model_router import ModelRouterMiddleware
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
