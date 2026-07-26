"""`UsageSink` 协议的内存版默认实现。

配合`ainative_core.usage_tracking.UsageTrackingCallbackHandler`使用——
`build_model`/`build_agent_model`等工厂函数接受一个可选的`usage_sink`
参数，传入`InMemoryUsageSink`实例即可脱离任何真实数据库直接跑通
用量统计的demo和测试。

`PromptStore`协议的内存版默认实现在`ainative_prompt.store.InMemoryPromptStore`
（更贴近实际使用场景：与`load_prompt()`/`ab_select_deterministic()`等
Prompt管理逻辑放在同一个包里），不在这里重复实现。
"""

from __future__ import annotations

# copy模块是Python标准库自带的，`copy.deepcopy(x)`会把x连同它内部
# 所有嵌套的list/dict一起完整复制一份，复制出来的新对象和原对象完全
# 独立——改动其中一个，另一个不会跟着变。与之相对的是"浅拷贝"
# （比如`dict(x)`），浅拷贝只复制最外层，内部嵌套的list/dict仍然和
# 原对象共享同一份，改一个另一个也会变（这是很多真实bug的根源）。
import copy

# typing模块提供"类型提示"用的工具——这些内容只是给人看/给IDE和类型
# 检查工具看的，不影响程序真正运行时的行为。`Any`表示"这里可以是任意
# 类型"，用在不方便/不需要精确约束类型的地方。
from typing import Any


class InMemoryUsageSink:
    """`UsageSink` 的内存版实现——把所有用量事件存进一个list。

    `record()`对传入的`event`做深拷贝再存储，`events`属性也对每个已存储
    事件做深拷贝再返回——不能只存/返回原始dict引用：调用方常见的用法是
    复用一个"模板"事件dict（比如`base = {...}; event = {**base, "field": x}`
    这类不一致的构造方式，或者拿到`events`列表后就地修改某一条做二次
    处理），如果内部存储/对外返回的是同一个dict对象，这类操作会静默
    篡改"已经记录完成"的历史用量数据，且没有任何报错信号。
    """

    # __init__ 是Python类的"构造函数"——每次用 `InMemoryUsageSink()`
    # 创建一个新实例时，Python会自动调用这个方法。`self`代表"这个正在
    # 被创建的实例本身"，是Python里所有实例方法约定俗成的第一个参数名。
    def __init__(self) -> None:
        # `self._events = []` 给这个实例挂上一个新的属性`_events`，
        # 初始值是一个空列表。前缀下划线`_`是Python的一种命名习惯，
        # 表示"这是内部实现细节，不建议外部代码直接访问"（但语言层面
        # 并不强制禁止，纯粹是约定）。
        # `list[dict[str, Any]]` 这个类型注解的意思是："一个列表，
        # 列表里每一项都是一个dict，dict的key是字符串，value可以是
        # 任意类型"——这正好对应一条条用量事件记录的形状。
        self._events: list[dict[str, Any]] = []

    def record(self, event: dict[str, Any]) -> None:
        # `.append(x)` 是Python列表的内置方法，作用是把x加到列表末尾。
        # 这里加的不是`event`本身，而是`copy.deepcopy(event)`——也就是
        # event的一份完整独立复制品，避免调用方后续修改自己手里的
        # event变量时，意外改到了已经存进来的历史记录（见上面类
        # docstring里"真实bug背景"的详细解释）。
        self._events.append(copy.deepcopy(event))

    # @property 是一个装饰器，作用是让下面这个方法可以"像访问属性一样
    # 被调用"——加了这个装饰器之后，调用方写`sink.events`（不用加括号）
    # 就能拿到结果，而不需要写成`sink.events()`。适合用在"计算/包装一下
    # 再返回，但概念上更像是一个只读属性"的场景。
    @property
    def events(self) -> list[dict[str, Any]]:
        """只读视图，供测试/demo读取已记录的全部事件。"""
        # 同样用deepcopy返回一份独立复制品，而不是内部存储列表`_events`
        # 本身——调用方拿到这个返回值之后即使去修改它，也不会影响
        # sink内部真正保存的数据。
        return copy.deepcopy(self._events)

    def total_tokens(self) -> int:
        """把所有事件的 input_tokens+output_tokens 累加——demo/测试里常用的快捷统计。"""
        # 先声明一个累加器变量，从0开始。
        total = 0
        # `for event in self._events:` 是Python的"for循环"，效果是
        # 依次把`_events`列表里的每一项（每一条事件记录）取出来，
        # 临时命名为`event`，然后执行循环体里的代码，重复直到列表
        # 里所有项都处理完。
        for event in self._events:
            # `event.get("input_tokens", 0)` 表示"从这个dict里找key
            # 叫input_tokens的值，找不到就用0代替"——这样即使某条记录
            # 忘了填这个字段，也不会因为KeyError直接崩溃。
            # 后面的 `or 0` 是双重保险：即使这个字段真的存在，但值恰好
            # 是None（比如显式写了`"input_tokens": None`），`None or 0`
            # 的结果也会是0，而不是让后面的加法因为"None不能和数字相加"
            # 而报错。
            total += event.get("input_tokens", 0) or 0
            total += event.get("output_tokens", 0) or 0
        # 循环结束后，把累加好的总数返回给调用方。
        return total

    def total_for_agent(self, agent_name: str) -> int:
        """按`agent_name`过滤后再累加——用于统计某个具体agent的用量。"""
        total = 0
        for event in self._events:
            # 只有这条记录的agent_name字段，和调用方传进来的
            # agent_name参数完全一致时，才把它的token数计入总数——
            # 这就是"过滤"的实现方式：用if判断决定要不要执行累加。
            if event.get("agent_name") == agent_name:
                total += event.get("input_tokens", 0) or 0
                total += event.get("output_tokens", 0) or 0
        return total
