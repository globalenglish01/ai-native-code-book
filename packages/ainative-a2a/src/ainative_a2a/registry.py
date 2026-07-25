"""`ainative_core.protocols.AgentRegistry`的内存版默认实现。"""

from __future__ import annotations

from ainative_core.protocols import AgentCapability


class InMemoryAgentRegistry:
    """按`(agent_name, capability_name)`存储能力声明的内存版注册表。"""

    def __init__(self) -> None:
        self._capabilities: dict[str, dict[str, AgentCapability]] = {}

    def register(self, agent_name: str, capability: AgentCapability) -> None:
        bucket = self._capabilities.setdefault(agent_name, {})
        bucket[capability.name] = capability

    def find_agents_for(self, capability_name: str) -> list[str]:
        return sorted(
            agent_name
            for agent_name, caps in self._capabilities.items()
            if capability_name in caps
        )

    def get_capability(self, agent_name: str, capability_name: str) -> AgentCapability | None:
        return self._capabilities.get(agent_name, {}).get(capability_name)

    def all_agents(self) -> list[str]:
        return sorted(self._capabilities.keys())

    def capabilities_of(self, agent_name: str) -> list[AgentCapability]:
        return list(self._capabilities.get(agent_name, {}).values())
