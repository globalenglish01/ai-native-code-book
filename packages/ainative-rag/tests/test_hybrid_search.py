from __future__ import annotations

from ainative_rag.hybrid_search import RankedResult, ranked_results_from_scores, rrf_fuse


def test_document_appearing_in_both_lists_ranks_highest():
    bm25 = [RankedResult(doc_id="a", rank=1), RankedResult(doc_id="b", rank=2)]
    vector = [RankedResult(doc_id="b", rank=1), RankedResult(doc_id="c", rank=2)]

    fused = rrf_fuse(bm25, vector)

    assert fused[0][0] == "b"


def test_single_result_list_preserves_original_ranking():
    results = [RankedResult(doc_id="a", rank=1), RankedResult(doc_id="b", rank=2), RankedResult(doc_id="c", rank=3)]
    fused = rrf_fuse(results)
    assert [doc_id for doc_id, _ in fused] == ["a", "b", "c"]


def test_document_only_in_one_list_still_gets_a_score():
    fused = rrf_fuse([RankedResult(doc_id="only_here", rank=1)], [])
    assert dict(fused)["only_here"] > 0


def test_no_result_lists_produces_empty_fusion():
    assert rrf_fuse() == []


def test_lower_k_amplifies_rank_differences():
    results_a = [RankedResult(doc_id="top", rank=1)]
    results_b = [RankedResult(doc_id="bottom", rank=10)]
    fused_low_k = dict(rrf_fuse(results_a, results_b, k=1))
    fused_high_k = dict(rrf_fuse(results_a, results_b, k=1000))

    ratio_low_k = fused_low_k["top"] / fused_low_k["bottom"]
    ratio_high_k = fused_high_k["top"] / fused_high_k["bottom"]
    assert ratio_low_k > ratio_high_k


def test_ranked_results_from_scores_orders_by_descending_score():
    scores = {"x": 0.9, "y": 0.5, "z": 0.7}
    ranked = ranked_results_from_scores(scores)
    assert [r.doc_id for r in ranked] == ["x", "z", "y"]
    assert [r.rank for r in ranked] == [1, 2, 3]


def test_ranked_results_from_scores_handles_empty_input():
    assert ranked_results_from_scores({}) == []


def test_rrf_fuse_composes_with_ranked_results_from_scores():
    """The intended real-world composition: BM25 and vector search each
    produce raw scores, which get converted to ranks before fusion —
    verifying the two functions actually work together end to end."""
    bm25_scores = {"a": 15.2, "b": 8.1}
    vector_scores = {"b": 0.95, "c": 0.80}

    fused = rrf_fuse(ranked_results_from_scores(bm25_scores), ranked_results_from_scores(vector_scores))

    assert fused[0][0] == "b"  # appears in both, ranked #1 in each
