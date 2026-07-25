from __future__ import annotations

import pytest
from products.document_publishing_pipeline import DocumentPublishingPipeline


@pytest.mark.asyncio
async def test_short_low_risk_content_auto_publishes():
    pipeline = DocumentPublishingPipeline()
    run = await pipeline.submit("Our new feature ships next week. Feedback welcome!")

    assert run.is_completed
    assert run.context["published"] is True


@pytest.mark.asyncio
async def test_risky_content_pauses_instead_of_auto_publishing():
    pipeline = DocumentPublishingPipeline()
    run = await pipeline.submit("Invest now for guaranteed returns, risk-free profits await!")

    assert run.is_paused
    assert run.paused_at == "review"
    assert "human review" in run.pause_payload["reason"]
    assert "published" not in run.context


@pytest.mark.asyncio
async def test_overly_long_content_pauses_even_without_risky_keywords():
    pipeline = DocumentPublishingPipeline()
    run = await pipeline.submit("word " * 100)

    assert run.is_paused
    assert run.paused_at == "review"


@pytest.mark.asyncio
async def test_human_approval_resumes_and_completes_publication():
    pipeline = DocumentPublishingPipeline()
    run = await pipeline.submit("Invest now for guaranteed returns, risk-free profits await!")
    assert run.is_paused

    resumed = await pipeline.approve_and_resume(run)

    assert resumed.is_completed
    assert resumed.context["published"] is True


@pytest.mark.asyncio
async def test_resuming_does_not_rerun_the_draft_stage():
    pipeline = DocumentPublishingPipeline()
    run = await pipeline.submit("guaranteed returns")
    assert run.node_status["draft"].value == "completed"

    resumed = await pipeline.approve_and_resume(run)

    assert resumed.node_status["draft"].value == "completed"
    assert resumed.context["draft_text"] == "guaranteed returns"


def test_review_stage_has_a_tighter_consecutive_error_limit_than_default():
    pipeline = DocumentPublishingPipeline()
    assert pipeline.stage_limits.max_consecutive_errors("review") == 1
    assert pipeline.stage_limits.max_consecutive_errors("draft") == 2
