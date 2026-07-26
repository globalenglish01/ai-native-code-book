"""`SpanExporter`协议的内存版默认实现——供demo/测试使用。"""

from __future__ import annotations

from ainative_observability.tracing import SpanRecord


class InMemorySpanExporter:
    """把span原样存进内存列表——真实项目应实现`SpanExporter`协议接入真实后端。"""

    def __init__(self) -> None:
        self._records: list[SpanRecord] = []

    def export(self, record: SpanRecord) -> None:
        self._records.append(record)

    def all(self) -> list[SpanRecord]:
        return list(self._records)

    def for_correlation_id(self, correlation_id: str) -> list[SpanRecord]:
        return [r for r in self._records if r.correlation_id == correlation_id]


class AlwaysFailingSpanExporter:
    """故意每次都抛异常的exporter——用于测试`Tracer`的导出失败自我监控是否生效。"""

    def export(self, record: SpanRecord) -> None:
        raise ConnectionError("simulated span export backend unavailable")
