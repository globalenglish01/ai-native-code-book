from __future__ import annotations

from ainative_rag.freshness import build_freshness_aware_cache_key


def test_same_query_and_versions_produce_the_same_key():
    k1 = build_freshness_aware_cache_key("what is x", doc_versions={"doc-1": "v1"})
    k2 = build_freshness_aware_cache_key("what is x", doc_versions={"doc-1": "v1"})
    assert k1 == k2


def test_document_version_change_produces_a_different_key():
    """The core guarantee this module exists for: updating a document's
    content (bumping its version/hash) must naturally invalidate the old
    cache entry — a real bug this fixes was a cache key that only hashed
    the query text, so a stale answer kept being served after the
    underlying document was updated or deleted."""
    k1 = build_freshness_aware_cache_key("what is x", doc_versions={"doc-1": "v1"})
    k2 = build_freshness_aware_cache_key("what is x", doc_versions={"doc-1": "v2"})
    assert k1 != k2


def test_key_is_order_independent_for_the_same_set_of_documents():
    k1 = build_freshness_aware_cache_key("q", doc_versions={"a": "1", "b": "2"})
    k2 = build_freshness_aware_cache_key("q", doc_versions={"b": "2", "a": "1"})
    assert k1 == k2


def test_different_query_text_produces_a_different_key():
    k1 = build_freshness_aware_cache_key("query one", doc_versions={"doc-1": "v1"})
    k2 = build_freshness_aware_cache_key("query two", doc_versions={"doc-1": "v1"})
    assert k1 != k2


def test_different_document_set_produces_a_different_key():
    k1 = build_freshness_aware_cache_key("q", doc_versions={"doc-1": "v1"})
    k2 = build_freshness_aware_cache_key("q", doc_versions={"doc-1": "v1", "doc-2": "v1"})
    assert k1 != k2


def test_no_documents_still_produces_a_stable_key():
    k1 = build_freshness_aware_cache_key("q", doc_versions={})
    k2 = build_freshness_aware_cache_key("q", doc_versions={})
    assert k1 == k2
