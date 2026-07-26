"""MCP/工具调用审计记录——存储无关的通用schema。

改造自真实项目里验证过的MongoDB工具调用日志设计（原版硬编码collection名、
业务字段如`project_identifier`/`org_id`），泛化成任何工具调用审计场景都能
复用的通用schema：工具名、agent名、入参、出参、耗时、run/thread关联ID、
状态。具体持久化到哪里由调用方决定（内存/数据库/日志系统均可）。
"""

# 这一行是Python的"未来特性"声明：让下面所有类型注解（比如函数参数、
# 返回值上写的类型）都不需要在写代码的时候真正存在，Python解释器只把它们
# 当作字符串暂存，等真正需要检查类型时（比如IDE/mypy）才去解析，兼容性
# 更好、写法也更自由（比如可以用`str | None`这种"新式"写法）。
from __future__ import annotations

# time模块是Python标准库自带的，用来处理"时间"相关的操作。这里用到的
# `time.time`，会返回"从1970年1月1日00:00:00 UTC到现在经过的秒数"（一个
# 浮点数），这是计算机里表示"某一时刻"最常见、跨语言/跨系统都通用的方式
# 之一，方便不同系统之间比较、传输时间信息，不用担心时区问题。
import time

# dataclass是Python标准库提供的一个"装饰器"（写在类前面的@xxx），作用是
# 自动帮你生成一个类的__init__（构造函数）、__repr__（打印时显示的样子）
# 等样板代码，只需要声明"这个类有哪些字段"，不用自己手写重复代码。
# field是配合dataclass使用的辅助函数，专门用在"默认值不是一个简单的
# 字面量（比如不是数字/字符串），而是需要每次创建实例时单独算一遍"的
# 场景——见下面`input_summary`字段的详细解释。
from dataclasses import dataclass, field

# Any——表示"任意类型都可以"，用在没办法/没必要限定具体是什么类型的地方；
# Literal——"字面量类型"，比如`Literal["success", "error"]`表示"这个值
# 只能是字符串'success'或字符串'error'这两者之一，不能是别的任意字符串"，
# 比笼统的`str`类型注解更精确，IDE和类型检查工具能帮你提前发现"传了一个
# 不在允许范围内的字符串"这种错误。
from typing import Any, Literal

# 这一行不是一个类，而是给`Literal[...]`这一整串类型注解取了一个简短的
# 别名`ToolCallStatus`——之后别的地方（比如下面`ToolCallRecord`的
# `status`字段）写`ToolCallStatus`，效果和写一整串`Literal["success", "error"]`
# 完全一样，只是更短、语义更清楚，也方便以后统一在一个地方增删可选状态值。
ToolCallStatus = Literal["success", "error"]


