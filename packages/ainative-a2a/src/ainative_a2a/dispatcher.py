"""Agent间任务委派编排——按能力发现目标agent，委派任务，并防止委派链路失控。

设计动机：一旦允许"agent A可以把任务委派给agent B"，就存在"B又把任务
委派回A（或者委派给一个更长链条最终绕回A）"这类循环委派的风险——如果
没有防护，会导致无限循环直到耗尽资源。本模块把两类防护作为一等公民：

1. **委派深度上限**（`max_delegation_depth`）：委派链条超过这个长度就
   直接拒绝，不再往下委派。
2. **循环检测**：如果目标agent已经出现在当前委派链条里（`A2ATask.delegation_chain`），
   说明即将形成环，直接拒绝，不依赖深度限制"迟早会截断"这种间接保护。
"""

# 先解释一下"A2A"和"委派"这两个概念：A2A是"Agent-to-Agent"（智能体对
# 智能体）的缩写——当一个系统里有多个不同分工的智能体（agent）时，一个
# agent遇到自己不擅长处理的子任务，可以把这个子任务"委派"（delegate）
# 给另一个更擅长的agent去做，等它做完再拿回结果——这就好比公司里一个
# 员工把某项专业工作转交给另一个部门的同事处理。这个文件要解决的核心
# 问题是：委派链条不能失控——如果A委派给B、B又不小心委派回A，就会形成
# 一个"死循环"，两边互相踢皮球，永远做不完（还会一直消耗计算资源）。
from __future__ import annotations

# uuid是Python标准库提供的"生成几乎不可能重复的唯一编号"的工具——
# UUID是Universally Unique Identifier（全局唯一标识符）的缩写，生成出
# 来是一长串看起来随机的字母数字组合，几乎不可能和世界上任何其他一次
# 生成的结果撞车，很适合给"这一次具体的委派任务"分配一个独一无二的
# task_id，方便后续追踪、审计、和别的任务区分开。
import uuid

from ainative_core.protocols import A2AResult, A2ATask, AgentRegistry, AgentTransport

DEFAULT_MAX_DELEGATION_DEPTH = 5
# ↑ 委派链条最多允许多长——超过这个长度就直接拒绝，即使还没有真的形成
#   循环，也可能是设计上出了问题（正常合理的委派场景不太可能需要
#   链条特别长），这是一道额外的安全网。


class DelegationLimitExceededError(RuntimeError):
    """委派链条超过`max_delegation_depth`或检测到循环委派时抛出。"""


class Dispatcher:
    """按能力名称找到目标agent，委派任务，并对委派链路做深度/循环防护。

    Args:
        registry: 能力注册表，用于按能力名称发现目标agent。
        transport: 实际发送任务的传输实现，默认可用`InProcessTransport`。
        max_delegation_depth: 允许的最大委派链条长度，超过则拒绝并抛出
            `DelegationLimitExceededError`。
    """

    def __init__(
        self,
        registry: AgentRegistry,
        transport: AgentTransport,
        *,
        max_delegation_depth: int = DEFAULT_MAX_DELEGATION_DEPTH,
    ) -> None:
        # 把构造时传进来的三个东西，分别存到`self`（这个实例自己）身上，
        # 方便下面的delegate方法随时取用。
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
            DelegationLimitExceededError: 委派链条超过深度上限，或`target_agent`
                已经出现在当前链条里（循环委派）。
            LookupError: 没有找到能处理该能力的agent，或匹配到多个但未显式
                指定`target_agent`。
        """
        # `(*delegation_chain, sender_agent)`——用星号把已有的
        # delegation_chain元组"展开"，再把这次发起委派的sender_agent
        # 追加到末尾，组成一个新的、更长一步的元组。这是"不修改原始
        # 数据、而是生成一份新数据"的写法（元组本身也是不可变的，压根
        # 没法"修改"，只能这样重新组合出一个新的）。
        chain = (*delegation_chain, sender_agent)
        if len(chain) > self._max_delegation_depth:
            # 委派链条已经比允许的最大深度还长——直接拒绝，抛出前面
            # 定义好的异常，把当前完整链条内容一起放进错误信息里，方便
            # 排查是从哪里开始一路委派下来的。
            raise DelegationLimitExceededError(
                f"delegation chain depth {len(chain)} exceeds max_delegation_depth={self._max_delegation_depth}: {chain}"
            )

        # `target_agent or self._resolve_target(capability)`——如果调用方
        # 已经明确指定了要委派给谁（target_agent不是None/空字符串），就
        # 用调用方指定的；否则调用下面的_resolve_target方法，按能力名称
        # 自动去注册表里查找。
        resolved_target = target_agent or self._resolve_target(capability)
        # `resolved_target in chain`——检查这次要委派的目标agent，是不是
        # 已经出现在当前委派链条里了。如果是，说明马上就要形成一个循环
        # （比如A委派给B，B又想委派回A，而"A"已经在链条里了）——直接
        # 拒绝，不依赖"链条早晚会因为深度限制被截断"这种间接、迟来的
        # 保护，第一时间就把循环挡住。
        if resolved_target in chain:
            raise DelegationLimitExceededError(
                f"cyclic delegation detected: '{resolved_target}' already appears in chain {chain}"
            )

        # 组装一个完整的"委派任务"数据对象——包含一个全新生成的唯一
        # task_id、要委派的能力名称、任务数据、发起方标识，以及包含这次
        # 委派在内的完整链条。
        task = A2ATask(
            # `str(uuid.uuid4())`——生成一个全新的随机UUID，再转换成
            # 字符串形式，作为这次任务的唯一编号。
            task_id=str(uuid.uuid4()),
            capability=capability,
            payload=payload,
            sender_agent=sender_agent,
            delegation_chain=chain,
        )
        # `await self._transport.send(...)`——真正把这个任务发送给目标
        # agent，并且"等待"它执行完、拿到结果。具体"怎么发送"（进程内
        # 直接调用函数，还是走网络请求）由传入的transport对象决定，
        # Dispatcher本身不关心这个细节。
        return await self._transport.send(resolved_target, task)

    def _resolve_target(self, capability: str) -> str:
        # 去注册表里查询"哪些agent登记了这个能力"，得到一个agent名字的
        # 列表（可能是0个、1个、或多个）。
        candidates = self._registry.find_agents_for(capability)
        if not candidates:
            # 空列表——没有任何agent登记过这个能力，没法自动决定该委派
            # 给谁，直接抛出LookupError（Python内置的"查找失败"异常类型）。
            raise LookupError(f"no agent registered for capability '{capability}'")
        if len(candidates) > 1:
            # 找到了不止一个候选——这种情况下"自动"选一个是有风险的
            # （可能选错），所以也直接报错，要求调用方显式指定到底想
            # 委派给哪一个，而不是让Dispatcher替调用方"猜"。
            raise LookupError(
                f"multiple agents registered for capability '{capability}': {candidates}; "
                f"pass target_agent explicitly to disambiguate"
            )
        # 只剩恰好一个候选——这是唯一能安全自动选择的情况，直接返回它。
        return candidates[0]
