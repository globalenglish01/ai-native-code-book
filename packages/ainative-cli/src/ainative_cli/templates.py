"""内置项目模板——每种模板声明所需的ainative-*包依赖 + 生成的起始代码。

模板本身只负责"生成什么文件、写什么内容"，不负责实际的文件系统IO——
那是`scaffold.py`的职责，这样模板可以独立于文件系统单独测试。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProjectTemplate:
    """一种项目类型的完整模板定义。"""

    name: str
    description: str
    packages: tuple[str, ...]
    """这种项目类型需要依赖的ainative-*包名（不含版本号，脚手架生成的
    pyproject.toml会引用真实发布的包，或者本地workspace路径——由调用方决定）。"""

    main_py: str
    """`main.py`的完整内容。"""


def _render_main(body: str) -> str:
    header = (
        '"""由 `ainative new` 生成的起始代码——自由修改，这只是一个可运行的起点。"""\n\n'
        "from __future__ import annotations\n\n"
        "import asyncio\n"
        "import sys\n\n"
        'if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):\n'
        '    sys.stdout.reconfigure(encoding="utf-8")\n\n'
    )
    return header + body


_CUSTOMER_SERVICE_MAIN = _render_main(
    '''from ainative_core.config import ProviderConfig
from ainative_core.protocols import GateCheck, GateResult, MemoryEntry, PromptVariant
from ainative_eval.gate import GREEN, RED, Gate
from ainative_guardrail.budget_middleware import ConsecutiveRetryGuardMiddleware, TokenBudgetMiddleware
from ainative_guardrail.limits import AgentLimits
from ainative_memory.store import InMemoryMemoryStore
from ainative_prompt.store import InMemoryPromptStore, load_prompt
from ainative_security.output_safety import OutputSafetyMiddleware
from ainative_security.pii_redaction import redact_pii_text


async def main() -> None:
    # 护栏：每个agent的运行参数（recursion/token/连续失败上限）
    limits = AgentLimits()
    limits.register("support_agent", recursion_limit=60, token_budget=200_000)

    retry_guard = ConsecutiveRetryGuardMiddleware(max_consecutive_errors=2)
    token_budget = TokenBudgetMiddleware(max_total_input_tokens=limits.token_budget("support_agent"))

    # Prompt：支持多变体A/B与粘性路由
    prompt_store = InMemoryPromptStore()
    await prompt_store.save_variant(
        "support_agent", "system_prompt",
        PromptVariant(variant="default", content="You are a helpful customer support agent.", traffic_pct=100, version=1),
    )
    system_prompt = await load_prompt(prompt_store, "support_agent", thread_id="demo-thread")

    # 安全：PII脱敏（持久化用户消息前）+ 输出安全扫描中间件（挂到agent的middleware列表里）
    safety_middleware = OutputSafetyMiddleware("support_agent")
    redacted_message = redact_pii_text("my phone number is 13812345678")

    # 记忆：跨会话记忆存储
    memory_store = InMemoryMemoryStore()
    await memory_store.append(MemoryEntry(owner_id="demo-user", sequence=0, content=redacted_message))

    # 治理：部署前门控
    def check_guardrail_wired() -> GateResult:
        wired = retry_guard is not None and token_budget is not None and safety_middleware is not None
        return GateResult(
            dimension="Guardrail", gating=True,
            status=GREEN if wired else RED,
            detail="retry guard, token budget, and output safety middleware are all wired",
        )

    gate = Gate([GateCheck(name="guardrail_wired", gating=True, check_fn=check_guardrail_wired)])
    decision = gate.run()

    config = ProviderConfig.from_env()

    print(f"system_prompt: {system_prompt}")
    print(f"redacted_message stored in memory: {redacted_message}")
    print(f"gate passed: {decision.passed}")
    print(f"default_model_id (from environment): {config.default_model_id}")

    # 真实项目在这里接入真实模型：
    # from ainative_core.model_factory import build_agent_model_with_fallback
    # model, fallback_mw = build_agent_model_with_fallback(config=config)


if __name__ == "__main__":
    asyncio.run(main())
'''
)

_BROWSER_AGENT_MAIN = _render_main(
    '''from ainative_guardrail.budget_middleware import ConsecutiveCallGuardMiddleware, MCPCallLimiterMiddleware
from ainative_mcp.config import build_mcp_config, build_safe_env
from ainative_security.output_safety import OutputSafetyMiddleware


async def main() -> None:
    # MCP：浏览器自动化server配置（stdio模式，env走安全白名单过滤）
    browser_mcp_config = build_mcp_config(
        "browser", "stdio",
        command="npx", args=["-y", "@playwright/mcp@latest"],
        env=build_safe_env(),
    )

    # 护栏：限制browser工具调用次数 + 探索类工具连续空转检测
    call_limiter = MCPCallLimiterMiddleware(per_tool_limit={
        "browser_snapshot": 20, "browser_click": 30, "browser_navigate": 15,
    })
    stall_guard = ConsecutiveCallGuardMiddleware(max_stall_calls=3)

    safety_middleware = OutputSafetyMiddleware("browser_agent")

    print(f"MCP config: {browser_mcp_config}")
    print("Guardrails and safety middleware ready — wire these into your agent's middleware list.")


if __name__ == "__main__":
    asyncio.run(main())
'''
)

_MULTI_AGENT_MAIN = _render_main(
    '''from ainative_a2a.dispatcher import Dispatcher
from ainative_a2a.registry import InMemoryAgentRegistry
from ainative_a2a.transport import InProcessTransport
from ainative_core.protocols import A2ATask, AgentCapability
from ainative_workflow.graph import Workflow, WorkflowNode


async def main() -> None:
    # A2A：能力注册 + 任务委派
    registry = InMemoryAgentRegistry()
    transport = InProcessTransport()
    registry.register("researcher_agent", AgentCapability(name="research", description="Gathers information"))

    async def researcher_handler(task: A2ATask) -> dict:
        return {"findings": f"researched: {task.payload.get('topic')}"}

    transport.register_handler("researcher_agent", researcher_handler)
    dispatcher = Dispatcher(registry, transport)

    # Workflow：多阶段编排，阶段间有依赖关系
    async def research_step(ctx: dict) -> dict:
        result = await dispatcher.delegate(capability="research", payload={"topic": ctx["topic"]}, sender_agent="orchestrator")
        return result.output

    def summarize_step(ctx: dict) -> str:
        return f"Summary of: {ctx['research_out']['findings']}"

    workflow = Workflow([
        WorkflowNode(name="research", fn=research_step, output_key="research_out"),
        WorkflowNode(name="summarize", fn=summarize_step, depends_on=("research",), output_key="summary"),
    ])

    run = await workflow.run({"topic": "AI Native frameworks"})
    print(f"workflow completed: {run.is_completed}")
    print(f"summary: {run.context.get('summary')}")


if __name__ == "__main__":
    asyncio.run(main())
'''
)

_MINIMAL_MAIN = _render_main(
    '''from ainative_core.config import ProviderConfig
from ainative_core.model_factory import build_agent_model_with_fallback


async def main() -> None:
    config = ProviderConfig.from_env()
    print("ProviderConfig loaded from environment.")
    print("Call build_agent_model_with_fallback(config=config) once ANTHROPIC_API_KEY is set.")


if __name__ == "__main__":
    asyncio.run(main())
'''
)


TEMPLATES: dict[str, ProjectTemplate] = {
    "customer-service": ProjectTemplate(
        name="customer-service",
        description="客服/支持类Agent：护栏+Prompt管理+PII脱敏+输出安全+记忆+治理门控",
        packages=(
            "ainative-core", "ainative-guardrail", "ainative-prompt",
            "ainative-security", "ainative-eval", "ainative-memory",
        ),
        main_py=_CUSTOMER_SERVICE_MAIN,
    ),
    "browser-agent": ProjectTemplate(
        name="browser-agent",
        description="浏览器自动化Agent：MCP配置+调用护栏+输出安全",
        packages=("ainative-core", "ainative-guardrail", "ainative-mcp", "ainative-security"),
        main_py=_BROWSER_AGENT_MAIN,
    ),
    "multi-agent": ProjectTemplate(
        name="multi-agent",
        description="多Agent协作系统：任务委派(A2A)+DAG工作流编排",
        packages=("ainative-core", "ainative-a2a", "ainative-workflow"),
        main_py=_MULTI_AGENT_MAIN,
    ),
    "minimal": ProjectTemplate(
        name="minimal",
        description="最小骨架：只有ainative-core，自己按需添加其他模块",
        packages=("ainative-core",),
        main_py=_MINIMAL_MAIN,
    ),
}


def get_template(name: str) -> ProjectTemplate:
    """按模板名查找模板定义；未找到时抛出`KeyError`，附带全部可用模板名。"""
    try:
        return TEMPLATES[name]
    except KeyError:
        available = ", ".join(sorted(TEMPLATES))
        raise KeyError(f"unknown project type '{name}'. Available types: {available}") from None
