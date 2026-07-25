from __future__ import annotations

from ainative_core.protocols import AgentCapability
from ainative_a2a.registry import InMemoryAgentRegistry


def test_register_and_find_agents_for_capability():
    registry = InMemoryAgentRegistry()
    registry.register("planner_agent", AgentCapability(name="make_plan", description="Creates a plan"))
    assert registry.find_agents_for("make_plan") == ["planner_agent"]


def test_find_agents_for_unknown_capability_returns_empty_list():
    registry = InMemoryAgentRegistry()
    assert registry.find_agents_for("nonexistent") == []


def test_multiple_agents_can_share_a_capability():
    registry = InMemoryAgentRegistry()
    registry.register("agent_a", AgentCapability(name="summarize", description="A"))
    registry.register("agent_b", AgentCapability(name="summarize", description="B"))
    assert registry.find_agents_for("summarize") == ["agent_a", "agent_b"]


def test_get_capability_returns_none_when_not_registered():
    registry = InMemoryAgentRegistry()
    assert registry.get_capability("agent_a", "unknown") is None


def test_get_capability_returns_registered_capability():
    cap = AgentCapability(name="make_plan", description="Creates a plan", input_schema={"goal": "string"})
    registry = InMemoryAgentRegistry()
    registry.register("planner_agent", cap)
    assert registry.get_capability("planner_agent", "make_plan") == cap


def test_all_agents_lists_registered_agent_names():
    registry = InMemoryAgentRegistry()
    registry.register("agent_b", AgentCapability(name="x", description=""))
    registry.register("agent_a", AgentCapability(name="y", description=""))
    assert registry.all_agents() == ["agent_a", "agent_b"]


def test_capabilities_of_returns_all_capabilities_for_an_agent():
    registry = InMemoryAgentRegistry()
    registry.register("agent_a", AgentCapability(name="x", description=""))
    registry.register("agent_a", AgentCapability(name="y", description=""))
    caps = registry.capabilities_of("agent_a")
    assert {c.name for c in caps} == {"x", "y"}
