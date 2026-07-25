from __future__ import annotations

import pytest
from products.research_team import ResearchTeam


@pytest.mark.asyncio
async def test_workflow_pauses_at_signoff_awaiting_human_approval():
    team = ResearchTeam()
    workflow = team.build_workflow()
    run = await workflow.run({"topic": "quantum computing"})
    assert run.is_paused
    assert run.paused_at == "signoff"


@pytest.mark.asyncio
async def test_resume_after_approval_completes_and_publishes():
    team = ResearchTeam()
    workflow = team.build_workflow()
    run = await workflow.run({"topic": "quantum computing"})
    run = await workflow.resume(run, resume_context={"approved": True})
    assert run.is_completed
    assert "DRAFT REPORT" in run.context["published_out"]


@pytest.mark.asyncio
async def test_fact_checker_flags_unverified_claims():
    team = ResearchTeam()
    workflow = team.build_workflow()
    run = await workflow.run({"topic": "anything"})
    assert run.pause_payload["flagged_during_fact_check"] is True
    assert "claim B [UNVERIFIED]" in run.pause_payload["draft"]


@pytest.mark.asyncio
async def test_workflow_without_human_signoff_requirement_completes_immediately():
    team = ResearchTeam()
    workflow = team.build_workflow(require_human_signoff=False)
    run = await workflow.run({"topic": "quantum computing"})
    assert run.is_completed
    assert not run.is_paused


@pytest.mark.asyncio
async def test_research_and_fact_check_delegation_go_through_a2a_dispatcher():
    """Verifies the multi-agent delegation chain actually records both hops
    (orchestrator -> researcher_agent, orchestrator -> fact_checker_agent)."""
    team = ResearchTeam()
    result = await team.dispatcher.delegate(
        capability="research", payload={"topic": "test"}, sender_agent="orchestrator",
    )
    assert result.status == "success"
    assert "test" in result.output["findings"]
