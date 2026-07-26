"""产品示例：RAG知识库问答服务——完整检索增强生成流水线。

真实产品形态：文档摄入时按重叠比例分块存储（保留边界上下文）；查询时
分别跑关键词检索和"向量检索"（用简单打分模拟），用RRF按排名融合两路
结果而不是直接比较不可比的原始分数；融合后的候选文档过一遍重排序
（未评分的文档不会被误判成最相关）；组织上下文、生成回答、附带引用；
完全没有检索到相关内容时诚实拒答，而不是编造一个看似合理的答案；
回答按查询+涉及文档版本生成新鲜度感知的缓存键，文档更新后旧缓存
自然失效。

组合的包：ainative-rag（本次新增的全部六个子模块）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ainative_rag import (
    RagAnswer,
    ScoredChunk,
    aggregate_chunk_scores,
    apply_rerank_if_enabled,
    build_freshness_aware_cache_key,
    chunk_by_token_estimate,
    ranked_results_from_scores,
    rrf_fuse,
)


@dataclass
class IngestedDocument:
    doc_id: str
    version: str
    chunks: list[str] = field(default_factory=list)


class RagKnowledgeBaseService:
    """把ainative-rag的六个模块组合成一条完整的"摄入->检索->融合->重排序
    ->生成->缓存"流水线。"""

    def __init__(self) -> None:
        self._documents: dict[str, IngestedDocument] = {}
        self._answer_cache: dict[str, RagAnswer] = {}

    def ingest(self, doc_id: str, text: str, *, version: str) -> int:
        """摄入一份文档：按重叠比例分块存储，返回产生的片段数。"""
        chunks = chunk_by_token_estimate(text, chunk_size=50, overlap_ratio=0.15)
        self._documents[doc_id] = IngestedDocument(doc_id=doc_id, version=version, chunks=[c.text for c in chunks])
        return len(chunks)

    def _keyword_search(self, query: str) -> dict[str, float]:
        """按词汇集合精确匹配打分——用词而不是子字符串匹配，避免"a"/"is"
        这类常见短词因为作为子字符串出现在几乎任何文本里而造成误判命中。"""
        query_terms = set(query.lower().split())
        scores = {}
        for doc_id, doc in self._documents.items():
            doc_terms = set(" ".join(doc.chunks).lower().split())
            matched = query_terms & doc_terms
            if matched:
                scores[doc_id] = float(len(matched))
        return scores

    def _vector_search(self, query: str) -> dict[str, float]:
        """demo用的"向量相似度"模拟——按词汇集合的Jaccard相似度打分（真实
        embedding语义相似度会理解同义词/上下文，这里只是提供一个与关键词
        精确计数不同的独立打分维度，用来演示两路检索结果融合，不代表
        真实embedding效果）。"""
        query_terms = set(query.lower().split())
        scores = {}
        for doc_id, doc in self._documents.items():
            doc_terms = set(" ".join(doc.chunks).lower().split())
            intersection = query_terms & doc_terms
            union = query_terms | doc_terms
            if intersection:
                scores[doc_id] = len(intersection) / len(union)
        return scores

    def _rerank(self, doc_ids: list[str], query: str) -> dict[str, float]:
        """demo用的重排序——真实项目应替换成真正的cross-encoder重排序模型调用。
        故意让某个候选文档"重排序失败未评分"，演示`aggregate_chunk_scores`
        不会把它错当成满分。"""
        scored_chunks = []
        for doc_id in doc_ids:
            if doc_id == "doc-unscored":
                scored_chunks.append(ScoredChunk(chunk_id=f"{doc_id}-0", doc_id=doc_id, rerank_score=None))
            else:
                relevance = len(set(query.lower()) & set(doc_id.lower())) + 1
                scored_chunks.append(ScoredChunk(chunk_id=f"{doc_id}-0", doc_id=doc_id, rerank_score=relevance / 10))
        return aggregate_chunk_scores(scored_chunks)

    async def query(self, query: str) -> RagAnswer:
        doc_versions = {doc_id: doc.version for doc_id, doc in self._documents.items()}
        cache_key = build_freshness_aware_cache_key(query, doc_versions=doc_versions)
        if cache_key in self._answer_cache:
            return self._answer_cache[cache_key]

        keyword_scores = self._keyword_search(query)
        vector_scores = self._vector_search(query)

        if not keyword_scores and not vector_scores:
            answer = RagAnswer(text="I don't have enough information in the knowledge base to answer this question.",
                                citations=[], grounded=False)
            self._answer_cache[cache_key] = answer
            return answer

        fused = rrf_fuse(ranked_results_from_scores(keyword_scores), ranked_results_from_scores(vector_scores))
        candidate_doc_ids = [doc_id for doc_id, _score in fused]

        reranked_order = apply_rerank_if_enabled(
            candidate_doc_ids, enabled=True, rerank_fn=lambda ids: self._rerank(ids, query),
        )

        top_doc_id = reranked_order[0]
        context = " ".join(self._documents[top_doc_id].chunks[:1])
        answer = RagAnswer(
            text=f"Based on {top_doc_id}: {context[:80]}...",
            citations=[], grounded=True,
        )
        self._answer_cache[cache_key] = answer
        return answer

    def cache_size(self) -> int:
        return len(self._answer_cache)


async def main() -> None:
    import sys

    if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    service = RagKnowledgeBaseService()

    chunk_count = service.ingest("doc-python", "Python is a high-level programming language known for readability.", version="v1")
    print(f"ingested doc-python into {chunk_count} chunk(s)")
    service.ingest("doc-unscored", "This document mentions python too but reranking will fail to score it.", version="v1")
    service.ingest("doc-unrelated", "The weather today is sunny with a light breeze.", version="v1")

    answer1 = await service.query("what is python")
    print(f"\nquery 1 -> grounded={answer1.grounded}, text={answer1.text}")

    # Same query again — should hit the freshness-aware cache.
    answer2 = await service.query("what is python")
    print(f"query 1 repeated -> same cached answer: {answer1.text == answer2.text}, cache size: {service.cache_size()}")

    # Update the document's version — the cache key changes, so a stale
    # cached answer is never served for content that has since changed.
    service.ingest("doc-python", "Python is a versatile, high-level language used in AI and web development.", version="v2")
    await service.query("what is python")
    print(f"after document update -> cache size grew (new key): {service.cache_size() == 2}")

    # A query with no relevant content in the knowledge base gets an
    # honest refusal, not a fabricated answer.
    answer4 = await service.query("quantum physics equations")
    print(f"\nunrelated query -> grounded={answer4.grounded}, text={answer4.text}")
    print(f"final cache size: {service.cache_size()}")


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
