"""ainative-mcp —— MCP server配置组装、环境变量白名单、工具调用审计schema。"""

# MCP是"Model Context Protocol"（模型上下文协议）的缩写——一种让AI模型
# 能够统一调用外部工具（比如浏览器自动化、文件读写、数据库查询）的通用
# 协议标准。本包不实现MCP协议本身，只负责两件配套的事：组装MCP服务器
# 需要的配置字典、以及给"启动一个子进程运行MCP服务器"这件事做环境变量
# 安全过滤（防止把敏感的环境变量意外传给不受信任的子进程）。
from __future__ import annotations

# 从子模块把类/函数/常量导入到包的顶层，让使用方可以写更短的
# `from ainative_mcp import build_mcp_config`，不需要知道具体定义在
# `ainative_mcp.config`这个子文件里。
from ainative_mcp.audit import InMemoryToolCallAuditLog, ToolCallRecord
from ainative_mcp.config import SAFE_ENV_KEYS, build_mcp_config, build_safe_env, merge_mcp_configs

# `__version__` 是Python社区的通用约定——定义了这个变量，别的代码就能
# 通过 `ainative_mcp.__version__` 读到"这是第几个版本"。
__version__ = "0.1.0"

# `__all__` 声明"这个包对外公开的完整名单"——同时也是当别人写
# `from ainative_mcp import *` 时会被导入的名字列表。
__all__ = [
    "SAFE_ENV_KEYS",
    "InMemoryToolCallAuditLog",
    "ToolCallRecord",
    "build_mcp_config",
    "build_safe_env",
    "merge_mcp_configs",
]
