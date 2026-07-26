"""ainative-core —— AI Native Framework 的核心地基。

不依赖任何具体数据库/中间件产品（Postgres、MongoDB、Redis），只定义：
1. 跨模块共享的协议接口（`protocols.py`）——比如"怎么记录一次用量"、
   "怎么读取/写入一份Prompt"，具体存储实现由使用方注入。
2. 供应商无关的模型工厂（`model_factory.py`）——统一构建LangChain
   `BaseChatModel`，支持跨厂商自动降级，可选挂载用量采集回调
   （`usage_tracking.py`）。
3. 面向环境变量的通用配置读取（`config.py`）。
4. `UsageSink`协议的内存版默认实现（`memory_backends.py`）——保证不接
   真实数据库也能独立运行、独立测试。

真实生产项目使用本包时，只需要实现这里定义的协议（比如自己写一个
`PostgresUsageSink(UsageSink)`），不需要改动本包的任何代码。
"""

from __future__ import annotations

# `__version__` 是Python社区的一个通用约定——一个包如果定义了这个变量，
# 别的代码就能通过 `ainative_core.__version__` 读到"这是第几个版本"，
# 不需要单独去解析pyproject.toml文件。这个包本身不导出任何具体的类/
# 函数在这里（不像别的ainative-*包那样有一长串`from xxx import ...`），
# 是因为protocols.py/model_factory.py等文件里的内容，使用方通常会
# 写`from ainative_core.protocols import UsageSink`这种"精确到具体
# 文件"的导入方式，而不是`from ainative_core import UsageSink`。
__version__ = "0.1.0"
