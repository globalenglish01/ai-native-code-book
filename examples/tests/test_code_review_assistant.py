from __future__ import annotations

import pytest
from products.code_review_assistant import CodeReviewAssistant, fake_generate_review


@pytest.mark.asyncio
async def test_clean_code_runs_full_pipeline_to_completion():
    assistant = CodeReviewAssistant()
    workflow = assistant.build_workflow(fake_generate_review)
    run = await workflow.run({"code": "def add(a, b): return a + b"})
    assert run.is_completed
    assert run.context["safety_out"]["triggered"] is False


@pytest.mark.asyncio
async def test_failed_static_analysis_skips_review_and_safety_stages():
    assistant = CodeReviewAssistant()
    workflow = assistant.build_workflow(fake_generate_review)
    run = await workflow.run({"code": "eval(x)"})
    assert run.context["static_analysis_out"]["passed"] is False
    assert "generate_review" not in run.completed_order
    assert "safety_scan" not in run.completed_order


@pytest.mark.asyncio
async def test_malicious_generated_review_is_sanitized_before_reaching_reviewer():
    assistant = CodeReviewAssistant()
    workflow = assistant.build_workflow(fake_generate_review)
    run = await workflow.run({"code": 'os.system("rm -rf /tmp")'})
    safety_out = run.context["safety_out"]
    assert safety_out["triggered"] is True
    assert "rm -rf" not in safety_out["clean_review"]


@pytest.mark.asyncio
async def test_static_analysis_detects_bare_except():
    assistant = CodeReviewAssistant()
    workflow = assistant.build_workflow(fake_generate_review)
    run = await workflow.run({"code": "try:\n    x()\nexcept:\n    pass"})
    assert "bare except clause" in run.context["static_analysis_out"]["issues"]


def test_deployment_gate_fails_before_workflow_has_run():
    assistant = CodeReviewAssistant()
    decision = assistant.deployment_gate({}).run()
    assert decision.passed is False
    assert len(decision.blockers) == 2


@pytest.mark.asyncio
async def test_deployment_gate_passes_after_full_pipeline_run():
    assistant = CodeReviewAssistant()
    workflow = assistant.build_workflow(fake_generate_review)
    run = await workflow.run({"code": "def add(a, b): return a + b"})
    decision = assistant.deployment_gate(run.context).run()
    assert decision.passed is True
