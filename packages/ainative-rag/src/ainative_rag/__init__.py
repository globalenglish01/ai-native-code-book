"""AI Native Framework RAG模块——文档分块、混合检索融合(RRF)、重排序聚合、
检索增强生成流水线、Embedding批处理、内容新鲜度感知缓存键。

改造自对`anything-chat-rag`（MIT协议，LightRAG衍生项目）真实代码的设计
提炼——本模块只重新实现设计模式（分块策略/融合公式/聚合逻辑/流水线结构），
不照抄LightRAG本体的具体实现代码，也修复了提炼过程中发现的几处真实
逻辑缺陷（详见各子模块docstring里标注的"真实bug背景"）。
"""

from ainative_rag.chunking import (
    Chunk,
    InvalidChunkingParametersError,
    chunk_by_semantic_boundary,
    chunk_by_token_estimate,
)
from ainative_rag.embedding_batch import (
    DEFAULT_MAX_CHARS_PER_TEXT,
    EmbeddingBatchResult,
    EmbeddingCountMismatchError,
    embed_batch_with_accounting,
)
from ainative_rag.freshness import build_freshness_aware_cache_key
from ainative_rag.hybrid_search import RankedResult, ranked_results_from_scores, rrf_fuse
from ainative_rag.pipeline import (
    NO_RELEVANT_CONTENT_MESSAGE,
    Citation,
    InMemoryRetriever,
    RagAnswer,
    RetrievedChunk,
    Retriever,
    run_rag_query,
)
from ainative_rag.reranking import DEFAULT_MISSING_SCORE, ScoredChunk, aggregate_chunk_scores, apply_rerank_if_enabled

__version__ = "0.1.0"

__all__ = [
    "DEFAULT_MAX_CHARS_PER_TEXT",
    "DEFAULT_MISSING_SCORE",
    "NO_RELEVANT_CONTENT_MESSAGE",
    "Chunk",
    "Citation",
    "EmbeddingBatchResult",
    "EmbeddingCountMismatchError",
    "InMemoryRetriever",
    "InvalidChunkingParametersError",
    "RagAnswer",
    "RankedResult",
    "RetrievedChunk",
    "Retriever",
    "ScoredChunk",
    "aggregate_chunk_scores",
    "apply_rerank_if_enabled",
    "build_freshness_aware_cache_key",
    "chunk_by_semantic_boundary",
    "chunk_by_token_estimate",
    "embed_batch_with_accounting",
    "ranked_results_from_scores",
    "rrf_fuse",
    "run_rag_query",
]
