"""`ainative_core.protocols.AgentRegistry`的内存版默认实现。"""

from __future__ import annotations

# copy——见ainative_core.memory_backends.py里的详细解释：`copy.deepcopy(x)`
# 把x连同它内部所有嵌套的list/dict完整复制一份，复制出来的新对象和原对象
# 完全独立，改动一个不会影响另一个。
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
        # `dict[str, dict[str, AgentCapability]]`——一个"两层嵌套"的字典：
        # 外层key是agent名字，value又是一个字典；内层key是能力名字，
        # value才是真正的AgentCapability对象。可以理解成"一个agent可能
        # 登记了好几项不同的能力，需要按能力名字分别存放，所以每个agent
        # 名字下面还要再套一层字典"。
        self._capabilities: dict[str, dict[str, AgentCapability]] = {}

    def register(self, agent_name: str, capability: AgentCapability) -> None:
        # `dict.setdefault(key, 默认值)`——如果这个key已经存在，直接返回
        # 已有的value；如果key不存在，就先把"key: 默认值"这一对存进去，
        # 再返回这个默认值。这里的效果是："如果这个agent_name是第一次见
        # 到，就给它建一个空字典作为它的能力集合；如果已经见过，就直接
        # 拿到它已有的那个能力字典，继续往里面加东西"。
        bucket = self._capabilities.setdefault(agent_name, {})
        # 把这个能力，用它自己的name当key，存进这个agent对应的能力字典
        # 里——用deepcopy存一份独立复制品，而不是调用方传进来的原始对象
        # 本身（详见类docstring"真实bug背景"的解释）。
        bucket[capability.name] = copy.deepcopy(capability)

    def find_agents_for(self, capability_name: str) -> list[str]:
        # 这是一个"生成器表达式"包在`sorted(...)`里的写法——依次检查
        # `self._capabilities`里的每一个"agent_name: 能力字典"这一对，
        # 只要`capability_name`（要查找的能力名）出现在这个agent的能力
        # 字典的key里（`capability_name in caps`，对字典用`in`默认检查
        # 的是key），就把这个agent_name收集进结果里。最外层`sorted(...)`
        # 把收集到的agent名字列表按字母顺序排序后返回，保证每次查询同样
        # 的条件都得到同样顺序的结果，方便测试和展示。
        return sorted(
            agent_name
            for agent_name, caps in self._capabilities.items()
            if capability_name in caps
        )

    def get_capability(self, agent_name: str, capability_name: str) -> AgentCapability | None:
        # `self._capabilities.get(agent_name, {})` 先安全地拿到这个agent
        # 对应的能力字典（没有这个agent就当作空字典，不报错）；再对这个
        # 字典调用`.get(capability_name)`，安全地拿到具体的能力对象
        # （没有这个能力就是None）。两次`.get(...)`连着写，是"逐层安全
        # 访问嵌套字典"的常见写法，任何一层找不到都不会报错，只会顺着
        # 走到"最终结果是None"。
        found = self._capabilities.get(agent_name, {}).get(capability_name)
        # `A if 条件 else B` 是Python的"三元表达式"（一行内写的if/else）：
        # 如果找到了（`found is not None`），返回它的深拷贝；没找到就
        # 直接返回None——这里不能对None做deepcopy（deepcopy(None)其实
        # 也是合法的、结果还是None，但显式判断更清晰，也避免"看起来在
        # 深拷贝一个可能不存在的东西"这种误导）。
        return copy.deepcopy(found) if found is not None else None

    def all_agents(self) -> list[str]:
        # `.keys()` 拿到字典里所有key组成的一个视图，`sorted(...)`把它们
        # 转换成一个排好序的列表返回。
        return sorted(self._capabilities.keys())

    def capabilities_of(self, agent_name: str) -> list[AgentCapability]:
        # 这是一个"列表推导式"：对`self._capabilities.get(agent_name, {})`
        # （这个agent已登记的全部能力，没有就是空字典）的每一个value
        # （也就是每一个AgentCapability对象），都做一次深拷贝，收集成
        # 一个新列表返回——同样是为了避免调用方拿到列表后修改其中的
        # 对象，污染注册表内部真正存储的数据。
        return [copy.deepcopy(cap) for cap in self._capabilities.get(agent_name, {}).values()]
