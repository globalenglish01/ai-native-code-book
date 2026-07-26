"""Embedding批量请求 + 数量对账 + 超长文本预检截断。

改造自checklist A类"Embedding与向量化处理子类"真实发现的反面案例（对
`anything-chat-rag`真实代码`llm/openai.py`的`openai_embed`设计提炼，
修复其中一处真实bug后重新实现）：

真实bug背景：批量获取向量时，如果供应商返回的向量数量和请求的文字
数量对不上，原版只记一条错误日志、函数隐式返回`None`，调用方（全代码库
几十处调用点）都没有检查返回值，导致这批文档的实体/关系数据静默丢失、
且完全无法被感知。

正确设计原则：
1. **数量对账失败必须是硬错误**，让异常真正传导到能感知和处理它的
   地方，而不是记一条容易被忽略的日志后悄悄返回`None`。
2. **单段文字长度超过Embedding模型处理上限**这件事，必须在真正调用
   API之前就主动截断处理，而不是等供应商那边报错。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

DEFAULT_MAX_CHARS_PER_TEXT = 32_000
"""单段文字送去embedding之前的字符数上限——不同embedding模型的真实token
上限不同，这是一个保守的默认预检阈值，真实项目应该按具体模型的文档
覆盖这个值。"""


class EmbeddingCountMismatchError(RuntimeError):
    """供应商返回的向量数量与请求的文字数量不一致时抛出——不允许静默
    丢弃这批不对账的embedding请求。"""

    def __init__(self, *, requested: int, received: int) -> None:
        super().__init__(
            f"embedding count mismatch: requested {requested} texts but received {received} vectors — "
            f"this batch's data would be silently lost if not treated as a hard error"
        )
        self.requested = requested
        self.received = received


@dataclass(frozen=True)
class EmbeddingBatchResult:
    vectors: list[list[float]]
    truncated_count: int
    """这一批里有多少段文字在调用API之前被截断过——供调用方决定是否需要
    对这些文档做进一步处理（比如改用支持更长上下文的embedding模型）。"""


async def embed_batch_with_accounting(
    texts: list[str],
    *,
    embed_fn: Callable[[list[str]], Awaitable[list[list[float]]]],
    max_chars_per_text: int = DEFAULT_MAX_CHARS_PER_TEXT,
) -> EmbeddingBatchResult:
    """批量获取向量，对超长文本做预检截断，并强制核对返回的向量数量。

    Args:
        texts: 待向量化的文字列表。
        embed_fn: 实际调用embedding供应商API的函数，接收一批文字、返回
            对应的向量列表——具体供应商由调用方决定，本函数不内置任何
            供应商依赖。
        max_chars_per_text: 单段文字超过这个长度时，在调用API之前就
            主动截断，而不是让供应商那边因为超出上限而报错。

    Raises:
        EmbeddingCountMismatchError: `embed_fn`返回的向量数量与`texts`
            长度不一致——绝不允许把这种情况当作部分成功静默处理。
    """
    truncated_count = 0
    prepared_texts = []
    for text in texts:
        if len(text) > max_chars_per_text:
            truncated_count += 1
            prepared_texts.append(text[:max_chars_per_text])
        else:
            prepared_texts.append(text)

    if not prepared_texts:
        return EmbeddingBatchResult(vectors=[], truncated_count=0)

    vectors = await embed_fn(prepared_texts)
    if len(vectors) != len(prepared_texts):
        raise EmbeddingCountMismatchError(requested=len(prepared_texts), received=len(vectors))

    return EmbeddingBatchResult(vectors=vectors, truncated_count=truncated_count)
