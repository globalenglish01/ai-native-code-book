"""ainative-rag —— 文档分块、混合检索融合(RRF)、重排序聚合、检索增强生成
流水线、Embedding批处理、内容新鲜度感知缓存键。

改造自对`anything-chat-rag`（MIT协议，LightRAG衍生项目）真实代码的设计
提炼——本模块只重新实现设计模式（分块策略/融合公式/聚合逻辑/流水线结构），
不照抄LightRAG本体的具体实现代码，也修复了提炼过程中发现的几处真实
逻辑缺陷（详见各子模块docstring里标注的"真实bug背景"）。
"""

# 这个文件是整个包的"入口文件"——当别的代码写`from ainative_rag import
# Chunk`时，Python实际上就是先执行这个`__init__.py`，再从里面找`Chunk`
# 这个名字。这个文件本身几乎不包含任何业务逻辑，主要作用是把分散在
# 各个子模块（chunking.py/hybrid_search.py等）里定义的类/函数/常量，
# 统一"搬"到包的顶层，让使用者可以直接写`from ainative_rag import xxx`，
# 不需要知道`xxx`具体是在哪个子模块文件里定义的。

from __future__ import annotations

# 下面这几组import，分别从各个子模块把公开的类/函数/常量导入进来。
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
from ainative_rag.hybrid_search import InvalidRankError, RankedResult, ranked_results_from_scores, rrf_fuse
from ainative_rag.pipeline import (
    NO_RELEVANT_CONTENT_MESSAGE,
    Citation,
    InMemoryRetriever,
    RagAnswer,
    RetrievedChunk,
    Retriever,
    run_rag_query,
)
from ainative_rag.reranking import (
    DEFAULT_MISSING_SCORE,
    InvalidRerankScoreError,
    ScoredChunk,
    aggregate_chunk_scores,
    apply_rerank_if_enabled,
)

__version__ = "0.1.0"
# ↑ 这个包当前的版本号——写成字符串常量，方便别的代码（或者调试时）
#   查询`ainative_rag.__version__`确认自己用的是哪个版本。

# `__all__`是Python的一个特殊约定变量：定义"当别人写
# `from ainative_rag import *`（星号导入，导入这个包里所有公开的东西）
# 时，具体应该导入哪些名字"。它同时也是给人看的一份清晰的"本包公开
# API清单"——按字母顺序列出所有希望外部代码使用的类名/函数名/常量名。
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
    "InvalidRankError",
    "InvalidRerankScoreError",
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
