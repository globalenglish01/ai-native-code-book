"""对话历史的token预算裁剪——防止"历史消息不计入预算"这个真实反面案例。

背景：调研中发现一个真实的生产反面案例——某RAG系统里，检索到的文档片段/
实体/关系都有明确的token预算与截断逻辑，唯独`conversation_history`完全
没有任何token计数或截断机制，随对话轮数增长可以无限膨胀，即使其余部分
的预算算得再精确，加上一段未被计入预算的历史后，最终prompt仍可能超出
模型上下文窗口。

本模块把"对话历史必须纳入统一token预算"作为默认行为而非可选项：
`trim_history_to_budget`永远按预算裁剪，调用方如果真的需要不裁剪，
需要显式传一个很大的`max_tokens`，而不是"忘记裁剪"这种默认状态。
"""

from __future__ import annotations

from typing import Any


def _estimate_tokens(text: str) -> int:
    """粗略估算：字符数/4。与`ainative_guardrail.budget_middleware`保持一致的
    经验估算比例（best-effort，不追求精确到真实tokenizer的程度）。"""
    return len(text) // 4


def trim_history_to_budget(
    history: list[dict[str, Any]],
    *,
    max_tokens: int,
    content_key: str = "content",
) -> list[dict[str, Any]]:
    """从最近的消息开始保留，直到达到`max_tokens`预算为止（旧消息被丢弃）。

    Args:
        history: 消息列表，每条至少包含`content_key`指定的字段（纯文本）。
        max_tokens: 对话历史允许占用的token预算上限。
        content_key: 消息文本内容所在的字段名。

    Returns:
        从`history`尾部（最近的消息）开始保留、总估算token数不超过`max_tokens`
        的子列表，顺序与原列表一致（时间顺序，不是倒序）。
    """
    kept: list[dict[str, Any]] = []
    used = 0
    for message in reversed(history):
        text = str(message.get(content_key, ""))
        cost = _estimate_tokens(text)
        if kept and used + cost > max_tokens:
            break
        kept.append(message)
        used += cost
    kept.reverse()
    return kept


def estimate_history_tokens(history: list[dict[str, Any]], *, content_key: str = "content") -> int:
    """估算整段历史的token占用，供调用方并入自己的总预算计算。"""
    return sum(_estimate_tokens(str(m.get(content_key, ""))) for m in history)
