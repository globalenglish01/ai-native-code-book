from __future__ import annotations

import asyncio

from ainative_guardrail.budget_middleware import (
    ConsecutiveCallGuardMiddleware,
    ConsecutiveRetryGuardMiddleware,
    MCPCallLimiterMiddleware,
    TokenBudgetMiddleware,
    TokenCounter,
)
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langchain_core.messages import ToolMessage


def _tool_request(name: str, call_id: str = "call-1") -> ToolCallRequest:
    return ToolCallRequest(
        tool_call={"name": name, "args": {}, "id": call_id},
        tool=None,
        state={},
        runtime=None,
    )


def test_mcp_call_limiter_short_circuits_after_cap():
    mw = MCPCallLimiterMiddleware(per_tool_limit={"search": 2})
    handler_calls = []

    def handler(request):
        handler_calls.append(request)
        return ToolMessage(content="ok", tool_call_id="call-1", name="search", status="success")

    for _ in range(2):
        result = mw.wrap_tool_call(_tool_request("search"), handler)
        assert result.status == "success"

    result = mw.wrap_tool_call(_tool_request("search"), handler)
    assert result.status == "error"
    assert len(handler_calls) == 2


def test_mcp_call_limiter_unlimited_tool_passes_through():
    mw = MCPCallLimiterMiddleware(per_tool_limit={"search": 1})

    def handler(request):
        return ToolMessage(content="ok", tool_call_id="call-1", name="other", status="success")

    for _ in range(5):
        result = mw.wrap_tool_call(_tool_request("other"), handler)
        assert result.status == "success"


def test_consecutive_retry_guard_short_circuits_after_max_errors():
    mw = ConsecutiveRetryGuardMiddleware(max_consecutive_errors=2)

    def failing_handler(request):
        return ToolMessage(content="boom", tool_call_id="call-1", name="click", status="error")

    r1 = mw.wrap_tool_call(_tool_request("click"), failing_handler)
    assert r1.status == "error"
    r2 = mw.wrap_tool_call(_tool_request("click"), failing_handler)
    assert r2.status == "error"

    # Third call onward should be short-circuited without invoking the real handler.
    calls = []

    def tracking_handler(request):
        calls.append(request)
        return ToolMessage(content="boom", tool_call_id="call-1", name="click", status="error")

    mw.wrap_tool_call(_tool_request("click"), tracking_handler)
    assert calls == []


def test_consecutive_retry_guard_does_not_inflate_counter_on_short_circuit():
    """ch12-01修复验证：短路产生的合成消息不应继续累加errors计数器。"""
    mw = ConsecutiveRetryGuardMiddleware(max_consecutive_errors=2)

    def failing_handler(request):
        return ToolMessage(content="boom", tool_call_id="call-1", name="click", status="error")

    mw.wrap_tool_call(_tool_request("click"), failing_handler)
    mw.wrap_tool_call(_tool_request("click"), failing_handler)
    assert mw.status()["errors"]["click"] == 2

    for _ in range(10):
        mw.wrap_tool_call(_tool_request("click"), failing_handler)

    assert mw.status()["errors"]["click"] == 2


def test_consecutive_retry_guard_clears_on_success():
    mw = ConsecutiveRetryGuardMiddleware(max_consecutive_errors=2)

    def failing_handler(request):
        return ToolMessage(content="boom", tool_call_id="call-1", name="click", status="error")

    def success_handler(request):
        return ToolMessage(content="ok", tool_call_id="call-1", name="click", status="success")

    mw.wrap_tool_call(_tool_request("click"), failing_handler)
    mw.wrap_tool_call(_tool_request("click"), success_handler)
    assert mw.status()["errors"]["click"] == 0


def test_consecutive_call_guard_short_circuits_on_stall_streak():
    mw = ConsecutiveCallGuardMiddleware(max_stall_calls=3)

    def handler(request):
        return ToolMessage(content="listing", tool_call_id="call-1", name="ls", status="success")

    for _ in range(3):
        result = mw.wrap_tool_call(_tool_request("ls"), handler)
        assert result.status == "success"

    result = mw.wrap_tool_call(_tool_request("ls"), handler)
    assert result.status == "error"


def test_consecutive_call_guard_resets_on_progress_tool():
    mw = ConsecutiveCallGuardMiddleware(max_stall_calls=2)

    def stall_handler(request):
        return ToolMessage(content="listing", tool_call_id="call-1", name="ls", status="success")

    def progress_handler(request):
        return ToolMessage(content="written", tool_call_id="call-1", name="write_file", status="success")

    mw.wrap_tool_call(_tool_request("ls"), stall_handler)
    mw.wrap_tool_call(_tool_request("ls"), stall_handler)
    mw.wrap_tool_call(_tool_request("write_file"), progress_handler)
    assert mw.status()["stall_count"] == 0


def test_token_counter_does_not_inflate_from_early_unmetered_message():
    """ch10-01修复验证：早期无usage_metadata的大段消息不应永久虚高后续真实累计值。"""
    counter = TokenCounter()
    huge_early_message = HumanMessage(content="huge early tool dump " * 2000)
    real_ai_message = AIMessage(content="ai reply", usage_metadata={"input_tokens": 3000, "output_tokens": 10, "total_tokens": 3010})

    result = counter.count([huge_early_message, real_ai_message])
    assert result == 3000


def test_token_budget_middleware_short_circuits_when_exhausted():
    mw = TokenBudgetMiddleware(max_total_input_tokens=100)

    class _FakeRequest:
        messages = [AIMessage(content="x" * 800)]

    response = mw.wrap_model_call(_FakeRequest(), lambda r: "should not reach here")
    assert "budget" in response.result[0].content.lower()


def test_token_budget_middleware_passes_through_when_under_budget():
    mw = TokenBudgetMiddleware(max_total_input_tokens=1_000_000)

    class _FakeRequest:
        messages = [AIMessage(content="short")]

    response = mw.wrap_model_call(_FakeRequest(), lambda r: "handled")
    assert response == "handled"
