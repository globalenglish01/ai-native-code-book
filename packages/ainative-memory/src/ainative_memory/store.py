"""`ainative_core.protocols.MemoryStore`的内存版默认实现。

改造自真实项目里"跨章节书籍记忆"的滑动窗口裁剪设计：记忆的存储/裁剪
与记忆内容的生成（调用LLM提取摘要）完全解耦——本模块只负责"存/取/
按owner删除"，"提取什么、怎么提取"交给调用方（通常搭配
`ainative_prompt.judge`或调用方自己的LLM调用逻辑）。
"""

from __future__ import annotations

import copy
import dataclasses

from ainative_core.protocols import MemoryEntry


class InMemoryMemoryStore:
    """`MemoryStore`的内存版实现——用dict存储，按owner_id分组。

    `MemoryEntry`本身是frozen dataclass，但它的`metadata`字段是普通可变
    dict——"frozen"只阻止字段被重新赋值，不阻止字段内容被原地修改。
    `append()`/`load_recent()`都对`metadata`做深拷贝，而不是直接存储/
    返回调用方传入的原始对象：调用方常见的用法是复用同一个可变dict作为
    "元数据模板"分别构造多条`MemoryEntry`，如果内部存的是引用，后续继续
    修改这个模板会静默篡改"已经存进去"的历史记忆条目。
    """

    def __init__(self) -> None:
        self._entries: dict[str, list[MemoryEntry]] = {}

    async def append(self, entry: MemoryEntry) -> None:
        bucket = self._entries.setdefault(entry.owner_id, [])
        bucket.append(dataclasses.replace(entry, metadata=copy.deepcopy(entry.metadata)))

    async def load_recent(
        self, owner_id: str, *, before_sequence: int | None = None, max_items: int = 10
    ) -> list[MemoryEntry]:
        bucket = self._entries.get(owner_id, [])
        candidates = (
            [e for e in bucket if e.sequence < before_sequence]
            if before_sequence is not None
            else list(bucket)
        )
        candidates.sort(key=lambda e: e.sequence, reverse=True)
        return [dataclasses.replace(e, metadata=copy.deepcopy(e.metadata)) for e in candidates[:max_items]]

    async def delete_by_owner(self, owner_id: str) -> int:
        bucket = self._entries.pop(owner_id, [])
        return len(bucket)
