"""跨Agent护栏参数的集中管理。

改造自真实项目里"把散落在各agent.py里的recursion_limit/token预算/连续失败
上限集中到一个模块"的做法，泛化成一个可以按需注册任意agent名称的类，而不是
硬编码某个项目专属的agent列表。

真实项目里这类数值大多是历史经验值而非严谨评估的结果——保留这个诚实态度：
`AgentLimits`不假装"默认值"是放之四海而皆准的最优解，只是一个安全的起点，
调用方应该在有真实运行数据后按需覆盖。
"""

from __future__ import annotations

from dataclasses import dataclass, field

DEFAULT_RECURSION_LIMIT = 60
DEFAULT_TOKEN_BUDGET = 200_000
DEFAULT_MAX_CONSECUTIVE_ERRORS = 2


@dataclass(frozen=True)
class AgentLimit:
    """单个agent的一组护栏参数。"""

    recursion_limit: int = DEFAULT_RECURSION_LIMIT
    token_budget: int = DEFAULT_TOKEN_BUDGET
    max_consecutive_errors: int = DEFAULT_MAX_CONSECUTIVE_ERRORS


@dataclass
class AgentLimits:
    """按agent名称注册/查询护栏参数，未注册的agent回退到默认值。

    用法::

        limits = AgentLimits()
        limits.register("checkout_agent", recursion_limit=80, token_budget=300_000)
        limits.recursion_limit("checkout_agent")   # 80
        limits.recursion_limit("unknown_agent")    # 60（默认值）
    """

    _entries: dict[str, AgentLimit] = field(default_factory=dict)

    def register(
        self,
        agent_name: str,
        *,
        recursion_limit: int = DEFAULT_RECURSION_LIMIT,
        token_budget: int = DEFAULT_TOKEN_BUDGET,
        max_consecutive_errors: int = DEFAULT_MAX_CONSECUTIVE_ERRORS,
    ) -> None:
        self._entries[agent_name] = AgentLimit(
            recursion_limit=recursion_limit,
            token_budget=token_budget,
            max_consecutive_errors=max_consecutive_errors,
        )

    def _get(self, agent_name: str) -> AgentLimit:
        return self._entries.get(agent_name, AgentLimit())

    def recursion_limit(self, agent_name: str) -> int:
        return self._get(agent_name).recursion_limit

    def token_budget(self, agent_name: str) -> int:
        return self._get(agent_name).token_budget

    def max_consecutive_errors(self, agent_name: str) -> int:
        return self._get(agent_name).max_consecutive_errors
