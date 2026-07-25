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

__version__ = "0.1.0"
