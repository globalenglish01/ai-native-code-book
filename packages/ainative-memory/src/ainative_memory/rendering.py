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
    # 这是本函数唯一的"提前返回"分支：如果压根没有任何记忆条目要渲染
    # （比如调用方是一个全新的、还没有历史记忆的owner），直接返回空
    # 字符串——不需要往下走拼接逻辑，也避免了`"\n\n".join([])`这种边界
    # 情况需要额外考虑（虽然那样其实也会正确返回空字符串，但提前返回
    # 让"没有记忆"这个特殊情况一眼就能看明白）。
    if not entries:
        return ""

    # 这是一个"列表推导式"：对`entries`里的每一条记忆`e`，都生成一段
    # "标题+正文"的文本块。`header_template.format(sequence=e.sequence)`
    # ——`str.format()`是Python字符串的方法，把模板字符串里的`{sequence}`
    # 占位符替换成实际传入的值（这里是这条记忆的序号）。比如默认模板
    # `"## Memory #{sequence}"`配合`sequence=3`，会渲染成
    # `"## Memory #3"`。`f"{...}\n{e.content}"`——用f-string把"渲染好的
    # 标题"和"这条记忆的正文内容`e.content`"用换行符拼在一起，组成
    # 一个完整的文本块。
    blocks = [f"{header_template.format(sequence=e.sequence)}\n{e.content}" for e in entries]
    # `"\n\n".join(blocks)`——把上面生成的多个文本块，用"一个空行"
    # （两个连续换行符）连接成一整段文本，视觉上让每条记忆之间有清晰的
    # 分隔，方便后续直接整段拼进system prompt里，模型阅读起来也更容易
    # 区分"这是第几条记忆、从哪里到哪里"。
    return "\n\n".join(blocks)
