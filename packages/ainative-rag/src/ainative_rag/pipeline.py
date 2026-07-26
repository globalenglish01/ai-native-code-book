"""检索增强生成（RAG）查询流水线——分阶段执行 + 引用溯源 + 诚实的空结果处理。

改造自checklist A类"RAG端到端流程设计子类"与"引用溯源与内容新鲜度子类"
真实设计要点（对`anything-chat-rag`真实代码`operate.py`的`kg_query`/
`naive_query`查询流水线、`utils.py`的引用列表生成设计提炼）：

1. **拆解成明确阶段**：检索资料 -> 组织上下文 -> 生成回答，而不是把用户
   原始问题和所有可能相关的资料一股脑丢给AI自己处理。
2. **检索无结果时诚实拒答**：完全没有检索到相关内容时，在早期就明确
   返回"没有足够信息"，而不是让AI基于空上下文强行生成一个可能编造的
   答案——这是本流水线的默认行为，不是可选项。
3. **引用溯源贯穿全程**：从"检索到具体片段"到"回答携带引用信息"要有
   完整链路，而不是检索时打了标签、最终回答却没有把这份信息带出来。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class RetrievedChunk:
    """一次检索命中的片段——`doc_id`+`chunk_id`是引用溯源的最小定位单元。"""

    doc_id: str
    chunk_id: str
    text: str
    score: float


@dataclass(frozen=True)
class Citation:
    """最终回答里携带的一条引用——从`RetrievedChunk`直接转换而来，
    不是回答生成之后才另外拼凑的信息。"""

    doc_id: str
    chunk_id: str


@dataclass(frozen=True)
class RagAnswer:
    text: str
    citations: list[Citation]
    grounded: bool
    """`False`表示检索阶段完全没有命中任何相关内容，`text`是诚实的
    "没有足够信息"提示，而不是模型编造的答案——调用方应该用这个字段
    区分"真的有依据的回答"和"拒答提示"，不要只看`text`是否非空。"""


NO_RELEVANT_CONTENT_MESSAGE = "I don't have enough information in the knowledge base to answer this question."


@runtime_checkable
class Retriever(Protocol):
    """"怎么把一个查询变成一批候选片段"这件事的抽象接口——具体是向量
    检索/关键词检索/混合检索由调用方实现，本流水线只关心拿到的结果。"""

    async def retrieve(self, query: str) -> list[RetrievedChunk]: ...


async def run_rag_query(
    query: str,
    *,
    retriever: Retriever,
    generate_fn: Callable[[str, str], Awaitable[str]],
    max_context_chunks: int = 5,
) -> RagAnswer:
    """完整的RAG查询流水线：检索 -> （无结果则诚实拒答）-> 组织上下文
    -> 生成回答 -> 附带引用。

    Args:
        query: 用户的原始查询。
        retriever: 检索阶段的具体实现。
        generate_fn: 接收`(query, context)`、返回生成回答文本的函数——
            具体调用哪个模型由调用方决定，本流水线不内置任何供应商依赖。
        max_context_chunks: 组织上下文时最多纳入的片段数量。
    """
    chunks = await retriever.retrieve(query)
    if not chunks:
        return RagAnswer(text=NO_RELEVANT_CONTENT_MESSAGE, citations=[], grounded=False)

    selected = chunks[:max_context_chunks]
    context = "\n\n".join(f"[{c.doc_id}#{c.chunk_id}] {c.text}" for c in selected)
    answer_text = await generate_fn(query, context)
    citations = [Citation(doc_id=c.doc_id, chunk_id=c.chunk_id) for c in selected]
    return RagAnswer(text=answer_text, citations=citations, grounded=True)


@dataclass
class InMemoryRetriever:
    """`Retriever`的内存版最小实现——按关键词子串匹配打分，仅供demo/测试
    使用，不代表真正的向量/混合检索能力。"""

    documents: dict[str, str] = field(default_factory=dict)
    """`{doc_id: full_text}`——demo用的极简"知识库"。"""

    async def retrieve(self, query: str) -> list[RetrievedChunk]:
        query_terms = query.lower().split()
        results = []
        for doc_id, text in self.documents.items():
            text_lower = text.lower()
            score = sum(1 for term in query_terms if term in text_lower)
            if score > 0:
                results.append(RetrievedChunk(doc_id=doc_id, chunk_id=f"{doc_id}-0", text=text, score=float(score)))
        results.sort(key=lambda r: r.score, reverse=True)
        return results
