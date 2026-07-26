"""`ainative_core.protocols.AgentRegistry`的内存版默认实现。"""

from __future__ import annotations

import copy

from ainative_core.protocols import AgentCapability


class InMemoryAgentRegistry:
    """按`(agent_name, capability_name)`存储能力声明的内存版注册表。

    `AgentCapability`本身是frozen dataclass，但它的`input_schema`/
    `output_schema`字段是普通可变dict——"frozen"只阻止字段被重新赋值，
    不阻止字段内容被原地修改。`register()`/`get_capability()`/
    `capabilities_of()`都对`AgentCapability`做深拷贝，而不是直接存储/
    返回调用方传入或即将拿到的原始对象：否则调用方读取一个能力的
    schema用于检查/日志，顺手原地修改了那个dict（或者注册前后复用同一份
    schema字典并修改它），会静默污染注册表内部状态，所有后续查询都会
    看到被污染的结果，且没有任何报错信号——这与本会话中发现的
    `merge_mcp_configs`别名bug是同一类问题。
    """

    def __init__(self) -> None:
        self._capabilities: dict[str, dict[str, AgentCapability]] = {}

    def register(self, agent_name: str, capability: AgentCapability) -> None:
        bucket = self._capabilities.setdefault(agent_name, {})
        bucket[capability.name] = copy.deepcopy(capability)

    def find_agents_for(self, capability_name: str) -> list[str]:
        return sorted(
            agent_name
            for agent_name, caps in self._capabilities.items()
            if capability_name in caps
        )

    def get_capability(self, agent_name: str, capability_name: str) -> AgentCapability | None:
        found = self._capabilities.get(agent_name, {}).get(capability_name)
        return copy.deepcopy(found) if found is not None else None

    def all_agents(self) -> list[str]:
        return sorted(self._capabilities.keys())

    def capabilities_of(self, agent_name: str) -> list[AgentCapability]:
        return [copy.deepcopy(cap) for cap in self._capabilities.get(agent_name, {}).values()]
