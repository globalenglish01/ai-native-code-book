"""产品示例：多Agent协作的研究团队。

真实产品形态：一个编排者（orchestrator）把"研究"和"事实核查"两个子任务
分别委派给专门的agent（A2A能力发现+任务委派+委派深度/循环保护），整个
流程用DAG工作流编排（research -> fact_check -> write -> human sign-off），
在"人工签发"这一步用HITL暂停，等待人工批准后才真正"发布"。

组合的包：ainative-a2a + ainative-workflow。
"""

from __future__ import annotations

from ainative_a2a.dispatcher import Dispatcher
from ainative_a2a.registry import InMemoryAgentRegistry
from ainative_a2a.transport import InProcessTransport
from ainative_core.protocols import A2ATask, AgentCapability
from ainative_workflow.graph import Workflow, WorkflowNode, WorkflowPaused
from ainative_workflow.hitl_policy import safe_timeout_decision


class ResearchTeam:
    """orchestrator通过A2A委派research/fact_check子任务，用workflow编排四个阶段。"""

    def __init__(self) -> None:
        self.registry = InMemoryAgentRegistry()
        self.transport = InProcessTransport()
        self.dispatcher = Dispatcher(self.registry, self.transport, max_delegation_depth=5)

        self.registry.register("researcher_agent", AgentCapability(name="research", description="Gathers raw information"))
        self.registry.register("fact_checker_agent", AgentCapability(name="fact_check", description="Verifies claims"))

        self.transport.register_handler("researcher_agent", self._researcher_handler)
        self.transport.register_handler("fact_checker_agent", self._fact_checker_handler)

    async def _researcher_handler(self, task: A2ATask) -> dict:
        topic = task.payload["topic"]
        return {"findings": f"Raw research notes about {topic}: [claim A, claim B, claim C]"}

    async def _fact_checker_handler(self, task: A2ATask) -> dict:
        findings = task.payload["findings"]
        # 模拟事实核查——真实项目在这里接入检索/交叉验证逻辑。
        flagged = "claim B" in findings  # 模拟发现一条可疑声明
        return {"verified_findings": findings.replace("claim B", "claim B [UNVERIFIED]"), "flagged": flagged}

    def build_workflow(self, *, require_human_signoff: bool = True) -> Workflow:
        async def research_step(ctx: dict) -> dict:
            result = await self.dispatcher.delegate(
                capability="research", payload={"topic": ctx["topic"]}, sender_agent="orchestrator",
            )
            if result.status != "success":
                raise RuntimeError(f"research delegation failed: {result.error_message}")
            return result.output

        async def fact_check_step(ctx: dict) -> dict:
            result = await self.dispatcher.delegate(
                capability="fact_check", payload={"findings": ctx["research_out"]["findings"]},
                sender_agent="orchestrator",
            )
            if result.status != "success":
                raise RuntimeError(f"fact-check delegation failed: {result.error_message}")
            return result.output

        def draft_step(ctx: dict) -> str:
            return f"DRAFT REPORT: {ctx['fact_check_out']['verified_findings']}"

        def signoff_step(ctx: dict) -> str:
            if require_human_signoff and not ctx.get("approved"):
                raise WorkflowPaused(payload={
                    "reason": "human sign-off required before publishing",
                    "draft": ctx["draft_out"],
                    "flagged_during_fact_check": ctx["fact_check_out"]["flagged"],
                })
            return ctx["draft_out"]

        return Workflow([
            WorkflowNode(name="research", fn=research_step, output_key="research_out"),
            WorkflowNode(name="fact_check", fn=fact_check_step, depends_on=("research",), output_key="fact_check_out"),
            WorkflowNode(name="draft", fn=draft_step, depends_on=("fact_check",), output_key="draft_out"),
            WorkflowNode(name="signoff", fn=signoff_step, depends_on=("draft",), output_key="published_out"),
        ])


async def main() -> None:
    import sys

    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")

    team = ResearchTeam()
    workflow = team.build_workflow()

    run = await workflow.run({"topic": "AI Native Frameworks"})
    print(f"paused at: {run.paused_at}")
    print(f"pause payload: {run.pause_payload}")

    if run.is_paused and not run.pause_payload.get("flagged_during_fact_check"):
        run = await workflow.resume(run, resume_context={"approved": True})
    else:
        print(f"escalating for manual review instead of auto-approving; "
              f"if this timed out, safe default would be: {safe_timeout_decision()}")
        run = await workflow.resume(run, resume_context={"approved": True})  # simulate human approving after review

    print(f"\nfinal report: {run.context.get('published_out')}")
    print(f"workflow completed: {run.is_completed}")


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
