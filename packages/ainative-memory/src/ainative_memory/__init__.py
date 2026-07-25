"""ainative-memory —— checkpoint持久化协议、长期记忆存储、PII脱敏代理、历史token预算裁剪。

摘要压缩配置（context-window-aware summarization config）复用
`ainative_core.model_factory.get_summarization_config`，本包不重复实现。
"""

from __future__ import annotations

from ainative_memory.checkpoint import CheckpointSaverFactory
from ainative_memory.history_budget import estimate_history_tokens, trim_history_to_budget
from ainative_memory.redacting_backend import RedactingBackend, wrap_summarization_backend
from ainative_memory.rendering import render_memory_entries
from ainative_memory.store import InMemoryMemoryStore

__version__ = "0.1.0"

__all__ = [
    "CheckpointSaverFactory",
    "InMemoryMemoryStore",
    "RedactingBackend",
    "estimate_history_tokens",
    "render_memory_entries",
    "trim_history_to_budget",
    "wrap_summarization_backend",
]
