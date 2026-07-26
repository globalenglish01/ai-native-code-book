"""重排序分数聚合——缺失分数的默认值语义必须诚实，禁用/失败时一致回退。

改造自checklist A类"Reranking重排序设计子类"真实发现的反面案例（对
`anything-chat-rag`真实代码`aggregate_chunk_scores`的逻辑颠倒问题
提炼，本模块直接实现修复后的正确版本）：

真实bug背景：原版重排序没能给某个片段打分时（比如那一批打分请求本身
失败），默认值给了1.0（满分）——导致"评估环节本身出问题、不知道是否
相关"的片段反而必然通过"最低分数过滤"这一关，而真正被认真评估过、
给出低分的片段却会被过滤掉，是一处逻辑完全颠倒的真实缺陷。

正确设计原则：
1. **缺失分数不能默认成满分**——本模块默认给缺失分数最低值（0.0），
   语义上准确对应"这份内容没有被验证过相关性，不应该被优先展示"，
   而不是碰巧混进"最相关"的结果里。
2. **禁用/未配置/调用失败三种情况必须处理一致**——统一退回到原始
   检索顺序，而不是三种情况各自产生不同的、不可预期的行为。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

DEFAULT_MISSING_SCORE = 0.0
"""重排序未能给某个片段打分时的默认分数——刻意选择最低值而非满分，
对应"未验证相关性的内容不应该被当作高相关内容对待"这一诚实语义。"""


@dataclass(frozen=True)
class ScoredChunk:
    chunk_id: str
    doc_id: str
    rerank_score: float | None
    """`None`表示这个片段没有被重排序模型打分（比如那一批请求失败）——
    与"确实打分了、但分数是0.0"是两种不同的情况，聚合时都会被当作
    `DEFAULT_MISSING_SCORE`处理，但保留`None`这个区分是为了让调用方
    需要时能单独统计"重排序覆盖率"。"""


def aggregate_chunk_scores(
    chunks: list[ScoredChunk], *, missing_score: float = DEFAULT_MISSING_SCORE,
) -> dict[str, float]:
    """按`doc_id`汇总同一文档下多个片段的重排序分数，取该文档下的最高分
    （比片段更细的重排序结果，最终按文档粒度过滤/展示时的常见汇总策略）。

    未打分的片段（`rerank_score is None`）用`missing_score`兜底参与汇总，
    而不是被`if not scores: continue`这类写法直接跳过——跳过会让整份
    文档完全从汇总结果里消失，等价于"当作从未存在过"，比"当作低分处理"
    更容易造成数据静默丢失（真实项目里这两个问题是同一处bug的一体两面）。
    """
    doc_best_scores: dict[str, float] = {}
    for chunk in chunks:
        score = chunk.rerank_score if chunk.rerank_score is not None else missing_score
        current_best = doc_best_scores.get(chunk.doc_id)
        if current_best is None or score > current_best:
            doc_best_scores[chunk.doc_id] = score
    return doc_best_scores


def apply_rerank_if_enabled(
    doc_ids_in_original_order: list[str],
    *,
    enabled: bool,
    rerank_fn: Callable[[list[str]], dict[str, float]] | None,
) -> list[str]:
    """重排序被禁用、未配置、或调用过程本身失败，三种情况统一退回到
    `doc_ids_in_original_order`（原始检索顺序），而不是产生三种互相
    不一致的行为。
    """
    if not enabled or rerank_fn is None:
        return list(doc_ids_in_original_order)
    try:
        scores = rerank_fn(doc_ids_in_original_order)
    except Exception:
        return list(doc_ids_in_original_order)
    return sorted(
        doc_ids_in_original_order,
        key=lambda doc_id: scores.get(doc_id, DEFAULT_MISSING_SCORE),
        reverse=True,
    )
