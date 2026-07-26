from __future__ import annotations

import pytest
from ainative_rag.pipeline import NO_RELEVANT_CONTENT_MESSAGE, Citation, InMemoryRetriever, run_rag_query


async def _fake_generate(query: str, context: str) -> str:
    return f"answer based on: {context}"


@pytest.mark.asyncio
async def test_run_rag_query_returns_grounded_answer_with_citations():
    retriever = InMemoryRetriever(documents={"doc-1": "Python is a programming language"})

    answer = await run_rag_query("python", retriever=retriever, generate_fn=_fake_generate)

    assert answer.grounded is True
    assert answer.citations == [Citation(doc_id="doc-1", chunk_id="doc-1-0")]


@pytest.mark.asyncio
async def test_run_rag_query_with_no_matching_documents_returns_honest_refusal():
    """The core design guarantee: no relevant content found must produce
    an explicit, honest "not enough information" answer — never a
    generated response that looks confident but has nothing backing it."""
    retriever = InMemoryRetriever(documents={"doc-1": "completely unrelated content about gardening"})

    answer = await run_rag_query("quantum computing", retriever=retriever, generate_fn=_fake_generate)

    assert answer.grounded is False
    assert answer.text == NO_RELEVANT_CONTENT_MESSAGE
    assert answer.citations == []


@pytest.mark.asyncio
async def test_run_rag_query_with_empty_knowledge_base_returns_honest_refusal():
    retriever = InMemoryRetriever(documents={})
    answer = await run_rag_query("anything", retriever=retriever, generate_fn=_fake_generate)
    assert answer.grounded is False


@pytest.mark.asyncio
async def test_run_rag_query_respects_max_context_chunks():
    retriever = InMemoryRetriever(documents={
        "doc-1": "python programming", "doc-2": "python language", "doc-3": "python code",
    })

    answer = await run_rag_query("python", retriever=retriever, generate_fn=_fake_generate, max_context_chunks=2)

    assert len(answer.citations) == 2


@pytest.mark.asyncio
async def test_in_memory_retriever_ranks_by_keyword_match_count():
    retriever = InMemoryRetriever(documents={
        "doc-1": "python python python",
        "doc-2": "python once",
    })
    results = await retriever.retrieve("python")
    assert results[0].doc_id == "doc-1"
