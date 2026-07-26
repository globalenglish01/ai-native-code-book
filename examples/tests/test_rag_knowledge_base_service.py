from __future__ import annotations

import pytest
from products.rag_knowledge_base_service import RagKnowledgeBaseService


@pytest.mark.asyncio
async def test_query_returns_grounded_answer_for_matching_document():
    service = RagKnowledgeBaseService()
    service.ingest("doc-python", "Python is a high-level programming language.", version="v1")

    answer = await service.query("what is python")

    assert answer.grounded is True
    assert "doc-python" in answer.text


@pytest.mark.asyncio
async def test_query_with_no_matching_documents_returns_honest_refusal():
    service = RagKnowledgeBaseService()
    service.ingest("doc-python", "Python is a high-level programming language.", version="v1")

    answer = await service.query("quantum physics equations")

    assert answer.grounded is False
    assert "don't have enough information" in answer.text


@pytest.mark.asyncio
async def test_repeated_query_hits_the_cache():
    service = RagKnowledgeBaseService()
    service.ingest("doc-python", "Python is a high-level programming language.", version="v1")

    await service.query("what is python")
    await service.query("what is python")

    assert service.cache_size() == 1


@pytest.mark.asyncio
async def test_document_version_update_invalidates_the_cache_for_that_query():
    """The core freshness guarantee: updating a document must not cause
    the old cached answer (generated from stale content) to keep being
    served."""
    service = RagKnowledgeBaseService()
    service.ingest("doc-python", "Python is a high-level programming language.", version="v1")

    await service.query("what is python")
    assert service.cache_size() == 1

    service.ingest("doc-python", "Python is now described differently.", version="v2")
    await service.query("what is python")

    assert service.cache_size() == 2


@pytest.mark.asyncio
async def test_unscored_document_is_not_treated_as_the_top_result():
    """Exercises the reranking fix directly: a document that fails to get
    a rerank score must not outrank documents that were actually scored,
    even if it matches the query's keywords."""
    service = RagKnowledgeBaseService()
    service.ingest("doc-unscored", "python content that reranking fails to score", version="v1")
    service.ingest("doc-python", "Python is a high-level programming language.", version="v1")

    answer = await service.query("python")

    assert "doc-unscored" not in answer.text
