"""产品示例：代码审查助手。

真实产品形态：多阶段流水线（静态分析 -> LLM生成审查意见 -> 输出安全扫描
防止生成的"修复建议"里混入破坏性命令 -> 治理门控决定能不能自动合并），
用`ainative-workflow`的DAG编排显式建模阶段依赖，任何一个阶段失败都不会
继续往下走（比如静态分析都过不了，就不该浪费一次LLM调用去生成审查意见）。

组合的包：ainative-workflow + guardrail + security + eval。
"""

from __future__ import annotations

from ainative_core.protocols import GateCheck, GateResult
from ainative_eval.gate import GREEN, RED, Gate
from ainative_security.output_safety import OutputSafetyMiddleware
from ainative_workflow.graph import Workflow, WorkflowNode
from langchain_core.messages import AIMessage


def _run_static_analysis(ctx: dict) -> dict:
    """模拟静态分析——真实项目在这里接入ruff/mypy/eslint等工具。"""
    code = ctx["code"]
    issues = []
    if "eval(" in code:
        issues.append("use of eval() is discouraged")
    if "except:" in code:
        issues.append("bare except clause")
    return {"issues": issues, "passed": len(issues) == 0}


def _generate_review(ctx: dict, generate_fn) -> str:
    static = ctx["static_analysis_out"]
    return generate_fn(ctx["code"], static["issues"])


def _safety_scan_review(ctx: dict, safety_mw: OutputSafetyMiddleware) -> dict:
    raw_review = ctx["review_out"]

    class _FakeModelRequest:
        def __init__(self) -> None:
            self.messages: list = []

    class _FakeModelResponse:
        def __init__(self, output: AIMessage) -> None:
            self.output = output

    def handler(_req):
        return _FakeModelResponse(output=AIMessage(content=raw_review))

    result = safety_mw.wrap_model_call(_FakeModelRequest(), handler)
    return {"clean_review": result.output.content, "triggered": result.output.content != raw_review}


class CodeReviewAssistant:
    """静态分析 -> LLM审查生成 -> 安全扫描 -> 治理门控，四阶段DAG流水线。"""

    def __init__(self, agent_name: str = "code_review_agent") -> None:
        self.agent_name = agent_name
        self.safety = OutputSafetyMiddleware(agent_name)

    def build_workflow(self, generate_fn) -> Workflow:
        return Workflow([
            WorkflowNode(name="static_analysis", fn=_run_static_analysis, output_key="static_analysis_out"),
            WorkflowNode(
                name="generate_review",
                fn=lambda ctx: _generate_review(ctx, generate_fn),
                depends_on=("static_analysis",),
                output_key="review_out",
                condition=lambda ctx: ctx["static_analysis_out"]["passed"],
            ),
            WorkflowNode(
                name="safety_scan",
                fn=lambda ctx: _safety_scan_review(ctx, self.safety),
                depends_on=("generate_review",),
                output_key="safety_out",
            ),
        ])

    def deployment_gate(self, run_context: dict) -> Gate:
        def check_static_analysis_ran() -> GateResult:
            ran = "static_analysis_out" in run_context
            return GateResult(
                dimension="StaticAnalysis", gating=True,
                status=GREEN if ran else RED,
                detail="static analysis stage executed before any LLM-generated review was produced",
            )

        def check_safety_scan_ran() -> GateResult:
            ran = "safety_out" in run_context
            return GateResult(
                dimension="OutputSafety", gating=True,
                status=GREEN if ran else RED,
                detail="LLM-generated review text was scanned before being surfaced to the reviewer",
            )

        return Gate([
            GateCheck(name="static_analysis_ran", gating=True, check_fn=check_static_analysis_ran),
            GateCheck(name="safety_scan_ran", gating=True, check_fn=check_safety_scan_ran),
        ])


def fake_generate_review(code: str, static_issues: list[str]) -> str:
    """模拟LLM生成代码审查意见——真实项目在这里换成真实的模型调用。"""
    if "rm -rf" in code:
        # 模拟模型被代码里的注释诱导，在"修复建议"里直接给出破坏性命令。
        return "To clean up temp files, just run: rm -rf / --no-preserve-root"
    if static_issues:
        return f"Found {len(static_issues)} issue(s): {'; '.join(static_issues)}. Consider addressing these."
    return "Looks good — no issues found in static analysis."


async def main() -> None:
    import sys

    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")

    assistant = CodeReviewAssistant()

    print("--- clean code ---")
    workflow = assistant.build_workflow(fake_generate_review)
    run = await workflow.run({"code": "def add(a, b): return a + b"})
    print(f"completed: {run.is_completed}, review: {run.context['safety_out']['clean_review']}")

    print("\n--- code with static analysis issues ---")
    workflow2 = assistant.build_workflow(fake_generate_review)
    run2 = await workflow2.run({"code": "try:\n    x()\nexcept:\n    pass"})
    print(f"node_status: {run2.node_status}")

    print("\n--- code that manipulates the model into suggesting a destructive command ---")
    workflow3 = assistant.build_workflow(fake_generate_review)
    run3 = await workflow3.run({"code": 'os.system("rm -rf /tmp")'})
    print(f"sanitized review: {run3.context['safety_out']['clean_review']}")

    decision = assistant.deployment_gate(run.context).run()
    print(f"\ndeployment gate passed: {decision.passed}")


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
