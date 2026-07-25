"""`protocols.py` 里各 Protocol 的内存版默认实现。

这些实现的存在意义是——脱离任何真实数据库/中间件，也能让上层模块
（ainative-guardrail/ainative-prompt/ainative-security/ainative-eval）
独立跑通demo和单元测试。生产环境不建议直接用这些实现（进程重启数据即丢失），
应该实现 `protocols.py` 里对应的 Protocol，接自己的 Postgres/Redis/MongoDB。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from ainative_core.protocols import PromptVariant


class InMemoryUsageSink:
    """`UsageSink` 的内存版实现——把所有用量事件存进一个list。"""

    def __init__(self) -> None:
        self._events: list[dict[str, Any]] = []

    def record(self, event: dict[str, Any]) -> None:
        self._events.append(event)

    @property
    def events(self) -> list[dict[str, Any]]:
        """只读视图，供测试/demo读取已记录的全部事件。"""
        return list(self._events)

    def total_tokens(self) -> int:
        """把所有事件的 input_tokens+output_tokens 累加——demo/测试里常用的快捷统计。"""
        total = 0
        for event in self._events:
            total += event.get("input_tokens", 0) or 0
            total += event.get("output_tokens", 0) or 0
        return total


@dataclass
class _StickyRecord:
    variant: str
    decided_at: float = field(default_factory=time.time)


class InMemoryPromptStore:
    """`PromptStore` 的内存版实现——用普通dict存变体和粘性路由决策。"""

    def __init__(self) -> None:
        self._variants: dict[tuple[str, str], dict[str, PromptVariant]] = {}
        self._sticky: dict[tuple[str, str, str], _StickyRecord] = {}

    async def get_active_variants(
        self, agent_name: str, prompt_key: str
    ) -> list[PromptVariant]:
        bucket = self._variants.get((agent_name, prompt_key), {})
        return [v for v in bucket.values() if v.is_active]

    async def get_sticky_decision(
        self, agent_name: str, prompt_key: str, thread_id: str
    ) -> str | None:
        record = self._sticky.get((agent_name, prompt_key, thread_id))
        return record.variant if record is not None else None

    async def record_decision(
        self, agent_name: str, prompt_key: str, thread_id: str, variant: str
    ) -> None:
        self._sticky[(agent_name, prompt_key, thread_id)] = _StickyRecord(variant=variant)

    async def save_variant(
        self, agent_name: str, prompt_key: str, variant: PromptVariant
    ) -> None:
        bucket = self._variants.setdefault((agent_name, prompt_key), {})
        bucket[variant.variant] = variant
