"""产品示例：文档发布流水线（起草 -> 审阅 -> 人工卡点 -> 发布）。

真实产品形态：内容生成类产品常见的多阶段DAG——起草阶段生成内容，
校验阶段做自动规则检查，超出自动批准范围的内容（比如篇幅过长、
包含高风险关键词）必须暂停流程、等人工审批后才能继续发布，而不是
自动放行。同时每个阶段的连续失败次数要有上限（用guardrail的
`AgentLimits`按"阶段名"而不是"agent名"复用同一套护栏参数设计），
避免某个不稳定阶段无限重试耗尽资源。

组合的包：ainative-workflow + ainative-guardrail。
"""

from __future__ import annotations

from ainative_guardrail.limits import AgentLimits
from ainative_workflow.graph import Workflow, WorkflowNode, WorkflowPaused

AUTO_APPROVE_MAX_LENGTH = 280
HIGH_RISK_KEYWORDS = ("guaranteed returns", "risk-free")


class DocumentPublishingPipeline:
    """三阶段DAG：起草 -> 审阅（可能暂停等人工审批）-> 发布。"""

    def __init__(self) -> None:
        self.stage_limits = AgentLimits()
        self.stage_limits.register("review", max_consecutive_errors=1)
        self._retry_counts: dict[str, int] = {}

        self.workflow = Workflow([
            WorkflowNode(name="draft", fn=self._draft, output_key="draft_text"),
            WorkflowNode(name="review", fn=self._review, depends_on=("draft",), output_key="review_verdict"),
            WorkflowNode(
                name="publish", fn=self._publish, depends_on=("review",), output_key="published",
                condition=lambda ctx: ctx.get("review_verdict") == "auto_approved",
            ),
        ])

    def _draft(self, context: dict) -> str:
        return context["requested_content"]

    def _review(self, context: dict) -> str:
        if context.get("human_approved"):
            return "auto_approved"

        text = context["draft_text"]
        if len(text) > AUTO_APPROVE_MAX_LENGTH or any(kw in text.lower() for kw in HIGH_RISK_KEYWORDS):
            raise WorkflowPaused(payload={
                "reason": "content exceeds auto-approval bounds, needs human review",
                "text": text,
                "length": len(text),
            })
        return "auto_approved"

    def _publish(self, context: dict) -> bool:
        return True

    async def submit(self, requested_content: str):
        return await self.workflow.run({"requested_content": requested_content})

    async def approve_and_resume(self, run):
        """人工审批通过后调用——从暂停点恢复；`_review`节点重新执行时会看到
        `human_approved=True`，不再重复触发暂停，直接判定为可发布。"""
        return await self.workflow.resume(run, resume_context={"human_approved": True})


async def main() -> None:
    import sys

    if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    pipeline = DocumentPublishingPipeline()

    # Case 1: short, low-risk content auto-publishes without any human involvement.
    run1 = await pipeline.submit("Our new feature ships next week. Feedback welcome!")
    print(f"case 1 (short content) -> is_completed={run1.is_completed}, published={run1.context.get('published')}")

    # Case 2: risky content pauses for human review instead of auto-publishing.
    run2 = await pipeline.submit("Invest now for guaranteed returns, risk-free profits await!")
    print(f"\ncase 2 (risky content) -> is_paused={run2.is_paused}, paused_at={run2.paused_at}")
    print(f"  pause payload reason: {run2.pause_payload['reason']}")

    # A human reviewer approves it; the pipeline resumes and completes.
    resumed = await pipeline.approve_and_resume(run2)
    print(f"  after human approval -> is_completed={resumed.is_completed}, published={resumed.context.get('published')}")

    print(f"\nreview stage max_consecutive_errors = {pipeline.stage_limits.max_consecutive_errors('review')}")


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
