from __future__ import annotations

import pytest
from ainative_rag.embedding_batch import EmbeddingCountMismatchError, embed_batch_with_accounting


async def _good_embed(texts: list[str]) -> list[list[float]]:
    return [[0.1, 0.2, 0.3] for _ in texts]


@pytest.mark.asyncio
async def test_embed_batch_returns_one_vector_per_text():
    result = await embed_batch_with_accounting(["hello", "world"], embed_fn=_good_embed)
    assert len(result.vectors) == 2


@pytest.mark.asyncio
async def test_embed_batch_empty_input_returns_empty_result():
    result = await embed_batch_with_accounting([], embed_fn=_good_embed)
    assert result.vectors == []
    assert result.truncated_count == 0


@pytest.mark.asyncio
async def test_mismatched_vector_count_raises_instead_of_silently_losing_data():
    """The exact real-world bug this module fixes: a provider returning
    fewer/more vectors than requested texts must be a hard, unmissable
    error — not a logged warning with a silently returned None that
    every caller forgets to check."""
    async def mismatched_embed(texts: list[str]) -> list[list[float]]:
        return [[0.1, 0.2]]  # always returns exactly 1, regardless of input size

    with pytest.raises(EmbeddingCountMismatchError) as exc_info:
        await embed_batch_with_accounting(["a", "b", "c"], embed_fn=mismatched_embed)

    assert exc_info.value.requested == 3
    assert exc_info.value.received == 1


@pytest.mark.asyncio
async def test_oversized_text_is_truncated_before_calling_embed_fn():
    captured_lengths = []

    async def capturing_embed(texts: list[str]) -> list[list[float]]:
        captured_lengths.extend(len(t) for t in texts)
        return [[0.1] for _ in texts]

    long_text = "x" * 1000
    result = await embed_batch_with_accounting([long_text], embed_fn=capturing_embed, max_chars_per_text=100)

    assert captured_lengths == [100]
    assert result.truncated_count == 1


@pytest.mark.asyncio
async def test_text_within_limit_is_not_counted_as_truncated():
    result = await embed_batch_with_accounting(["short"], embed_fn=_good_embed, max_chars_per_text=100)
    assert result.truncated_count == 0


@pytest.mark.asyncio
async def test_mixed_batch_only_counts_the_actually_truncated_texts():
    result = await embed_batch_with_accounting(
        ["short", "x" * 1000, "also short"], embed_fn=_good_embed, max_chars_per_text=100,
    )
    assert result.truncated_count == 1
