from __future__ import annotations

from ainative_rag.reranking import ScoredChunk, aggregate_chunk_scores, apply_rerank_if_enabled


def test_missing_score_defaults_to_lowest_not_highest():
    """The exact real-world bug this module fixes: a chunk that failed to
    get scored must not be treated as a perfect match — it must default
    to the lowest score, since "unverified relevance" should never
    outrank content that was actually evaluated and scored low."""
    chunks = [
        ScoredChunk(chunk_id="c1", doc_id="doc-a", rerank_score=0.9),
        ScoredChunk(chunk_id="c2", doc_id="doc-b", rerank_score=None),
        ScoredChunk(chunk_id="c3", doc_id="doc-c", rerank_score=0.1),
    ]
    result = aggregate_chunk_scores(chunks)
    assert result["doc-b"] == 0.0
    assert result["doc-b"] < result["doc-c"]


def test_missing_chunk_is_not_dropped_from_the_aggregation_entirely():
    """A related bug class: skipping unscored chunks entirely (rather than
    giving them a default score) makes the whole document vanish from the
    aggregated results, which is worse than a low score — it's silent
    data loss."""
    chunks = [ScoredChunk(chunk_id="c1", doc_id="doc-a", rerank_score=None)]
    result = aggregate_chunk_scores(chunks)
    assert "doc-a" in result


def test_aggregate_takes_the_best_score_per_document():
    chunks = [
        ScoredChunk(chunk_id="c1", doc_id="doc-a", rerank_score=0.3),
        ScoredChunk(chunk_id="c2", doc_id="doc-a", rerank_score=0.8),
    ]
    result = aggregate_chunk_scores(chunks)
    assert result["doc-a"] == 0.8


def test_custom_missing_score_is_respected():
    chunks = [ScoredChunk(chunk_id="c1", doc_id="doc-a", rerank_score=None)]
    result = aggregate_chunk_scores(chunks, missing_score=-1.0)
    assert result["doc-a"] == -1.0


def test_apply_rerank_disabled_falls_back_to_original_order():
    order = ["a", "b", "c"]
    assert apply_rerank_if_enabled(order, enabled=False, rerank_fn=lambda ids: {"c": 1.0}) == order


def test_apply_rerank_no_function_falls_back_to_original_order():
    order = ["a", "b", "c"]
    assert apply_rerank_if_enabled(order, enabled=True, rerank_fn=None) == order


def test_apply_rerank_failure_falls_back_to_original_order():
    """Disabled, unconfigured, and failing must all behave identically —
    not three different unpredictable outcomes."""
    order = ["a", "b", "c"]

    def failing_fn(ids: list[str]) -> dict[str, float]:
        raise RuntimeError("rerank service unavailable")

    assert apply_rerank_if_enabled(order, enabled=True, rerank_fn=failing_fn) == order


def test_apply_rerank_success_reorders_by_score():
    order = ["a", "b", "c"]
    scores = {"a": 0.1, "b": 0.9, "c": 0.5}
    result = apply_rerank_if_enabled(order, enabled=True, rerank_fn=lambda ids: scores)
    assert result == ["b", "c", "a"]


def test_apply_rerank_missing_score_for_some_docs_uses_default_low_score():
    order = ["a", "b"]
    scores = {"a": 0.9}  # "b" was never scored
    result = apply_rerank_if_enabled(order, enabled=True, rerank_fn=lambda ids: scores)
    assert result == ["a", "b"]
