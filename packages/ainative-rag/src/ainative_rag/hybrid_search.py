"""混合检索融合——基于排名的RRF（Reciprocal Rank Fusion），不直接比较原始分数。

改造自checklist A类"混合搜索融合设计子类"真实设计要点（对`anything-chat-rag`
真实代码`bm25.py`的`rrf_fuse`设计提炼）：融合多种不同标准算出来的排序结果
（比如BM25关键词匹配 + 向量相似度检索）时，必须用基于"排名"而不是直接
比较"原始分数"的融合方式——不同算法的分数量纲完全不可比（BM25分数可能
是0-20的任意正数，余弦相似度是0-1），直接相加/加权平均会产生失真的
排序结果。RRF只关心"这个文档在每一路检索结果里排第几名"，天然规避了
量纲不可比的问题。
"""

from __future__ import annotations

from dataclasses import dataclass

DEFAULT_RRF_K = 60
"""RRF公式里的平滑常数k——业界惯用默认值60（出自RRF原始论文的实验结果），
避免排名靠前的文档因为分母过小而权重被过度放大。"""


class InvalidRankError(ValueError):
    """`RankedResult.rank`不满足"从1开始"这个约定时抛出。"""


@dataclass(frozen=True)
class RankedResult:
    """一路检索结果里的一条命中——只需要文档标识和它在这一路检索结果里的排名。"""

    doc_id: str
    rank: int
    """从1开始的排名（1表示这一路检索里最相关的结果）。"""

    def __post_init__(self) -> None:
        if self.rank < 1:
            raise InvalidRankError(
                f"rank must be >= 1 (ranks start from 1), got {self.rank} for doc_id={self.doc_id!r} — "
                f"an off-by-one upstream (e.g. enumerate() starting at 0) would otherwise silently "
                f"cause rrf_fuse's `1/(k+rank)` to divide by zero or produce a negative denominator"
            )


def rrf_fuse(*result_lists: list[RankedResult], k: int = DEFAULT_RRF_K) -> list[tuple[str, float]]:
    """把多路检索结果按RRF公式融合成一份统一排序，返回`(doc_id, rrf_score)`
    按分数降序排列的列表。

    RRF公式：一个文档的最终分数 = 它在每一路结果里`1/(k+rank)`的总和——
    只在某一路结果里出现的文档，那一路没出现就不贡献分数（不是当作
    最差排名参与计算，避免"完全没被某个检索器召回"和"被检索到但排名
    很靠后"这两种不同情况被混淆处理）。
    """
    scores: dict[str, float] = {}
    for results in result_lists:
        for result in results:
            scores[result.doc_id] = scores.get(result.doc_id, 0.0) + 1.0 / (k + result.rank)
    return sorted(scores.items(), key=lambda item: item[1], reverse=True)


def ranked_results_from_scores(doc_scores: dict[str, float]) -> list[RankedResult]:
    """把一份"文档->原始分数"的检索结果，转换成`rrf_fuse`需要的`RankedResult`
    列表——分数降序即排名（从1开始），供调用方在拿到向量检索/关键词检索
    各自的原始打分结果后，统一转换成排名再融合。
    """
    ordered = sorted(doc_scores.items(), key=lambda item: item[1], reverse=True)
    return [RankedResult(doc_id=doc_id, rank=i + 1) for i, (doc_id, _score) in enumerate(ordered)]
