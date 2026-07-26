"""文档分块——重叠比例控制 + 防御性参数校验。

改造自checklist A类"文档分块设计子类"真实设计要点（对`anything-chat-rag`
真实代码`chunking_by_token_size`的设计提炼，不照抄实现，重新设计通用版本）：

1. **相邻片段保留合理重叠**（行业惯例10%-20%区间）——检索到某个片段时，
   如果答案恰好横跨两个片段的边界，重叠部分能让边界附近的上下文不会
   被硬切丢失。
2. **防御性校验重叠量**：真实项目里发现过一个隐蔽bug——如果调用方把
   `overlap_size`设置得比`chunk_size`还大/相近，`range(0, n, step)`的
   `step`会变成0或负数，Python对此静默返回空序列——文档"分块成功"但
   实际零片段产出，这种"看起来正常但结果全错"的失败模式比直接报错
   更难排查。本模块在参数校验阶段就直接拒绝这类不合理组合，而不是
   让它在运行时悄悄产出空列表。
3. **语义边界优先切分**的兜底策略：如果按语义边界（如段落分隔符）切出来
   的某一段仍然超出大小限制，必须有明确的兜底（本模块选择：对超长段
   再做一次机械切分，而不是无声地生成一个超限的"片段"）。
"""

from __future__ import annotations

from dataclasses import dataclass

DEFAULT_OVERLAP_RATIO = 0.15
"""行业惯例的10%-20%重叠区间取中间值作为默认——不是拍脑袋数字，参考了
真实项目文档字符串里"行业推荐的10-20%区间"这一具体表述。"""


class InvalidChunkingParametersError(ValueError):
    """`overlap_size`相对`chunk_size`的比例不合理时抛出——防止`range()`
    步长退化为0/负数导致静默产出空片段列表。"""


@dataclass(frozen=True)
class Chunk:
    """一个文本片段——保留在原文里的起止位置，方便回溯定位。"""

    text: str
    start: int
    end: int
    sequence: int


def chunk_by_token_estimate(
    text: str, *, chunk_size: int = 1000, overlap_ratio: float = DEFAULT_OVERLAP_RATIO,
) -> list[Chunk]:
    """按估算token数（字符数//4，与`ainative-memory`保持一致的估算比例）切分文本，
    相邻片段保留`overlap_ratio`比例的重叠内容。

    Args:
        text: 待切分的原文。
        chunk_size: 每个片段的目标token数上限。
        overlap_ratio: 相邻片段的重叠比例（0-1之间，行业惯例10%-20%）。

    Raises:
        InvalidChunkingParametersError: `overlap_ratio`不在`[0, 1)`区间内——
            等于或超过1会让"重叠量"达到甚至超过片段本身大小，导致切分
            步长退化为0或负数。
    """
    if not (0.0 <= overlap_ratio < 1.0):
        raise InvalidChunkingParametersError(
            f"overlap_ratio must be in [0, 1), got {overlap_ratio} "
            f"(a ratio >= 1 makes the step size <= 0, silently producing zero chunks)"
        )
    if chunk_size <= 0:
        raise InvalidChunkingParametersError(f"chunk_size must be positive, got {chunk_size}")

    chars_per_token = 4
    chunk_chars = chunk_size * chars_per_token
    overlap_chars = int(chunk_chars * overlap_ratio)
    step = chunk_chars - overlap_chars

    if not text:
        return []

    chunks: list[Chunk] = []
    start = 0
    sequence = 0
    while start < len(text):
        end = min(start + chunk_chars, len(text))
        chunks.append(Chunk(text=text[start:end], start=start, end=end, sequence=sequence))
        sequence += 1
        if end >= len(text):
            break
        start += step
    return chunks


def chunk_by_semantic_boundary(
    text: str, *, separator: str = "\n\n", chunk_size: int = 1000, overlap_ratio: float = DEFAULT_OVERLAP_RATIO,
) -> list[Chunk]:
    """优先按语义边界（默认段落分隔符）切分；任何一段仍然超出`chunk_size`时，
    对那一段单独做`chunk_by_token_estimate()`机械二次切分兜底——不允许
    任何一个最终片段悄悄超出大小限制却不被处理。
    """
    chars_per_token = 4
    chunk_chars = chunk_size * chars_per_token

    paragraphs = text.split(separator)
    chunks: list[Chunk] = []
    sequence = 0
    cursor = 0
    for paragraph in paragraphs:
        para_start = cursor
        cursor += len(paragraph) + len(separator)
        if not paragraph:
            continue
        if len(paragraph) <= chunk_chars:
            chunks.append(Chunk(text=paragraph, start=para_start, end=para_start + len(paragraph), sequence=sequence))
            sequence += 1
        else:
            for sub in chunk_by_token_estimate(paragraph, chunk_size=chunk_size, overlap_ratio=overlap_ratio):
                chunks.append(Chunk(
                    text=sub.text, start=para_start + sub.start, end=para_start + sub.end, sequence=sequence,
                ))
                sequence += 1
    return chunks
