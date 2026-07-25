"""通用配置读取——不绑定任何具体项目的 Pydantic Settings 类。

真实项目里，`model_factory.py` 原本直接 `from app.config.settings import
settings` 去读一个和整个项目其他配置（数据库/MinIO/SMTP等）混在一起的巨大
Settings类。这里改成一个只关心"模型调用需要的那几项"的独立小配置对象，
默认从环境变量构造，真实项目如果想接入自己的配置系统，只需要在启动时
构造一个 `ProviderConfig` 实例并传给需要它的函数，而不需要让 ainative-core
知道任何关于这个真实项目配置系统的细节。
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class ProviderConfig:
    """模型调用相关的最小配置集合。

    默认构造函数 `from_env()` 从环境变量读取，字段名和常见的
    LangChain/供应商官方SDK约定的环境变量名保持一致（如 `ANTHROPIC_API_KEY`），
    方便真实项目直接复用已有的 `.env` 文件。
    """

    anthropic_api_key: str | None = None
    openai_api_key: str | None = None
    deepseek_api_key: str | None = None
    deepseek_base_url: str | None = None

    is_production: bool = False
    """是否生产环境——用于决定要不要对某些危险配置做更严格的拦截。"""

    preferred_language: str | None = None
    """部署语言（如 "ja"/"zh"），用于决定是否跳过对某些语言支持较弱的供应商。"""

    default_model_id: str = "anthropic:claude-sonnet-4-5-20250929"
    """未显式指定 model_id/provider 时使用的主力模型。"""

    cheap_model_id: str = "anthropic:claude-haiku-4-5"
    """快慢路由（ModelRouterMiddleware）里"便宜/快速"这一档使用的模型。"""

    @classmethod
    def from_env(cls) -> "ProviderConfig":
        """从环境变量构造一份配置——这是不传参数时的默认行为。"""
        return cls(
            anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY"),
            openai_api_key=os.environ.get("OPENAI_API_KEY"),
            deepseek_api_key=os.environ.get("DEEPSEEK_API_KEY"),
            deepseek_base_url=os.environ.get("DEEPSEEK_BASE_URL"),
            is_production=os.environ.get("ENVIRONMENT", "").lower() == "production",
            preferred_language=os.environ.get("AGENT_PREFERRED_LANGUAGE"),
            default_model_id=os.environ.get(
                "AGENT_MODEL_RELIABLE",
                os.environ.get("AGENT_MODEL", "anthropic:claude-sonnet-4-5-20250929"),
            ),
            cheap_model_id=os.environ.get("AGENT_MODEL_CHEAP", "anthropic:claude-haiku-4-5"),
        )
