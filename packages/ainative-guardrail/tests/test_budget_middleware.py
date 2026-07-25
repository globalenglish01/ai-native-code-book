from __future__ import annotations

import pytest
from ainative_guardrail.budget_middleware import (
    ConsecutiveCallGuardMiddleware,
    ConsecutiveRetryGuardMiddleware,
    MCPCallLimiterMiddleware,
    TokenBudgetMiddleware,
    TokenCounter,
)
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest


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


@pytest.mark.asyncio
async def test_mcp_call_limiter_awrap_tool_call_short_circuits_after_cap():
    mw = MCPCallLimiterMiddleware(per_tool_limit={"search": 1})

    async def handler(request):
        return ToolMessage(content="ok", tool_call_id="call-1", name="search", status="success")

    first = await mw.awrap_tool_call(_tool_request("search"), handler)
    assert first.status == "success"
    second = await mw.awrap_tool_call(_tool_request("search"), handler)
    assert second.status == "error"


def test_mcp_call_limiter_status_reports_worst_tool_and_ratio():
    mw = MCPCallLimiterMiddleware(per_tool_limit={"search": 4})

    def handler(request):
        return ToolMessage(content="ok", tool_call_id="call-1", name="search", status="success")

    for _ in range(2):
        mw.wrap_tool_call(_tool_request("search"), handler)

    status = mw.status()
    assert status["counters"]["search"] == 2
    assert status["max_ratio"] == 0.5
    assert status["worst_tool"] == "search"


def test_mcp_call_limiter_status_with_zero_limit_reports_infinite_ratio():
    """Regression test: a tool configured with limit=0 (never allowed) was
    silently excluded from status()'s ratio computation before the fix
    (status used `if limit:` instead of `if limit is not None:`, treating 0
    the same as "unlimited"), even though the tool call was correctly
    blocked. status() must reflect that this tool is maximally over budget."""
    mw = MCPCallLimiterMiddleware(per_tool_limit={"dangerous_tool": 0})
    mw.wrap_tool_call(_tool_request("dangerous_tool"), lambda r: pytest.fail("should never execute"))

    status = mw.status()
    assert status["worst_tool"] == "dangerous_tool"
    assert status["max_ratio"] == float("inf")


def test_mcp_call_limiter_status_with_no_calls_yet():
    mw = MCPCallLimiterMiddleware(per_tool_limit={"search": 4})
    status = mw.status()
    assert status == {"counters": {}, "max_ratio": 0.0, "worst_tool": ""}


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


def test_consecutive_retry_guard_clears_previous_tools_streak_on_tool_switch():
    mw = ConsecutiveRetryGuardMiddleware(max_consecutive_errors=5)

    def failing_click(request):
        return ToolMessage(content="boom", tool_call_id="call-1", name="click", status="error")

    def success_type(request):
        return ToolMessage(content="ok", tool_call_id="call-1", name="type", status="success")

    mw.wrap_tool_call(_tool_request("click"), failing_click)
    assert mw.status()["errors"]["click"] == 1

    # Switching to a different tool clears the previous tool's streak, even
    # though this new tool call itself succeeded (a different code path than
    # an explicit success on the *same* tool).
    mw.wrap_tool_call(_tool_request("type"), success_type)
    assert mw.status()["errors"]["click"] == 0


def test_consecutive_retry_guard_ignores_requests_with_empty_tool_name():
    mw = ConsecutiveRetryGuardMiddleware(max_consecutive_errors=1)
    empty_name_request = _tool_request("")

    def handler(request):
        return ToolMessage(content="ok", tool_call_id="call-1", name="", status="error")

    # Must never short-circuit or crash on a request with an empty tool name —
    # both _maybe_short_circuit and _record_real_result guard on `if not name`.
    result = mw.wrap_tool_call(empty_name_request, handler)
    assert result.status == "error"  # passed straight through to the real handler
    assert mw.status()["errors"] == {}


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


