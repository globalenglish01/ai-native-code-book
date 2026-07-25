from __future__ import annotations

import pytest
from ainative_a2a.dispatcher import DelegationLimitExceeded, Dispatcher
from ainative_a2a.registry import InMemoryAgentRegistry
from ainative_a2a.transport import InProcessTransport
from ainative_core.protocols import A2ATask, AgentCapability


def _make_dispatcher(*, max_delegation_depth: int = 5) -> tuple[Dispatcher, InProcessTransport, InMemoryAgentRegistry]:
    registry = InMemoryAgentRegistry()
    transport = InProcessTransport()
    dispatcher = Dispatcher(registry, transport, max_delegation_depth=max_delegation_depth)
    return dispatcher, transport, registry


@pytest.mark.asyncio
async def test_delegate_resolves_target_by_capability_and_returns_result():
    dispatcher, transport, registry = _make_dispatcher()
    registry.register("worker", AgentCapability(name="do_thing", description=""))

    async def handler(task: A2ATask) -> dict:
        return {"echo": task.payload}

    transport.register_handler("worker", handler)

    result = await dispatcher.delegate(capability="do_thing", payload={"a": 1}, sender_agent="caller")
    assert result.status == "success"
    assert result.output == {"echo": {"a": 1}}


@pytest.mark.asyncio
async def test_delegate_with_explicit_target_agent_skips_discovery():
    dispatcher, transport, _registry = _make_dispatcher()

    async def handler(task: A2ATask) -> dict:
        return {"ok": True}

    transport.register_handler("specific_worker", handler)
    result = await dispatcher.delegate(
        capability="anything", payload={}, sender_agent="caller", target_agent="specific_worker",
    )
    assert result.status == "success"


@pytest.mark.asyncio
async def test_delegate_raises_lookup_error_when_no_agent_has_capability():
    dispatcher, _, _ = _make_dispatcher()
    with pytest.raises(LookupError, match="no agent registered"):
        await dispatcher.delegate(capability="unknown_capability", payload={}, sender_agent="caller")


@pytest.mark.asyncio
async def test_delegate_raises_lookup_error_when_capability_is_ambiguous():
    dispatcher, _transport, registry = _make_dispatcher()
    registry.register("agent_a", AgentCapability(name="shared", description=""))
    registry.register("agent_b", AgentCapability(name="shared", description=""))

    with pytest.raises(LookupError, match="multiple agents"):
        await dispatcher.delegate(capability="shared", payload={}, sender_agent="caller")


@pytest.mark.asyncio
async def test_delegation_chain_records_sender_in_task():
    dispatcher, transport, registry = _make_dispatcher()
    registry.register("worker", AgentCapability(name="do_thing", description=""))

    captured_task = {}

    async def handler(task: A2ATask) -> dict:
        captured_task["task"] = task
        return {}

    transport.register_handler("worker", handler)
    await dispatcher.delegate(capability="do_thing", payload={}, sender_agent="orchestrator")
    assert captured_task["task"].delegation_chain == ("orchestrator",)


@pytest.mark.asyncio
async def test_cyclic_delegation_is_rejected():
    """A delegates to B; B tries to delegate back to A -> must be rejected."""
    dispatcher, _transport, registry = _make_dispatcher()
    registry.register("agent_a", AgentCapability(name="task_a", description=""))
    registry.register("agent_b", AgentCapability(name="task_b", description=""))

    with pytest.raises(DelegationLimitExceeded, match="cyclic"):
        await dispatcher.delegate(
            capability="task_a", payload={}, sender_agent="agent_b",
            target_agent="agent_a", delegation_chain=("agent_a", "agent_b"),
        )


@pytest.mark.asyncio
async def test_delegation_depth_limit_is_enforced():
    dispatcher, transport, registry = _make_dispatcher(max_delegation_depth=2)
    registry.register("worker", AgentCapability(name="do_thing", description=""))

    async def handler(task: A2ATask) -> dict:
        return {}

    transport.register_handler("worker", handler)

    # Chain already has 2 entries; adding sender makes 3, which exceeds max_delegation_depth=2.
    with pytest.raises(DelegationLimitExceeded, match="exceeds max_delegation_depth"):
        await dispatcher.delegate(
            capability="do_thing", payload={}, sender_agent="agent_c",
            delegation_chain=("agent_a", "agent_b"),
        )


@pytest.mark.asyncio
async def test_delegation_within_depth_limit_succeeds():
    dispatcher, transport, registry = _make_dispatcher(max_delegation_depth=3)
    registry.register("worker", AgentCapability(name="do_thing", description=""))

    async def handler(task: A2ATask) -> dict:
        return {"done": True}

    transport.register_handler("worker", handler)
    result = await dispatcher.delegate(
        capability="do_thing", payload={}, sender_agent="agent_c",
        delegation_chain=("agent_a", "agent_b"),
    )
    assert result.status == "success"


@pytest.mark.asyncio
async def test_multi_hop_delegation_chain_via_handler_re_delegation():
    """Simulates agent_a -> agent_b -> agent_c, each re-delegating using its own task's chain."""
    dispatcher, transport, registry = _make_dispatcher(max_delegation_depth=5)
    registry.register("agent_b", AgentCapability(name="step_b", description=""))
    registry.register("agent_c", AgentCapability(name="step_c", description=""))

    async def handler_c(task: A2ATask) -> dict:
        return {"final": True, "chain_seen": task.delegation_chain}

    async def handler_b(task: A2ATask) -> dict:
        result = await dispatcher.delegate(
            capability="step_c", payload={}, sender_agent="agent_b",
            delegation_chain=task.delegation_chain,
        )
        return result.output

    transport.register_handler("agent_c", handler_c)
    transport.register_handler("agent_b", handler_b)

    result = await dispatcher.delegate(capability="step_b", payload={}, sender_agent="agent_a")
    assert result.status == "success"
    assert result.output["final"] is True
    assert result.output["chain_seen"] == ("agent_a", "agent_b")
