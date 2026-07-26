"""端到端演示：第二批模块——memory + mcp + workflow + a2a + CLI生成的项目骨架。

全程不需要真实API Key、不需要安装Postgres/Redis/MongoDB。

运行方式（在D:\\ai-native-framework目录下）::

    uv run python examples/quickstart_v2.py
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

from ainative_a2a.dispatcher import Dispatcher
from ainative_a2a.registry import InMemoryAgentRegistry
from ainative_a2a.transport import InProcessTransport
from ainative_cli.scaffold import scaffold_project
from ainative_cli.templates import get_template
from ainative_core.protocols import A2ATask, AgentCapability, MemoryEntry
from ainative_mcp.config import build_mcp_config, build_safe_env
from ainative_memory.checkpoint import CheckpointSaverFactory
from ainative_memory.history_budget import trim_history_to_budget
from ainative_memory.rendering import render_memory_entries
from ainative_memory.store import InMemoryMemoryStore
from ainative_workflow.graph import Workflow, WorkflowNode, WorkflowPaused
from ainative_workflow.hitl_policy import safe_timeout_decision


def section(title: str) -> None:
    print(f"\n{'=' * 60}\n{title}\n{'=' * 60}")


async def main() -> None:
    # ── 1. ainative-memory：长期记忆存储 + 滑动窗口渲染 + 历史token预算裁剪 ──
    section("1. ainative-memory — long-term memory + history token budget")

    memory_store = InMemoryMemoryStore()
    for i in range(5):
        await memory_store.append(MemoryEntry(owner_id="thread-1", sequence=i, content=f"turn {i} summary"))

    recent = await memory_store.load_recent("thread-1", max_items=3)
    print(render_memory_entries(recent))

    long_history = [{"role": "user", "content": "x" * 4000} for _ in range(20)]
    trimmed = trim_history_to_budget(long_history, max_tokens=3000)
    print(f"history trimmed from {len(long_history)} to {len(trimmed)} messages to fit token budget")

    build_attempts = {"count": 0}

    async def build_saver():
        build_attempts["count"] += 1
        return "in-memory-checkpoint-handle"

    checkpoint_factory = CheckpointSaverFactory(build_saver)
    saver = await checkpoint_factory.get()
    print(f"checkpoint saver: {saver} (built {build_attempts['count']} time(s))")

    # ── 2. ainative-mcp：MCP配置组装 + 环境变量白名单 ─────────────────────
    section("2. ainative-mcp — MCP server config assembly")

    mcp_config = build_mcp_config("browser", "stdio", command="npx", args=["-y", "@playwright/mcp"], env=build_safe_env())
    print(f"MCP config for 'browser' server: transport={mcp_config['browser']['transport']}, command={mcp_config['browser']['command']}")

    # ── 3. ainative-workflow：DAG编排 + HITL暂停/恢复 ─────────────────────
    section("3. ainative-workflow — DAG orchestration with a pause/resume gate")

    def draft_step(ctx):
        return f"draft for: {ctx['topic']}"

    def review_step(ctx):
        if not ctx.get("approved"):
            raise WorkflowPaused(payload={"reason": "needs human approval", "draft": ctx["draft_out"]})
        return "approved"

    def publish_step(ctx):
        return f"published: {ctx['draft_out']}"

    workflow = Workflow([
        WorkflowNode(name="draft", fn=draft_step, output_key="draft_out"),
        WorkflowNode(name="review", fn=review_step, depends_on=("draft",), output_key="review_out"),
        WorkflowNode(name="publish", fn=publish_step, depends_on=("review",), output_key="publish_out"),
    ])

    run = await workflow.run({"topic": "AI Native Framework"})
    print(f"paused at: {run.paused_at}, payload: {run.pause_payload}")

    # Simulate a timeout with no human response — safe default is always "reject".
    timeout_decision = safe_timeout_decision()
    print(f"if this timed out, the safe default decision would be: {timeout_decision}")

    # Simulate the human actually approving in time.
    run = await workflow.resume(run, resume_context={"approved": True})
    print(f"workflow completed: {run.is_completed}, result: {run.context.get('publish_out')}")

    # ── 4. ainative-a2a：能力发现 + 任务委派 ──────────────────────────────
    section("4. ainative-a2a — capability discovery + task delegation")

    registry = InMemoryAgentRegistry()
    transport = InProcessTransport()
    registry.register("translator_agent", AgentCapability(name="translate", description="Translates text"))

    async def translate_handler(task: A2ATask) -> dict:
        return {"translated": f"[translated] {task.payload['text']}"}

    transport.register_handler("translator_agent", translate_handler)
    dispatcher = Dispatcher(registry, transport)

    result = await dispatcher.delegate(capability="translate", payload={"text": "hello"}, sender_agent="orchestrator")
    print(f"delegation result: {result.status}, output: {result.output}")

    # ── 5. ainative-cli：一条命令生成新项目骨架 ───────────────────────────
    section("5. ainative-cli — scaffold a new project")

    with tempfile.TemporaryDirectory() as tmp_dir:
        target = Path(tmp_dir) / "demo-customer-service"
        template = get_template("customer-service")
        written = scaffold_project(target, "demo-customer-service", template)
        print(f"scaffolded {len(written)} files for a '{template.name}' project:")
        for path in written:
            print(f"  - {path.name}")

    section("Done — all 5 new modules (memory/mcp/workflow/a2a/cli) exercised end-to-end.")


if __name__ == "__main__":
    asyncio.run(main())