# `@dataclass(frozen=True)` 是"装饰器"语法，意思是：
# 1. dataclass——自动生成构造函数等样板代码（见上面import处的解释）。
# 2. frozen=True——"冻结"这个类的实例，一旦创建出来，字段就不能再被修改
#    （比如 record.status = "error" 会直接报错）。这里选择冻结，是因为
#    一条审计记录代表"某一次工具调用已经发生过的、不可变的历史事实"——
#    调用已经结束了，记录下来的这些信息不应该、也不需要事后再被改动。
@dataclass(frozen=True)
class ToolCallRecord:
    """一次工具调用的完整审计记录。"""

    # 下面这些是这个类的"字段"（也叫"属性"）——每一行 `字段名: 类型` 或
    # `字段名: 类型 = 默认值` 的写法，dataclass会自动帮你变成构造函数里
    # 的一个参数。没写默认值的字段（前5个）是"必填项"，创建实例时必须
    # 显式传值；写了默认值的字段可以在调用时省略，自动使用默认值。

    tool_name: str
    """被调用的工具名字，比如"read_file"、"search_web"。"""
    # ↑ str类型——普通字符串，必填。

    agent_name: str
    """发起这次工具调用的agent标识，用于按agent维度统计/排查问题。"""

    status: ToolCallStatus
    """这次调用的结果状态，只能是"success"或"error"其中之一（见上面ToolCallStatus的定义）。"""

    duration_ms: float
    """这次调用花费的时长，单位是毫秒（ms）。"""

    # 下面这些字段都写了默认值，意味着构造`ToolCallRecord`实例时，这些
    # 参数是可选的——不传就用默认值。`str | None = None`表示"可以是
    # 字符串，也可以不传（默认是None，表示'空/没有'）"。
    run_id: str | None = None
    """这次调用所属的一次完整"运行"（比如一次完整的Agent执行）的关联ID，方便把散落的多条工具调用记录串联回同一次运行。"""

    thread_id: str | None = None
    """这次调用所属的对话/会话线程ID，方便按会话维度查询历史记录。"""

    # `field(default_factory=dict)` 的写法专门用在"默认值是可变对象
    # （比如列表/字典）"的场景——不能直接写 `input_summary: dict = {}`，
    # 因为那样所有实例会共享同一个字典对象（这是Python的一个经典陷阱：
    # 可变默认值只会在类定义时被创建一次，之后所有实例复用同一份，一个
    # 实例改了这个字典，所有其他没显式传参的实例都会看到这个改动）。
    # `default_factory=dict` 表示"每次创建新实例时，都调用一次dict()
    # 生成一个全新的空字典"，确保每个ToolCallRecord实例的input_summary
    # 都是各自独立、互不干扰的字典对象。
    input_summary: dict[str, Any] = field(default_factory=dict)
    """入参的摘要信息（不建议塞入原始的敏感数据，只存脱敏后的关键字段）。"""

    output_summary: dict[str, Any] = field(default_factory=dict)
    """出参/返回结果的摘要信息，用法同input_summary。"""

    error_message: str | None = None
    """`status`为"error"时的具体错误信息；成功时通常为None。"""

    # `field(default_factory=time.time)` ——这里传给default_factory的是
    # `time.time`这个函数本身（注意没有加括号`()`调用它），意思是
    # "每次创建新实例时，去调用一次time.time()，把调用那一刻的时间戳
    # 当作默认值"。这样每条记录如果没有显式传timestamp，就会自动打上
    # "这条记录被创建出来那一刻"的时间戳，不需要调用方每次手动传当前时间。
    timestamp: float = field(default_factory=time.time)
    """这条记录被创建时的Unix时间戳（自1970年以来经过的秒数），未显式传入时自动取当前时间。"""


