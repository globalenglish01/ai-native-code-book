"""ainative-observability —— 结构化JSON日志、统一敏感信息过滤器、轻量级追踪span记录。"""

from __future__ import annotations

from ainative_observability.memory_backends import AlwaysFailingSpanExporter, InMemorySpanExporter
from ainative_observability.structured_logging import (
    DEFAULT_SENSITIVE_KEYS,
    JsonFormatter,
    SensitiveDataFilter,
    install_structured_logging,
)
from ainative_observability.tracing import SpanExporter, SpanRecord, Tracer

__version__ = "0.1.0"

__all__ = [
    "DEFAULT_SENSITIVE_KEYS",
    "AlwaysFailingSpanExporter",
    "InMemorySpanExporter",
    "JsonFormatter",
    "SensitiveDataFilter",
    "SpanExporter",
    "SpanRecord",
    "Tracer",
    "install_structured_logging",
]
