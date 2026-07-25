"""MCP/工具调用审计记录——存储无关的通用schema。

改造自真实项目里验证过的MongoDB工具调用日志设计（原版硬编码collection名、
业务字段如`project_identifier`/`org_id`），泛化成任何工具调用审计场景都能
复用的通用schema：工具名、agent名、入参、出参、耗时、run/thread关联ID、
状态。具体持久化到哪里由调用方决定（内存/数据库/日志系统均可）。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Literal

ToolCallStatus = Literal["success", "error"]


@dataclass(frozen=True)
class ToolCallRecord:
    """一次工具调用的完整审计记录。"""

    tool_name: str
    agent_name: str
    status: ToolCallStatus
    duration_ms: float
    run_id: str | None = None
    thread_id: str | None = None
    input_summary: dict[str, Any] = field(default_factory=dict)
    output_summary: dict[str, Any] = field(default_factory=dict)
    error_message: str | None = None
    timestamp: float = field(default_factory=time.time)


class InMemoryToolCallAuditLog:
    """`ToolCallRecord`的内存版存储——供demo/测试使用，真实项目应实现自己的持久化。"""

    def __init__(self) -> None:
        self._records: list[ToolCallRecord] = []

    def record(self, entry: ToolCallRecord) -> None:
        self._records.append(entry)

    def all(self) -> list[ToolCallRecord]:
        return list(self._records)

    def for_tool(self, tool_name: str) -> list[ToolCallRecord]:
        return [r for r in self._records if r.tool_name == tool_name]

    def error_rate(self, tool_name: str | None = None) -> float:
        """计算错误率——`tool_name`为None时统计全部工具的整体错误率。"""
        records = self.for_tool(tool_name) if tool_name is not None else self._records
        if not records:
            return 0.0
        errors = sum(1 for r in records if r.status == "error")
        return errors / len(records)
