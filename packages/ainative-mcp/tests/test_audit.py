from __future__ import annotations

import pytest

from ainative_mcp.audit import InMemoryToolCallAuditLog, ToolCallRecord


def test_record_and_retrieve_all():
    log = InMemoryToolCallAuditLog()
    log.record(ToolCallRecord(tool_name="search", agent_name="a1", status="success", duration_ms=12.0))
    log.record(ToolCallRecord(tool_name="search", agent_name="a1", status="error", duration_ms=8.0))
    assert len(log.all()) == 2


def test_for_tool_filters_by_name():
    log = InMemoryToolCallAuditLog()
    log.record(ToolCallRecord(tool_name="search", agent_name="a1", status="success", duration_ms=1.0))
    log.record(ToolCallRecord(tool_name="other", agent_name="a1", status="success", duration_ms=1.0))
    assert len(log.for_tool("search")) == 1


def test_error_rate_for_specific_tool():
    log = InMemoryToolCallAuditLog()
    log.record(ToolCallRecord(tool_name="search", agent_name="a1", status="success", duration_ms=1.0))
    log.record(ToolCallRecord(tool_name="search", agent_name="a1", status="error", duration_ms=1.0))
    log.record(ToolCallRecord(tool_name="search", agent_name="a1", status="error", duration_ms=1.0))
    assert log.error_rate("search") == pytest.approx(2 / 3)


def test_error_rate_overall_across_all_tools():
    log = InMemoryToolCallAuditLog()
    log.record(ToolCallRecord(tool_name="a", agent_name="x", status="error", duration_ms=1.0))
    log.record(ToolCallRecord(tool_name="b", agent_name="x", status="success", duration_ms=1.0))
    assert log.error_rate() == 0.5


def test_error_rate_with_no_records_is_zero():
    log = InMemoryToolCallAuditLog()
    assert log.error_rate() == 0.0
    assert log.error_rate("nonexistent") == 0.0
