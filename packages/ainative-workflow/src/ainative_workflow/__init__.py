"""ainative-workflow —— 轻量DAG编排引擎、HITL中断检测与超时安全默认值。"""

# 这一行是Python的"未来特性"声明：让下面所有类型注解都不需要在写代码的
# 时候真正存在，Python解释器只把它们当作字符串暂存，等真正需要检查类型时
# （比如IDE/mypy）才去解析。好处是允许"函数还没定义完就引用自己类名"这类
# 写法在旧版本Python里也不报错。这份文件本身没有用到这类写法，但保留这行
# 是整个仓库所有模块的统一约定，方便以后任意增删类型注解都不用再补这一行。
from __future__ import annotations

# 下面这一大段 `from 模块 import 名字` ，做的事情是：把graph.py、hitl.py、
# hitl_policy.py三个文件里各自定义好的类/函数，"搬"到这个包的最外层
# （也就是`ainative_workflow`这个包本身）来。这样使用这个包的人可以直接写
# `from ainative_workflow import Workflow`，而不需要知道`Workflow`这个类
# 具体是在`ainative_workflow.graph`这个子模块里定义的——`__init__.py`
# 在这里起到了"对外统一入口/门面"的作用，把内部文件划分的细节隐藏起来。
from ainative_workflow.graph import (
    NodeStatus,
    Workflow,
    WorkflowNode,
    WorkflowPaused,
    WorkflowRun,
)
from ainative_workflow.hitl import count_pending_decisions, extract_interrupt
from ainative_workflow.hitl_policy import (
    DEFAULT_TIMEOUT_SECONDS,
    read_timeout_seconds,
    safe_timeout_decision,
    safe_timeout_decisions,
)

# 这个包对外公开的版本号——纯粹是给人/工具看的字符串，不影响程序运行逻辑。
__version__ = "0.1.0"

# `__all__` 是Python的一个特殊约定变量：当别人写 `from ainative_workflow import *`
# （用星号一次性导入这个包里所有"公开"的东西）时，Python只会导入这个列表里
# 列出的名字，不在列表里的（哪怕是从别的模块import进来的）不会被带出去。
# 这里把它按字母顺序列出来，也相当于一份"这个包对外承诺的公开API清单"——
# 明确告诉使用者"你应该用这些，别的都是内部实现细节，随时可能改动"。
__all__ = [
    "DEFAULT_TIMEOUT_SECONDS",
    "NodeStatus",
    "Workflow",
    "WorkflowNode",
    "WorkflowPaused",
    "WorkflowRun",
    "count_pending_decisions",
    "extract_interrupt",
    "read_timeout_seconds",
    "safe_timeout_decision",
    "safe_timeout_decisions",
]
