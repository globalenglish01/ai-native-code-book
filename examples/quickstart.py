"""端到端演示：组合ainative-core + 4个模块，跑一次完整的
"路由 + 护栏 + Prompt加载 + 安全检测 + 治理判定"流程。

全程不需要真实API Key、不需要安装Postgres/Redis/MongoDB——用一个假的
LLM模型模拟真实调用，验证"这套框架真的可以脱离任何具体基础设施独立
运行"这个核心设计目标。

运行方式（在D:\ai-native-code-book目录下）::

    uv run python examples/quickstart.py
"""

from __future__ import annotations

import asyncio
import sys

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

from ainative_core.protocols import GateCheck, GateResult, PromptVariant
from ainative_eval.gate import GREEN, RED, Gate
from ainative_guardrail.budget_middleware import ConsecutiveRetryGuardMiddleware, MCPCallLimiterMiddleware
from ainative_guardrail.limits import AgentLimits
from ainative_prompt.store import InMemoryPromptStore, load_prompt
from ainative_security.output_safety import OutputSafetyMiddleware
from ainative_security.pii_redaction import redact_pii_text
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest


def section(title: str) -> None:
    print(f"\n{'=' * 60}\n{title}\n{'=' * 60}")


async def main() -> None:
    # ── 1. ainative-guardrail：护栏参数 + 预算中间件 ──────────────────────
    section("1. ainative-guardrail — agent limits + budget middleware")

    limits = AgentLimits()
    limits.register("checkout_agent", recursion_limit=80, token_budget=300_000)
    print(f"checkout_agent recursion_limit = {limits.recursion_limit('checkout_agent')}")
    print(f"unknown_agent recursion_limit (default) = {limits.recursion_limit('unknown_agent')}")

    call_limiter = MCPCallLimiterMiddleware(per_tool_limit={"search_api": 2})
    retry_guard = ConsecutiveRetryGuardMiddleware(max_consecutive_errors=2)

    def flaky_tool_handler(request: ToolCallRequest) -> ToolMessage:
        return ToolMessage(content="boom", tool_call_id="call-1", name="search_api", status="error")

    request = ToolCallRequest(tool_call={"name": "search_api", "args": {}, "id": "call-1"}, tool=None, state={}, runtime=None)
    for attempt in range(4):
        short = retry_guard._maybe_short_circuit(request)
        if short is not None:
            print(f"attempt {attempt}: short-circuited by ConsecutiveRetryGuardMiddleware -> {short.content}")
            continue
        result = flaky_tool_handler(request)
        retry_guard._record_real_result(request, result)
        print(f"attempt {attempt}: tool call executed, status={result.status}")

    # ── 2. ainative-prompt：多变体加载 + 粘性路由 ─────────────────────────
    section("2. ainative-prompt — versioned prompts with sticky A/B routing")

    prompt_store = InMemoryPromptStore()
    await prompt_store.save_variant(
        "checkout_agent", "system_prompt",
        PromptVariant(variant="default", content="You are a helpful checkout assistant.", traffic_pct=70, version=1),
    )
    await prompt_store.save_variant(
        "checkout_agent", "system_prompt",
        PromptVariant(variant="concise", content="Be extremely concise.", traffic_pct=30, version=1),
    )

    prompt_a = await load_prompt(prompt_store, "checkout_agent", thread_id="user-42")
    prompt_b = await load_prompt(prompt_store, "checkout_agent", thread_id="user-42")
    print(f"thread user-42 routed to prompt: {prompt_a!r}")
    print(f"same thread again -> same variant (sticky): {prompt_a == prompt_b}")

    # ── 3. ainative-security：PII脱敏 + 输出安全扫描 ──────────────────────
    section("3. ainative-security — PII redaction + output safety scanning")

    user_message = "我的手机号是13812345678，请帮我处理订单。"
    redacted = redact_pii_text(user_message)
    print(f"original : {user_message}")
    print(f"redacted : {redacted}")

    safety_mw = OutputSafetyMiddleware("checkout_agent")

    class _FakeAIMessage:
        def __init__(self, content: str) -> None:
            self.content = content
            self.tool_calls = []
            self.additional_kwargs = {}

    class _FakeResponse:
        def __init__(self, output) -> None:
            self.output = output

    from langchain_core.messages import AIMessage

    malicious_response = _FakeResponse(AIMessage(content='api_key: "sk-abcdefghijklmnopqrstuvwxyz123456", enjoy!'))

    def handler(req):
        return malicious_response

    class _FakeRequest:
        messages: list = []

    cleaned = safety_mw.wrap_model_call(_FakeRequest(), handler)
    print(f"LLM output after OutputSafetyMiddleware: {cleaned.output.content}")

    # ── 4. ainative-eval：治理Gate判定 ────────────────────────────────────
    section("4. ainative-eval — governance gate decision")

    def check_guardrail_wired() -> GateResult:
        wired = call_limiter is not None and retry_guard is not None
        return GateResult(
            dimension="Guardrail",
            gating=True,
            status=GREEN if wired else RED,
            detail="MCPCallLimiterMiddleware + ConsecutiveRetryGuardMiddleware both instantiated",
        )

    def check_pii_redaction_works() -> GateResult:
        ok = "13812345678" not in redact_pii_text("13812345678")
        return GateResult(
            dimension="PII_Redaction",
            gating=True,
            status=GREEN if ok else RED,
            detail="redact_pii_text masks china_mobile_phone pattern",
        )

    gate = Gate([
        GateCheck(name="guardrail_wired", gating=True, check_fn=check_guardrail_wired),
        GateCheck(name="pii_redaction", gating=True, check_fn=check_pii_redaction_works),
    ])
    decision = gate.run()
    print(f"gate passed: {decision.passed}")
    for dim in decision.dimensions:
        print(f"  - {dim.dimension}: {dim.status} ({dim.detail})")

    section("Done — all 4 modules exercised with zero real API keys / databases.")


if __name__ == "__main__":
    asyncio.run(main())
