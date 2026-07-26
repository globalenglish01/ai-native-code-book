"""ainative-observability —— 结构化JSON日志、统一敏感信息过滤器、轻量级追踪span记录。"""

# 让类型注解可以延迟解析，详见其他模块里的详细解释，这里不再重复。
from __future__ import annotations

# 这个文件是整个包的"入口"——当别的代码写`import ainative_observability`
# 或者`from ainative_observability import Tracer`时，Python实际执行的
# 就是这个`__init__.py`文件。下面这几行`from ... import ...`，把散落在
# 各个子模块（`memory_backends.py`/`structured_logging.py`/`tracing.py`）
# 里定义的类和函数，统一"搬"到包的最外层，这样使用者可以直接写
# `from ainative_observability import Tracer`，而不需要知道`Tracer`
# 具体是定义在哪个子文件里（`from ainative_observability.tracing import Tracer`）。
from ainative_observability.memory_backends import AlwaysFailingSpanExporter, InMemorySpanExporter
from ainative_observability.structured_logging import (
    DEFAULT_SENSITIVE_KEYS,
    JsonFormatter,
    SensitiveDataFilter,
    install_structured_logging,
)
from ainative_observability.tracing import SpanExporter, SpanRecord, Tracer

# 这个包对外公布的版本号——字符串形式，遵循"主版本.次版本.修订号"的
# 惯例，方便其他依赖这个包的项目知道自己用的是哪个版本。
__version__ = "0.1.0"

# `__all__`——Python的一个特殊模块级变量：明确列出"当别人写
# `from ainative_observability import *`时，应该导入哪些名字"。这不是
# 严格的访问限制（不写在这里的名字，只要知道具体路径，依然可以被导入），
# 更多是一份"官方支持、对外公开的API清单"的文档化声明，同时很多代码
# 检查工具也会用它来判断"这个名字虽然在文件里没被直接使用，但因为被
# 写进了__all__，所以确实是故意导出的，不是没用到的死代码"。
__all__ = [
    "DEFAULT_SENSITIVE_KEYS",
    "AlwaysFailingSpanExporter",
    "InMemorySpanExporter",
    "JsonFormatter",
    "SensitiveDataFilter",
    "SpanExporter",
    "SpanRecord",
    "Tracer",
    "install_structured_logging",
]
