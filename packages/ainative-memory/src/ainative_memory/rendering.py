"""把裁剪后的记忆条目渲染成可直接注入Prompt的文本。

改造自真实项目里"跨章节书籍记忆"的设计：`MemoryStore.load_recent()`
只负责"存/取/裁剪"，"怎么把一组记忆条目拼成一段可读文本"是一个独立的、
纯函数式的关注点——分开是为了让调用方可以自由替换渲染格式（比如换成
JSON、换成不同的分隔符），而不用改动存储层代码。
"""

from __future__ import annotations

from ainative_core.protocols import MemoryEntry


def render_memory_entries(entries: list[MemoryEntry], *, header_template: str = "## Memory #{sequence}") -> str:
    """把一组`MemoryEntry`渲染成带编号标题的纯文本，适合直接拼进system prompt。

    Args:
        entries: 通常来自`MemoryStore.load_recent()`的返回值（已按sequence降序排列）。
        header_template: 每条记忆的标题模板，`{sequence}`会被替换成该条记忆的序号。

    Returns:
        多条记忆用空行分隔拼接成的文本；`entries`为空时返回空字符串。
    """
    if not entries:
        return ""
    blocks = [f"{header_template.format(sequence=e.sequence)}\n{e.content}" for e in entries]
    return "\n\n".join(blocks)
