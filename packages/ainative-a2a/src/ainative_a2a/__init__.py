"""ainative-a2a —— Agent间任务委派、结果回传、能力注册与发现。"""

# 这一行是Python的"未来特性"声明：让下面所有类型注解都不需要在写代码的
# 时候真正存在，Python解释器只把它们当作字符串暂存，等真正需要检查类型
# 时（比如IDE/mypy）才去解析。好处是：类型注解里可以互相引用还没定义完的
# 类型，不加这一行在旧版本Python里会报错，加了就没问题。
from __future__ import annotations

# 下面这几行是"从子模块把类/函数/常量导入到包的顶层"——目的是让使用方
# 可以写更短的 `from ainative_a2a import Dispatcher`，而不用知道
# `Dispatcher`具体定义在`ainative_a2a.dispatcher`这个子文件里。
# 一行里用逗号隔开导入多个名字是Python的常见写法，效果等同于分开写多行
# `from ainative_a2a.dispatcher import xxx`。
from ainative_a2a.dispatcher import DEFAULT_MAX_DELEGATION_DEPTH, DelegationLimitExceededError, Dispatcher
from ainative_a2a.registry import InMemoryAgentRegistry
from ainative_a2a.transport import AgentHandler, InProcessTransport

# `__version__` 是Python社区的通用约定——定义了这个变量，别的代码就能
# 通过 `ainative_a2a.__version__` 读到"这是第几个版本"，不需要单独去
# 解析pyproject.toml文件。
__version__ = "0.1.0"

# `__all__` 是一个字符串列表，用来声明"当别人写
# `from ainative_a2a import *` 时，应该导入哪些名字"——它同时也是给
# 阅读者的一份"这个包对外公开的完整名单"说明文档，方便一眼看清这个包
# 到底提供了哪些可以直接使用的东西。
__all__ = [
    "DEFAULT_MAX_DELEGATION_DEPTH",
    "AgentHandler",
    "DelegationLimitExceededError",
    "Dispatcher",
    "InMemoryAgentRegistry",
    "InProcessTransport",
]
