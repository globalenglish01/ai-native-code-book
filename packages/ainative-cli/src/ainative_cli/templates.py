"""内置项目模板——每种模板声明所需的ainative-*包依赖 + 生成的起始代码。

模板本身只负责"生成什么文件、写什么内容"，不负责实际的文件系统IO——
那是`scaffold.py`的职责，这样模板可以独立于文件系统单独测试。
"""

# 让类型注解可以延迟解析，详见ainative_core里的详细解释，这里不再重复。
from __future__ import annotations

# dataclass——Python标准库提供的一个"装饰器"（写在类前面的`@xxx`），
# 作用是自动帮你生成一个类的`__init__`（构造函数）、`__repr__`（打印时
# 显示的样子）等样板代码，让你只需要声明"这个类有哪些字段"，不用自己
# 手写一大堆重复代码。下面`ProjectTemplate`就用到了它。
from dataclasses import dataclass


# `@dataclass(frozen=True)` 这行是装饰器语法，意思是：
# 1. dataclass——自动生成构造函数等样板代码（见上面的解释）。
# 2. frozen=True——"冻结"这个类的实例，一旦创建出来，字段就不能再被
#    修改（比如`template.name = "x"`会直接报错）。模板是"定义好之后
#    不该再变"的静态数据，冻结可以防止代码某处不小心改动了共享的
#    模板对象，影响到其他用到同一个模板的地方。
@dataclass(frozen=True)
class ProjectTemplate:
    """一种项目类型的完整模板定义。"""

    # 下面这些是这个类的"字段"（也叫"属性"）——每一行`字段名: 类型`的
    # 写法，dataclass会自动帮你变成构造函数里的一个参数。这几个字段都
    # 没写默认值，意味着构造`ProjectTemplate(...)`时必须把它们都传全。

    name: str
    # ↑ 模板名字（比如"minimal"、"customer-service"），会显示在
    #   `ainative list-types`的输出里，也是`--type`参数接受的值。

    description: str
    # ↑ 给人看的一句话描述，说明这个模板是做什么用的。

    packages: tuple[str, ...]
    """这种项目类型需要依赖的ainative-*包名（不含版本号，脚手架生成的
    pyproject.toml会引用真实发布的包，或者本地workspace路径——由调用方决定）。"""
    # ↑ `tuple[str, ...]`表示"一个元组，里面装若干个字符串"（省略号表示
    #   数量不固定）。之所以用元组（tuple）而不是列表（list），是因为
    #   元组一旦创建就不能增删元素——配合类本身的`frozen=True`，让整个
    #   模板对象（包括它内部的这个字段）从里到外都是真正不可变的，不会
    #   出现"表面上frozen，内部list却被偷偷改了"这种半吊子安全。

    main_py: str
    """`main.py`的完整内容。"""


def _render_main(body: str) -> str:
    # 这是一个"模块内部私有"的辅助函数——函数名前面的下划线`_`是Python
    # 的命名惯例，表示"这是本文件内部使用的实现细节，不建议外部代码
    # 直接导入使用"（不是语法强制，只是约定）。它的作用是把每个模板都
    # 需要的一段"公共头部代码"（未来类型注解声明、导入asyncio/sys、
    # Windows下让终端正确显示UTF-8字符的兼容代码）拼在调用方传入的
    # `body`（每个模板各自不同的业务逻辑代码）前面，避免每个模板都要
    # 重复手写这几行样板代码。
    header = (
        # 下面这一长串都是"字符串字面量"——用三引号`'''...'''`包裹的
        # 多行文本（也叫"三重引号字符串"），Python会把相邻写在一起、
        # 中间没有其他代码的多个字符串自动拼接成一个整体，效果等同于
        # 手写一个大大的`+`把它们连起来，只是这样排版更整齐、每行对应
        # 生成文件里的一行内容，更容易核对。
        '"""由 `ainative new` 生成的起始代码——自由修改，这只是一个可运行的起点。"""\n\n'
        "from __future__ import annotations\n\n"
        "import asyncio\n"
        "import sys\n\n"
        'if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):\n'
        '    sys.stdout.reconfigure(encoding="utf-8")\n\n'
    )
    # 把公共头部和调用方传入的具体业务代码拼接成完整的`main.py`文本内容。
    return header + body


# 下面几个变量是四种内置项目模板各自的`main.py`起始代码——都是先调用
# `_render_main(...)`拼上公共头部，再传入这个模板类型特有的示例代码。
# 之所以用三引号字符串整段整段地写"要生成的Python源码"，而不是用某种
# 模板引擎（比如Jinja2），是因为这些内容本身就是纯静态文本、不需要
# 按变量做任何替换——直接原样写成字符串常量最简单直接，也不需要给
# 这个包多引入一个模板引擎依赖。
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


# 这是整个模块最核心的"注册表"——一个字典（dict），key是模板名字符串
# （和`ProjectTemplate.name`保持一致），value是对应的`ProjectTemplate`
# 实例。`main.py`里的`--type`参数、`get_template()`查找、
# `ainative list-types`列出全部类型，全都是围绕这一个字典展开的，
# 新增一种项目模板只需要在这里加一条新的键值对。
# `dict[str, ProjectTemplate]`是这个变量的类型注解，表示"一个字典，
# key是字符串，value是ProjectTemplate实例"。
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
    # `try`/`except`是Python的"异常处理"语法：先尝试执行`try`块里的
    # 代码，如果执行过程中出错，程序不会直接崩溃退出，而是跳转去执行
    # 匹配的`except`块，做一些"补救"或"包装"处理。
    try:
        # 用方括号`[name]`直接按key去字典里取值——如果`name`不在字典里，
        # 这一行会抛出`KeyError`（Python字典查找失败时的标准异常类型）。
        return TEMPLATES[name]
    except KeyError:
        # 捕获到"模板名不存在"这个异常后，不是原样把这个信息量很少的
        # `KeyError`（只包含查找失败的key本身）往外抛，而是重新构造一个
        # 带有更多上下文（列出所有合法可选项）的新`KeyError`，方便调用方
        # （或者最终看到报错的人）知道"到底该输入哪些合法的模板名"。
        # `sorted(TEMPLATES)`——对字典直接`sorted()`会按字典的key（也就是
        # 模板名字符串）排序，返回一个排好序的名字列表；`", ".join(...)`
        # 把这个列表拼接成一句"a, b, c"这样的逗号分隔文本，方便展示。
        available = ", ".join(sorted(TEMPLATES))
        # `raise ... from None`——显式声明"这是一个全新构造的异常，不是
        # 在原来那个异常的基础上包装的"，这样Python打印异常堆栈时不会
        # 同时展示"在处理上面这个异常的过程中，又发生了下面这个异常"
        # 这种冗长的、对使用者没有额外帮助的两层追溯信息，只展示这个
        # 更友好的新异常本身。
        raise KeyError(f"unknown project type '{name}'. Available types: {available}") from None
