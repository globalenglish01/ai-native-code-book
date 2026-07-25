from __future__ import annotations

from products.browser_task_agent import BrowserTaskAgent


def test_calls_within_limits_succeed():
    agent = BrowserTaskAgent()
    for _ in range(3):
        result = agent.call_tool("browser_snapshot")
        assert result.status == "success"


def test_stall_guard_short_circuits_after_max_consecutive_snapshots():
    agent = BrowserTaskAgent()
    for _ in range(3):
        assert agent.call_tool("browser_snapshot").status == "success"
    blocked = agent.call_tool("browser_snapshot")
    assert blocked.status == "error"
    assert "without making progress" in blocked.content


def test_progress_tool_resets_stall_counter():
    agent = BrowserTaskAgent()
    for _ in range(3):
        agent.call_tool("browser_snapshot")
    agent.call_tool("browser_click")  # progress tool resets the stall streak
    result = agent.call_tool("browser_snapshot")
    assert result.status == "success"


def test_call_limiter_blocks_after_per_tool_cap():
    agent = BrowserTaskAgent()
    for _ in range(5):
        agent.call_tool("browser_navigate")
    blocked = agent.call_tool("browser_navigate")
    assert blocked.status == "error"
    assert "Call cap" in blocked.content


def test_audit_log_records_every_call_including_blocked_ones():
    agent = BrowserTaskAgent()
    for _ in range(5):
        agent.call_tool("browser_snapshot")
    assert len(agent.audit_log.for_tool("browser_snapshot")) == 5


def test_audit_log_error_rate_reflects_blocked_calls():
    agent = BrowserTaskAgent()
    for _ in range(5):
        agent.call_tool("browser_snapshot")
    # 3 succeed (within stall limit), 2 get blocked by ConsecutiveCallGuardMiddleware.
    assert agent.audit_log.error_rate("browser_snapshot") == 2 / 5


def test_mcp_config_uses_stdio_transport_with_safe_env():
    agent = BrowserTaskAgent()
    entry = agent.mcp_config["browser"]
    assert entry["transport"] == "stdio"
    assert "command" in entry
    assert "env" in entry
