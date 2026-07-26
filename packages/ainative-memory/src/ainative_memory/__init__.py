"""ainative-memory —— checkpoint持久化协议、长期记忆存储、PII脱敏代理、历史token预算裁剪。

摘要压缩配置（context-window-aware summarization config）复用
`ainative_core.model_factory.get_summarization_config`，本包不重复实现。
"""

# 让类型注解可以延迟解析，详见ainative_core里的详细解释，这里不再重复。
from __future__ import annotations

# 从各子模块把类/函数导入到包的顶层，让使用方可以写更短的
# `from ainative_memory import InMemoryMemoryStore`，不需要知道具体定义
# 在`ainative_memory.store`这个子文件里。
from ainative_memory.checkpoint import CheckpointSaverFactory
from ainative_memory.history_budget import estimate_history_tokens, trim_history_to_budget
from ainative_memory.redacting_backend import RedactingBackend, wrap_summarization_backend
from ainative_memory.rendering import render_memory_entries
from ainative_memory.store import InMemoryMemoryStore

# `__version__` 是Python社区的通用约定——定义了这个变量，别的代码就能
# 通过 `ainative_memory.__version__` 读到"这是第几个版本"。
__version__ = "0.1.0"

# `__all__` 声明"这个包对外公开的完整名单"——同时也是当别人写
# `from ainative_memory import *` 时会被导入的名字列表。
__all__ = [
    "CheckpointSaverFactory",
    "InMemoryMemoryStore",
    "RedactingBackend",
    "estimate_history_tokens",
    "render_memory_entries",
    "trim_history_to_budget",
    "wrap_summarization_backend",
]