class InMemoryToolCallAuditLog:
    """`ToolCallRecord`的内存版存储——供demo/测试使用，真实项目应实现自己的持久化。"""

    # 注意：这个类没有加`@dataclass`装饰器——因为它不是一个"只存数据的
    # 容器"，而是一个带有行为（记录、查询、统计）的"服务类"，需要自己手写
    # 构造函数和方法，dataclass的自动生成样板代码在这里不适用。

    def __init__(self) -> None:
        # `__init__`是Python里"构造函数"的固定写法——创建这个类的一个
        # 新实例时（比如`InMemoryToolCallAuditLog()`），Python会自动调用
        # 这个方法。`self`代表"正在被创建的这个实例本身"，是Python里
        # 实例方法的第一个参数的固定写法（虽然名字理论上可以随便起，但
        # 全世界的Python代码都约定俗成写成`self`）。
        # `-> None`表示"这个方法不返回任何有意义的值"——构造函数的职责
        # 是"把这个实例初始化好"，不是"计算并返回一个结果"。

        # `self._records`——变量名前面加一个下划线`_`，是Python里的一个
        # 约定（不是强制的语法规则）：表示"这是这个类的内部实现细节，
        # 不建议外部代码直接访问/修改它"，外部应该通过下面定义的
        # `record`/`all`/`for_tool`等公开方法来间接操作它。
        # `list[ToolCallRecord] = []`——类型注解表示"这是一个列表，
        # 里面每一项都应该是ToolCallRecord类型的实例"，初始值是一个
        # 空列表，之后每记录一次工具调用就往这个列表里追加一条。
        self._records: list[ToolCallRecord] = []

    def record(self, entry: ToolCallRecord) -> None:
        # `.append(entry)`——列表（list）的方法，把`entry`这一整个对象
        # 追加到列表的末尾，列表长度因此增加1。这是"记录一条新审计
        # 记录"这件事最核心的一步操作。
        self._records.append(entry)

    def all(self) -> list[ToolCallRecord]:
        # `list(self._records)`——用内部记录列表`self._records`重新
        # 构造一个新的列表对象返回给调用方，而不是直接把`self._records`
        # 本身返回出去。这样做是为了防止调用方拿到返回值之后，对它做
        # `.append(...)`/`.clear()`等修改操作时，意外地也改动了这个类
        # 内部真正维护的那份记录列表——`list(...)`包一层，相当于给
        # 调用方一份"独立的快照拷贝"，而不是"内部数据的直接引用"（和
        # config.py里`copy.deepcopy`要解决的问题是同一类思路，只是这里
        # 列表里的元素本身是frozen的dataclass、不会被就地修改，所以只需要
        # 浅层的`list(...)`复制一层列表容器本身即可，不需要更彻底的深拷贝）。
        return list(self._records)

    def for_tool(self, tool_name: str) -> list[ToolCallRecord]:
        """按工具名精确匹配——`tool_name`必须是一个真实的工具名字符串。

        注意：这里的`None`不是"不过滤"的特殊值（那是`error_rate(tool_name=None)`
        的语义，两者故意不同）——`for_tool(None)`会按字面意思匹配
        `ToolCallRecord.tool_name is None`的记录（正常情况下不应该存在，
        因为该字段声明类型是`str`），而不是返回全部记录。不要把这两个
        方法的`None`当作同一件事。
        """
        # 这是一个"列表推导式"（list comprehension）——`[表达式 for 变量 in 可迭代对象 if 条件]`
        # 的简写写法，等价于写一个for循环、每次判断条件为真就往一个新
        # 列表里追加一项，但更简洁。这里的意思是：遍历内部记录列表
        # `self._records`里的每一条记录`r`，只保留`r.tool_name`等于传入
        # 的`tool_name`的那些，组成一个新列表返回。
        return [r for r in self._records if r.tool_name == tool_name]

    def error_rate(self, tool_name: str | None = None) -> float:
        """计算错误率——`tool_name`为None时统计全部工具的整体错误率（这是
        `error_rate`自己的"不过滤"哨兵值语义，不等价于调用`for_tool(None)`，
        后者会尝试精确匹配`tool_name`字面值为`None`的记录）。"""
        # 这一行用了Python的"条件表达式"（三元表达式）写法：
        # `值A if 条件 else 值B`，意思是"如果条件为真，取值A；否则取值B"，
        # 等价于其他语言里的`条件 ? 值A : 值B`。这里的意思是：如果调用方
        # 传了具体的`tool_name`（不是None），就只统计这个工具的记录
        # （调用`for_tool`按名字过滤）；如果`tool_name`是None（调用方
        # 没传，或显式传了None），就直接使用全部记录`self._records`，
        # 统计"所有工具加在一起"的整体错误率。
        records = self.for_tool(tool_name) if tool_name is not None else self._records
        # 如果一条记录都没有（比如还没发生过任何工具调用，或者按名字
        # 过滤后一条都不匹配），"错误率"这个概念本身没有意义（分母为0
        # 会导致除法直接报错），这里选择返回0.0（表示"没有错误"）作为
        # 一个安全、合理的默认值，而不是让程序崩溃。
        if not records:
            return 0.0
        # `sum(1 for r in records if r.status == "error")`——这是一个
        # "生成器表达式"（写法很像列表推导式，但用圆括号而不是方括号），
        # 意思是"遍历records里的每条记录r，只要它的status是'error'，就
        # 产出一个1（其他情况不产出任何值）"，外层的`sum(...)`把所有产出
        # 的1加起来——效果就是"数一数records里有多少条记录的status是
        # 'error'"，这是一种比先构造一个完整列表再调用`len()`更省内存的
        # 计数写法。
        errors = sum(1 for r in records if r.status == "error")
        # 错误条数除以总条数，得到0.0~1.0之间的一个浮点数错误率（比如
        # 0.25表示25%的调用失败了）。
        return errors / len(records)
