from __future__ import annotations

import pytest
from products.eval_harness import EvalCase, EvalHarness, _ScriptedJudgeModel


def _case(name: str = "case1") -> EvalCase:
    return EvalCase(name=name, prompt="p", target_response="r", expected_criteria="c")


@pytest.mark.asyncio
async def test_run_case_returns_median_score_when_judges_agree():
    judge = _ScriptedJudgeModel(['{"score": 0.8, "reasoning": "good"}'])
    harness = EvalHarness(judge, judge_count=3)

    result = await harness.run_case(_case())

    assert result.score == 0.8
    assert result.high_uncertainty is False


@pytest.mark.asyncio
async def test_run_case_flags_high_uncertainty_when_judges_disagree():
    judge = _ScriptedJudgeModel([
        '{"score": 0.9, "reasoning": "great"}',
        '{"score": 0.1, "reasoning": "bad"}',
        '{"score": 0.5, "reasoning": "meh"}',
    ])
    harness = EvalHarness(judge, judge_count=3)

    result = await harness.run_case(_case())

    assert result.high_uncertainty is True


@pytest.mark.asyncio
async def test_run_case_returns_none_when_all_judge_calls_fail_to_parse():
    judge = _ScriptedJudgeModel(["not valid json at all"])
    harness = EvalHarness(judge, judge_count=2)

    result = await harness.run_case(_case())

    assert result is None


@pytest.mark.asyncio
async def test_run_suite_skips_unparseable_cases_and_keeps_valid_ones():
    judge = _ScriptedJudgeModel(['{"score": 0.7, "reasoning": "ok"}'])
    harness = EvalHarness(judge, judge_count=1)

    results = await harness.run_suite([_case("a"), _case("b")])

    assert [r.case_name for r in results] == ["a", "b"]


@pytest.mark.asyncio
async def test_deployment_gate_passes_when_scores_high_and_no_uncertainty():
    judge = _ScriptedJudgeModel(['{"score": 0.9, "reasoning": "great"}'])
    harness = EvalHarness(judge, judge_count=3)
    results = await harness.run_suite([_case("a"), _case("b")])

    decision = harness.deployment_gate(results).run()

    assert decision.passed is True


@pytest.mark.asyncio
async def test_deployment_gate_fails_when_a_case_has_high_uncertainty():
    judge = _ScriptedJudgeModel([
        '{"score": 0.9, "reasoning": "great"}',
        '{"score": 0.1, "reasoning": "bad"}',
        '{"score": 0.5, "reasoning": "meh"}',
    ])
    harness = EvalHarness(judge, judge_count=3)
    results = await harness.run_suite([_case("uncertain_case")])

    decision = harness.deployment_gate(results).run()

    assert decision.passed is False
    assert any("uncertain_case" in blocker for blocker in decision.blockers)


def test_deployment_gate_fails_with_no_results():
    judge = _ScriptedJudgeModel(['{"score": 0.9}'])
    harness = EvalHarness(judge)

    decision = harness.deployment_gate([]).run()

    assert decision.passed is False
