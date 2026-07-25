"""产品示例：多智能体插件市场（Agent Plugin Marketplace）。

真实产品形态：一个协调者agent不直接实现所有能力，而是把任务按能力名称
委派给市场里注册的"插件agent"（这正是当下"Agent市场/插件生态"类产品的
核心编排模式）——每个插件agent对外调用工具时都被审计记录下来，运营方
需要在把某个插件放行给真实用户使用之前，用治理Gate检查它的历史调用
错误率是否在可接受范围内，同时防止插件之间互相委派形成失控的委派环。

组合的包：ainative-a2a + ainative-mcp + ainative-eval。
"""

from __future__ import annotations

import time

from ainative_a2a.dispatcher import DelegationLimitExceeded, Dispatcher
from ainative_a2a.registry import InMemoryAgentRegistry
from ainative_a2a.transport import InProcessTransport
from ainative_core.protocols import A2ATask, AgentCapability, GateCheck, GateResult
from ainative_eval.gate import GREEN, RED, Gate
from ainative_mcp.audit import InMemoryToolCallAuditLog, ToolCallRecord

MAX_ACCEPTABLE_ERROR_RATE = 0.2


class PluginMarketplace:
    """按能力发现插件agent，委派任务并审计每次插件调用，供治理Gate把关。"""

    def __init__(self) -> None:
        self.registry = InMemoryAgentRegistry()
        self.transport = InProcessTransport()
        self.dispatcher = Dispatcher(self.registry, self.transport)
        self.audit_log = InMemoryToolCallAuditLog()

    def install_plugin(self, agent_name: str, capability: AgentCapability, handler) -> None:
        """安装一个插件：登记它声明的能力，并把它的处理逻辑包装成会被审计的handler。"""
        self.registry.register(agent_name, capability)

        async def audited_handler(task: A2ATask) -> dict:
            start = time.monotonic()
            try:
                output = await handler(task)
            except Exception as exc:
                self.audit_log.record(ToolCallRecord(
                    tool_name=capability.name, agent_name=agent_name, status="error",
                    duration_ms=(time.monotonic() - start) * 1000,
                    input_summary=task.payload, error_message=str(exc),
                ))
                raise
            self.audit_log.record(ToolCallRecord(
                tool_name=capability.name, agent_name=agent_name, status="success",
                duration_ms=(time.monotonic() - start) * 1000,
                input_summary=task.payload, output_summary=output,
            ))
            return output

        self.transport.register_handler(agent_name, audited_handler)

    async def request(self, capability: str, payload: dict, *, sender_agent: str = "coordinator"):
        return await self.dispatcher.delegate(capability=capability, payload=payload, sender_agent=sender_agent)

    def deployment_gate_for(self, agent_name: str) -> Gate:
        """插件上线前的治理Gate：历史调用错误率必须在可接受范围内。"""

        def check_error_rate() -> GateResult:
            records = [r for r in self.audit_log.all() if r.agent_name == agent_name]
            if not records:
                return GateResult(
                    dimension="ErrorRate", gating=True, status=RED,
                    detail=f"plugin '{agent_name}' has no call history yet — cannot certify",
                )
            errors = sum(1 for r in records if r.status == "error")
            rate = errors / len(records)
            status = GREEN if rate <= MAX_ACCEPTABLE_ERROR_RATE else RED
            return GateResult(
                dimension="ErrorRate", gating=True, status=status,
                detail=f"{errors}/{len(records)} calls failed ({rate:.0%})",
                evidence={"error_rate": rate, "sample_size": len(records)},
            )

        return Gate([GateCheck(name="acceptable_error_rate", gating=True, check_fn=check_error_rate)])


async def main() -> None:
    import sys

    if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    marketplace = PluginMarketplace()

    async def weather_plugin(task: A2ATask) -> dict:
        return {"forecast": f"sunny in {task.payload.get('city', 'unknown')}"}

    async def flaky_translation_plugin(task: A2ATask) -> dict:
        if task.payload.get("text") == "trigger_error":
            raise RuntimeError("translation backend timed out")
        return {"translated": task.payload.get("text", "").upper()}

    marketplace.install_plugin(
        "weather_agent",
        AgentCapability(name="get_weather", description="Look up current weather for a city"),
        weather_plugin,
    )
    marketplace.install_plugin(
        "translation_agent",
        AgentCapability(name="translate_text", description="Translate text between languages"),
        flaky_translation_plugin,
    )

    weather_result = await marketplace.request("get_weather", {"city": "Tokyo"})
    print(f"weather plugin -> status={weather_result.status}, output={weather_result.output}")

    # Simulate a mix of successful and failing translation calls.
    for text in ["hello", "trigger_error", "world", "trigger_error", "goodbye"]:
        result = await marketplace.request("translate_text", {"text": text})
        print(f"translation plugin -> status={result.status}")

    weather_decision = marketplace.deployment_gate_for("weather_agent").run()
    print(f"\nweather_agent deployment gate passed: {weather_decision.passed}")

    translation_decision = marketplace.deployment_gate_for("translation_agent").run()
    print(f"translation_agent deployment gate passed: {translation_decision.passed}")
    for blocker in translation_decision.blockers:
        print(f"  blocker: {blocker}")

    # Cyclic delegation is rejected rather than looping forever.
    try:
        await marketplace.dispatcher.delegate(
            capability="get_weather", payload={}, sender_agent="weather_agent",
            delegation_chain=("coordinator", "weather_agent"),
        )
    except DelegationLimitExceeded as exc:
        print(f"\ncyclic delegation correctly rejected: {exc}")


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