def test_token_counter_current_total_matches_count_result():
    counter = TokenCounter()
    counter.count([HumanMessage(content="x" * 400)])
    assert counter.current_total == 100
    assert counter.last_known_cumulative == 0  # no usage_metadata seen yet


def test_token_counter_current_total_after_real_cumulative_seen():
    counter = TokenCounter()
    counter.count([AIMessage(content="reply", usage_metadata={"input_tokens": 500, "output_tokens": 1, "total_tokens": 501})])
    assert counter.current_total == 500
    assert counter.last_known_cumulative == 500


def test_token_counter_estimates_tokens_from_list_content_blocks():
    """Anthropic-style content blocks: a list of dicts with "text"/"input" keys,
    rather than a plain string — must still contribute to the estimate."""
    counter = TokenCounter()
    message = HumanMessage(content=[{"type": "text", "text": "x" * 400}])
    assert counter.count([message]) == 100


def test_token_counter_list_content_ignores_non_dict_blocks():
    counter = TokenCounter()
    message = HumanMessage(content=["not a dict block", {"type": "text", "text": "x" * 40}])
    assert counter.count([message]) == 10


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


def test_token_budget_middleware_status_reflects_estimated_increment_not_just_real_cumulative():
    """Regression test: status() must report the same total _check() uses to
    decide whether to short-circuit — before the fix, status() only reported
    TokenCounter.last_known_cumulative (the real usage_metadata-derived value,
    0 until an AIMessage with usage_metadata is seen), silently dropping the
    character-count-estimated portion and under-reporting actual usage."""
    mw = TokenBudgetMiddleware(max_total_input_tokens=1000)

    class _FakeRequest:
        messages = [HumanMessage(content="x" * 400)]  # no usage_metadata -> 100 estimated tokens

    mw.wrap_model_call(_FakeRequest(), lambda r: "handled")
    assert mw.status()["spent"] == 100


@pytest.mark.asyncio
async def test_consecutive_retry_guard_awrap_tool_call_short_circuits():
    mw = ConsecutiveRetryGuardMiddleware(max_consecutive_errors=2)

    async def failing_handler(request):
        return ToolMessage(content="boom", tool_call_id="call-1", name="click", status="error")

    await mw.awrap_tool_call(_tool_request("click"), failing_handler)
    await mw.awrap_tool_call(_tool_request("click"), failing_handler)

    calls = []

    async def tracking_handler(request):
        calls.append(request)
        return ToolMessage(content="boom", tool_call_id="call-1", name="click", status="error")

    result = await mw.awrap_tool_call(_tool_request("click"), tracking_handler)
    assert result.status == "error"
    assert calls == []  # short-circuited before reaching the real handler


@pytest.mark.asyncio
async def test_consecutive_call_guard_awrap_tool_call_short_circuits_on_stall_streak():
    mw = ConsecutiveCallGuardMiddleware(max_stall_calls=2)

    async def handler(request):
        return ToolMessage(content="listing", tool_call_id="call-1", name="ls", status="success")

    await mw.awrap_tool_call(_tool_request("ls"), handler)
    await mw.awrap_tool_call(_tool_request("ls"), handler)
    result = await mw.awrap_tool_call(_tool_request("ls"), handler)
    assert result.status == "error"


@pytest.mark.asyncio
async def test_token_budget_middleware_awrap_model_call_short_circuits_when_exhausted():
    mw = TokenBudgetMiddleware(max_total_input_tokens=100)

    class _FakeRequest:
        messages = [AIMessage(content="x" * 800)]

    async def handler(r):
        pytest.fail("should not reach the real handler once budget is exhausted")

    response = await mw.awrap_model_call(_FakeRequest(), handler)
    assert "budget" in response.result[0].content.lower()


@pytest.mark.asyncio
async def test_token_budget_middleware_awrap_model_call_passes_through_when_under_budget():
    mw = TokenBudgetMiddleware(max_total_input_tokens=1_000_000)

    class _FakeRequest:
        messages = [AIMessage(content="short")]

    async def handler(r):
        return "handled"

    response = await mw.awrap_model_call(_FakeRequest(), handler)
    assert response == "handled"
