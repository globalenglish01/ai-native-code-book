"""跨Agent护栏参数的集中管理。

改造自真实项目里"把散落在各agent.py里的recursion_limit/token预算/连续失败
上限集中到一个模块"的做法，泛化成一个可以按需注册任意agent名称的类，而不是
硬编码某个项目专属的agent列表。

真实项目里这类数值大多是历史经验值而非严谨评估的结果——保留这个诚实态度：
`AgentLimits`不假装"默认值"是放之四海而皆准的最优解，只是一个安全的起点，
调用方应该在有真实运行数据后按需覆盖。
"""

# 这一行是Python的"未来特性"声明：让下面所有类型注解都不需要在写代码的
# 时候真正存在，Python解释器只把它们当作字符串暂存，等真正需要检查类型时
# （比如IDE/mypy）才去解析。详见ainative_core.config模块里的详细解释——
# 本包所有文件都统一加这一行，是本框架的通用约定。
from __future__ import annotations

# dataclass/field 是Python标准库提供的工具：
# - dataclass是一个"装饰器"（写在类前面的@xxx），作用是自动帮你生成一个类的
#   __init__（构造函数）等样板代码，你只需要声明"这个类有哪些字段"。
# - field是配合dataclass使用的辅助函数，专门用在"字段默认值是可变对象
#   （比如字典/列表）"的场景——不能直接写`_entries: dict = {}`，那样所有
#   实例会共享同一个字典对象；`field(default_factory=dict)`表示"每次
#   创建新实例时都调用一次dict()生成一个全新的空字典"，详见下面AgentLimits
#   类里的实际用法。
from dataclasses import dataclass, field

# 模块级别的常量——写在文件最外层、全大写命名的变量，代表"整个模块通用的
# 固定数值"，方便统一修改、避免同一个数字散落在代码各处。
DEFAULT_RECURSION_LIMIT = 60
# ↑ "递归限制"——一个agent运行时最多允许经过多少轮"模型调用→工具调用→
#   再次模型调用"这样的循环步骤，超过这个数字就应该强制停止，防止agent
#   陷入死循环无限跑下去。

DEFAULT_TOKEN_BUDGET = 200_000
# ↑ 一次agent运行最多允许消耗的输入token数量上限（`200_000`里的下划线
#   只是Python允许的"数字分组分隔符"，写法上等同于200000，纯粹是为了
#   人类阅读时更容易数清楚有几个零，不影响实际数值）。

DEFAULT_MAX_CONSECUTIVE_ERRORS = 2
# ↑ 同一个工具最多允许连续失败几次，超过就应该判定为"这条路走不通"，
#   短路拦截，不再让AI继续做无意义的重试。


# @dataclass(frozen=True) 这行是装饰器语法，意思是：
# 1. dataclass——自动生成构造函数等样板代码（见上面详细解释）。
# 2. frozen=True——"冻结"这个类的实例，一旦创建出来，字段就不能再被修改
#    （比如 limit.recursion_limit = 999 会直接报错）。这里用frozen=True
#    是因为AgentLimit代表"一组已经确定下来的护栏参数快照"，创建之后不应该
#    再被意外改动。
@dataclass(frozen=True)
class AgentLimit:
    """单个agent的一组护栏参数。"""

    # 下面三个字段都是"类型 = 默认值"的写法：dataclass会自动把每一行变成
    # 构造函数里的一个参数，不传就用等号后面写的默认值。三个字段类型都是
    # int（整数），默认值分别指向上面定义好的三个模块级常量。
    recursion_limit: int = DEFAULT_RECURSION_LIMIT
    token_budget: int = DEFAULT_TOKEN_BUDGET
    max_consecutive_errors: int = DEFAULT_MAX_CONSECUTIVE_ERRORS


# 这里是`@dataclass`，没有加`frozen=True`——意味着AgentLimits创建之后，
# 它内部的`_entries`字典仍然可以被后续的`register()`调用不断追加新内容。
# 这是刻意的：AgentLimits代表一个"运行期间会持续被写入新注册项"的容器，
# 不像AgentLimit那样是一次性、创建后就不再变化的数据快照。
@dataclass
class AgentLimits:
    """按agent名称注册/查询护栏参数，未注册的agent回退到默认值。

    用法::

        limits = AgentLimits()
        limits.register("checkout_agent", recursion_limit=80, token_budget=300_000)
        limits.recursion_limit("checkout_agent")   # 80
        limits.recursion_limit("unknown_agent")    # 60（默认值）
    """

    # `_entries: dict[str, AgentLimit] = field(default_factory=dict)` ——
    # 类型注解是"一个字典，key是字符串（agent名称），value是AgentLimit
    # 实例"；用`field(default_factory=dict)`而不是直接写`= {}`，是因为
    # 后者会让所有AgentLimits实例意外共享同一个字典（这是Python的一个
    # 经典陷阱：可变默认值只会在类定义时被创建一次）。字段名前的下划线
    # 是命名习惯，表示"这是内部实现细节，不建议外部代码直接读写"。
    _entries: dict[str, AgentLimit] = field(default_factory=dict)

    def register(
        self,
        agent_name: str,
        # `*` 出现在参数列表中间，是Python的特殊语法：它后面的所有参数
        # 都必须"用参数名=值"的方式传入（叫"关键字参数"），不能只按位置
        # 顺序传值。这样调用时必须写成
        # `limits.register("checkout_agent", recursion_limit=80, ...)`，
        # 强制调用方明确写出每个参数的名字，避免记错参数顺序而传错值。
        *,
        recursion_limit: int = DEFAULT_RECURSION_LIMIT,
        token_budget: int = DEFAULT_TOKEN_BUDGET,
        max_consecutive_errors: int = DEFAULT_MAX_CONSECUTIVE_ERRORS,
    ) -> None:
        # 把这个agent名字对应的一组参数，打包成一个AgentLimit实例，存进
        # `_entries`字典里——如果这个agent名字之前已经注册过，这里会直接
        # 覆盖旧的记录（字典的赋值语法本身就是"有就覆盖，没有就新增"）。
        self._entries[agent_name] = AgentLimit(
            recursion_limit=recursion_limit,
            token_budget=token_budget,
            max_consecutive_errors=max_consecutive_errors,
        )

    # 方法名前的下划线表示这是"内部辅助方法"，只打算被本类自己的其他方法
    # 调用，不建议外部代码直接调用它（纯命名约定，语言层面不强制）。
    def _get(self, agent_name: str) -> AgentLimit:
        # `dict.get(key, 默认值)` 表示"去字典里找这个key，找不到就返回
        # 默认值，不会因为KeyError而报错"——这里的默认值是`AgentLimit()`，
        # 也就是"什么都不传，全部使用类定义时写好的默认参数"，恰好对应
        # docstring里"未注册的agent回退到默认值"这句话的具体实现。
        return self._entries.get(agent_name, AgentLimit())

    def recursion_limit(self, agent_name: str) -> int:
        # 先拿到这个agent对应的AgentLimit实例（注册过的就是真实配置，
        # 没注册过的就是默认配置），再从里面读出recursion_limit这一个字段。
        return self._get(agent_name).recursion_limit

    def token_budget(self, agent_name: str) -> int:
        return self._get(agent_name).token_budget

    def max_consecutive_errors(self, agent_name: str) -> int:
        return self._get(agent_name).max_consecutive_errors
