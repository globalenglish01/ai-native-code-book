"""产品示例：Agent自动化评测流水线（Eval Harness）。

真实产品形态：给agent上线前，用一批测试用例（prompt + 通过标准）跑一轮
自动化评测——每个用例用ensemble LLM-judge打分（同一份评判prompt独立
调用多次，取中位数，评判之间分歧过大时标记为"不确定，需要人工复核"，
而不是盲目相信单次判分），最后用治理Gate判定"这批测试的整体质量分数
是否达标"，只要有一个用例的判分因为judge之间分歧过大而不可信，就不能
无脑地把整体分数当成绿灯直接放行。

组合的包：ainative-prompt（ensemble judge）+ ainative-eval（Gate + 分数
到状态的映射规则）。
"""

from __future__ import annotations

from dataclasses import dataclass

from ainative_core.protocols import GateCheck, GateResult
from ainative_eval.gate import GREEN, RED, Gate, status_from_score
from ainative_prompt.judge import judge_response


@dataclass
class EvalCase:
    name: str
    prompt: str
    target_response: str
    expected_criteria: str


@dataclass
class EvalCaseResult:
    case_name: str
    score: float
    high_uncertainty: bool
    reasoning: str


class _FakeAIMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class _ScriptedJudgeModel:
    """按顺序返回预设评判结果的假judge模型，模拟`BaseChatModel.ainvoke`接口。"""

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self._calls = 0

    async def ainvoke(self, messages):
        response = self._responses[self._calls % len(self._responses)]
        self._calls += 1
        return _FakeAIMessage(response)


class EvalHarness:
    """跑一批`EvalCase`，每个用例做ensemble judge打分，供治理Gate把关整体质量。"""

    def __init__(self, judge_model, *, judge_count: int = 3) -> None:
        self.judge_model = judge_model
        self.judge_count = judge_count

    async def run_case(self, case: EvalCase) -> EvalCaseResult | None:
        verdict = await judge_response(
            self.judge_model, case.prompt, case.target_response, case.expected_criteria,
            judge_count=self.judge_count,
        )
        if not verdict["ok"]:
            return None
        return EvalCaseResult(
            case_name=case.name, score=verdict["score"],
            high_uncertainty=verdict["high_uncertainty"], reasoning=verdict["reasoning"],
        )

    async def run_suite(self, cases: list[EvalCase]) -> list[EvalCaseResult]:
        results = []
        for case in cases:
            result = await self.run_case(case)
            if result is not None:
                results.append(result)
        return results

    def deployment_gate(self, results: list[EvalCaseResult], *, green_min: float = 0.8, yellow_min: float = 0.5) -> Gate:
        def check_average_quality() -> GateResult:
            if not results:
                return GateResult(dimension="EvalQuality", gating=True, status=RED, detail="no eval results to judge")
            avg_score = sum(r.score for r in results) / len(results)
            status = status_from_score(avg_score, green_min=green_min, yellow_min=yellow_min)
            return GateResult(
                dimension="EvalQuality", gating=True, status=status,
                detail=f"average score {avg_score:.2f} across {len(results)} cases",
                evidence={"average_score": avg_score, "case_count": len(results)},
            )

        def check_no_uncertain_cases() -> GateResult:
            uncertain = [r.case_name for r in results if r.high_uncertainty]
            status = GREEN if not uncertain else RED
            detail = (
                "no case had high judge disagreement" if not uncertain
                else f"{len(uncertain)} case(s) need human review due to judge disagreement: {uncertain}"
            )
            return GateResult(dimension="JudgeConfidence", gating=True, status=status, detail=detail)

        return Gate([
            GateCheck(name="average_quality", gating=True, check_fn=check_average_quality),
            GateCheck(name="no_uncertain_cases", gating=True, check_fn=check_no_uncertain_cases),
        ])


async def main() -> None:
    import sys

    if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    cases = [
        EvalCase(
            name="greeting_politeness", prompt="Say hello to the customer",
            target_response="Hello! How can I help you today?", expected_criteria="Response is polite and welcoming",
        ),
        EvalCase(
            name="refund_policy_accuracy", prompt="What is the refund policy?",
            target_response="Refunds are available within 30 days of purchase with a receipt.",
            expected_criteria="Response accurately states the 30-day refund window",
        ),
        EvalCase(
            name="ambiguous_case", prompt="Is this feature production-ready?",
            target_response="It should mostly work but there might be edge cases.",
            expected_criteria="Response gives a clear, confident yes/no answer",
        ),
    ]

    # judge_count=3 per case; a clean, consistent case (all 3 judges agree it's great).
    clean_judge = _ScriptedJudgeModel(['{"score": 0.9, "reasoning": "polite and clear"}'])
    harness_clean = EvalHarness(clean_judge)
    clean_results = await harness_clean.run_suite(cases[:2])
    for r in clean_results:
        print(f"{r.case_name}: score={r.score}, high_uncertainty={r.high_uncertainty}")

    clean_decision = harness_clean.deployment_gate(clean_results).run()
    print(f"\nclean suite deployment gate passed: {clean_decision.passed}")

    # Now simulate the ambiguous case where the 3 judges strongly disagree.
    disagreeing_judge = _ScriptedJudgeModel([
        '{"score": 0.9, "reasoning": "confident enough"}',
        '{"score": 0.2, "reasoning": "too hedgy, not confident"}',
        '{"score": 0.6, "reasoning": "borderline"}',
    ])
    harness_ambiguous = EvalHarness(disagreeing_judge)
    ambiguous_result = await harness_ambiguous.run_case(cases[2])
    print(f"\n{ambiguous_result.case_name}: score={ambiguous_result.score}, high_uncertainty={ambiguous_result.high_uncertainty}")

    all_results = [*clean_results, ambiguous_result]
    full_decision = harness_ambiguous.deployment_gate(all_results).run()
    print(f"\nfull suite (including uncertain case) deployment gate passed: {full_decision.passed}")
    for blocker in full_decision.blockers:
        print(f"  blocker: {blocker}")


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
