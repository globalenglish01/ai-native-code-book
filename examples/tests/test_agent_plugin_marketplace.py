from __future__ import annotations

import pytest
from ainative_a2a.dispatcher import DelegationLimitExceeded
from ainative_core.protocols import A2ATask, AgentCapability
from products.agent_plugin_marketplace import PluginMarketplace


async def _install_weather(marketplace: PluginMarketplace) -> None:
    async def handler(task: A2ATask) -> dict:
        return {"forecast": f"sunny in {task.payload.get('city')}"}

    marketplace.install_plugin(
        "weather_agent",
        AgentCapability(name="get_weather", description="weather lookup"),
        handler,
    )


@pytest.mark.asyncio
async def test_successful_plugin_call_is_dispatched_and_audited():
    marketplace = PluginMarketplace()
    await _install_weather(marketplace)

    result = await marketplace.request("get_weather", {"city": "Tokyo"})

    assert result.status == "success"
    assert result.output == {"forecast": "sunny in Tokyo"}
    records = marketplace.audit_log.for_tool("get_weather")
    assert len(records) == 1
    assert records[0].status == "success"
    assert records[0].agent_name == "weather_agent"


@pytest.mark.asyncio
async def test_failing_plugin_call_is_recorded_as_error_and_reraised():
    marketplace = PluginMarketplace()

    async def failing_handler(_task: A2ATask) -> dict:
        raise RuntimeError("boom")

    marketplace.install_plugin(
        "broken_agent", AgentCapability(name="do_thing", description="fails"), failing_handler,
    )

    result = await marketplace.request("do_thing", {})

    assert result.status == "error"
    records = marketplace.audit_log.for_tool("do_thing")
    assert len(records) == 1
    assert records[0].status == "error"
    assert records[0].error_message == "boom"


@pytest.mark.asyncio
async def test_deployment_gate_fails_with_no_call_history():
    marketplace = PluginMarketplace()
    await _install_weather(marketplace)

    decision = marketplace.deployment_gate_for("weather_agent").run()

    assert decision.passed is False
    assert "no call history" in decision.blockers[0]


@pytest.mark.asyncio
async def test_deployment_gate_passes_below_error_threshold():
    marketplace = PluginMarketplace()
    await _install_weather(marketplace)

    for _ in range(9):
        await marketplace.request("get_weather", {"city": "Paris"})

    decision = marketplace.deployment_gate_for("weather_agent").run()
    assert decision.passed is True


@pytest.mark.asyncio
async def test_deployment_gate_fails_above_error_threshold():
    marketplace = PluginMarketplace()
    call_count = 0

    async def sometimes_fails(_task: A2ATask) -> dict:
        nonlocal call_count
        call_count += 1
        if call_count <= 3:
            raise RuntimeError("failure")
        return {}

    marketplace.install_plugin(
        "unstable_agent", AgentCapability(name="do_thing", description="unstable"), sometimes_fails,
    )

    for _ in range(5):
        await marketplace.request("do_thing", {})

    decision = marketplace.deployment_gate_for("unstable_agent").run()
    assert decision.passed is False


@pytest.mark.asyncio
async def test_cyclic_delegation_is_rejected_not_infinite_looped():
    marketplace = PluginMarketplace()
    await _install_weather(marketplace)

    with pytest.raises(DelegationLimitExceeded):
        await marketplace.dispatcher.delegate(
            capability="get_weather", payload={}, sender_agent="weather_agent",
            delegation_chain=("coordinator", "weather_agent"),
        )
