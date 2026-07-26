from __future__ import annotations

import pytest
from ainative_rag.chunking import (
    InvalidChunkingParametersError,
    chunk_by_semantic_boundary,
    chunk_by_token_estimate,
)


def test_chunk_by_token_estimate_produces_multiple_chunks_for_long_text():
    text = "A" * 5000
    chunks = chunk_by_token_estimate(text, chunk_size=100, overlap_ratio=0.15)
    assert len(chunks) > 1


def test_chunks_cover_the_entire_text_without_gaps():
    text = "A" * 5000
    chunks = chunk_by_token_estimate(text, chunk_size=100, overlap_ratio=0.15)
    assert chunks[-1].end == len(text)


def test_adjacent_chunks_overlap():
    text = "A" * 5000
    chunks = chunk_by_token_estimate(text, chunk_size=100, overlap_ratio=0.15)
    assert chunks[1].start < chunks[0].end


def test_zero_overlap_produces_non_overlapping_chunks():
    text = "A" * 1000
    chunks = chunk_by_token_estimate(text, chunk_size=100, overlap_ratio=0.0)
    assert chunks[1].start == chunks[0].end


def test_short_text_produces_a_single_chunk():
    chunks = chunk_by_token_estimate("short text", chunk_size=1000, overlap_ratio=0.15)
    assert len(chunks) == 1
    assert chunks[0].text == "short text"


def test_empty_text_produces_no_chunks():
    assert chunk_by_token_estimate("", chunk_size=100) == []


def test_overlap_ratio_of_one_is_rejected():
    """The real-world bug class this guards against: overlap_ratio >= 1
    makes the step size <= 0, which Python's range() silently turns into
    an empty sequence — "chunking succeeded" but zero chunks were
    produced, a look-correct-but-totally-wrong failure mode."""
    with pytest.raises(InvalidChunkingParametersError):
        chunk_by_token_estimate("some text", chunk_size=100, overlap_ratio=1.0)


def test_overlap_ratio_above_one_is_rejected():
    with pytest.raises(InvalidChunkingParametersError):
        chunk_by_token_estimate("some text", chunk_size=100, overlap_ratio=1.5)


def test_negative_overlap_ratio_is_rejected():
    with pytest.raises(InvalidChunkingParametersError):
        chunk_by_token_estimate("some text", chunk_size=100, overlap_ratio=-0.1)


def test_non_positive_chunk_size_is_rejected():
    with pytest.raises(InvalidChunkingParametersError):
        chunk_by_token_estimate("some text", chunk_size=0)


def test_chunk_by_semantic_boundary_splits_on_paragraphs():
    text = "First paragraph.\n\nSecond paragraph.\n\nThird paragraph."
    chunks = chunk_by_semantic_boundary(text, chunk_size=1000)
    assert len(chunks) == 3
    assert chunks[0].text == "First paragraph."


def test_chunk_by_semantic_boundary_falls_back_to_mechanical_split_for_oversized_paragraph():
    """A single paragraph that itself exceeds chunk_size must not silently
    become one oversized "chunk" — it must be mechanically re-split."""
    oversized_paragraph = "word " * 2000  # far exceeds a small chunk_size
    text = f"short paragraph.\n\n{oversized_paragraph}"
    chunks = chunk_by_semantic_boundary(text, chunk_size=50, overlap_ratio=0.1)
    assert all(len(c.text) <= 50 * 4 for c in chunks)
    assert len(chunks) > 2


def test_chunk_by_semantic_boundary_skips_empty_paragraphs():
    text = "first\n\n\n\nsecond"
    chunks = chunk_by_semantic_boundary(text, chunk_size=1000)
    assert [c.text for c in chunks] == ["first", "second"]
