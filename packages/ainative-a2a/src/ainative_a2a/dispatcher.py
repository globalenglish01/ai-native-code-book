"""Agent间任务委派编排——按能力发现目标agent，委派任务，并防止委派链路失控。

设计动机：一旦允许"agent A可以把任务委派给agent B"，就存在"B又把任务
委派回A（或者委派给一个更长链条最终绕回A）"这类循环委派的风险——如果
没有防护，会导致无限循环直到耗尽资源。本模块把两类防护作为一等公民：

1. **委派深度上限**（`max_delegation_depth`）：委派链条超过这个长度就
   直接拒绝，不再往下委派。
2. **循环检测**：如果目标agent已经出现在当前委派链条里（`A2ATask.delegation_chain`），
   说明即将形成环，直接拒绝，不依赖深度限制"迟早会截断"这种间接保护。
"""

from __future__ import annotations

import uuid

from ainative_core.protocols import A2AResult, A2ATask, AgentRegistry, AgentTransport

DEFAULT_MAX_DELEGATION_DEPTH = 5


class DelegationLimitExceeded(RuntimeError):
    """委派链条超过`max_delegation_depth`或检测到循环委派时抛出。"""


class Dispatcher:
    """按能力名称找到目标agent，委派任务，并对委派链路做深度/循环防护。

    Args:
        registry: 能力注册表，用于按能力名称发现目标agent。
        transport: 实际发送任务的传输实现，默认可用`InProcessTransport`。
        max_delegation_depth: 允许的最大委派链条长度，超过则拒绝并抛出
            `DelegationLimitExceeded`。
    """

    def __init__(
        self,
        registry: AgentRegistry,
        transport: AgentTransport,
        *,
        max_delegation_depth: int = DEFAULT_MAX_DELEGATION_DEPTH,
    ) -> None:
        self._registry = registry
        self._transport = transport
        self._max_delegation_depth = max_delegation_depth

    async def delegate(
        self,
        *,
        capability: str,
        payload: dict,
        sender_agent: str,
        target_agent: str | None = None,
        delegation_chain: tuple[str, ...] = (),
    ) -> A2AResult:
        """委派一个任务，返回执行结果。

        Args:
            capability: 需要的能力名称。
            payload: 任务输入数据。
            sender_agent: 发起本次委派的agent标识。
            target_agent: 显式指定目标agent；留空则按`capability`自动发现
                （要求`registry`里恰好登记了一个能处理该能力的agent，否则报错）。
            delegation_chain: 到目前为止的委派链条（发起委派时通常留空，
                由`Dispatcher`自己在链条上追加`sender_agent`）。

        Raises:
            DelegationLimitExceeded: 委派链条超过深度上限，或`target_agent`
                已经出现在当前链条里（循环委派）。
            LookupError: 没有找到能处理该能力的agent，或匹配到多个但未显式
                指定`target_agent`。
        """
        chain = (*delegation_chain, sender_agent)
        if len(chain) > self._max_delegation_depth:
            raise DelegationLimitExceeded(
                f"delegation chain depth {len(chain)} exceeds max_delegation_depth={self._max_delegation_depth}: {chain}"
            )

        resolved_target = target_agent or self._resolve_target(capability)
        if resolved_target in chain:
            raise DelegationLimitExceeded(
                f"cyclic delegation detected: '{resolved_target}' already appears in chain {chain}"
            )

        task = A2ATask(
            task_id=str(uuid.uuid4()),
            capability=capability,
            payload=payload,
            sender_agent=sender_agent,
            delegation_chain=chain,
        )
        return await self._transport.send(resolved_target, task)

    def _resolve_target(self, capability: str) -> str:
        candidates = self._registry.find_agents_for(capability)
        if not candidates:
            raise LookupError(f"no agent registered for capability '{capability}'")
        if len(candidates) > 1:
            raise LookupError(
                f"multiple agents registered for capability '{capability}': {candidates}; "
                f"pass target_agent explicitly to disambiguate"
            )
        return candidates[0]
