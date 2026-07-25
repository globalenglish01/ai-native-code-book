"""供应商无关的LangChain模型工厂——统一构建`BaseChatModel`，支持跨厂商自动降级。

改造自真实生产项目里被反复验证过的`model_factory.py`，去掉了两类"项目专属、
不适合放进通用框架"的耦合：
1. 直接 `from app.config.settings import settings` 读取和数据库/MinIO/SMTP等
   混在一起的巨大Settings类——改成显式传入/默认从环境变量构造的`ProviderConfig`。
2. `_prod_safe_model_id()`里那段"browser-llm本地开发临时shim"的专属逻辑——
   这是原项目自己的本地开发工具分支，和"模型工厂"这个通用职责无关，直接丢弃。

**必须原样保留的关键设计约束（ch53-01历史事故的教训）**：
跨厂商降级不能用 `primary.with_fallbacks([...])`。`.with_fallbacks()` 返回的
`RunnableWithFallbacks` 不是 `BaseChatModel` 的子类，如果把这个对象直接传给
`create_agent(model=...)`，凡是内部会对 `model` 做 `BaseChatModel | str` 类型检查
（比如尝试把它当字符串spec处理、调用`.count(":")`之类）的框架代码都会直接抛
`AttributeError`，导致 agent 100% 创建失败。正确做法是用
`langchain.agents.middleware.ModelFallbackMiddleware`：`primary`参数本身永远是
一个货真价实的`BaseChatModel`，降级逻辑放在 middleware 层，通过
`request.override(model=...)`在实际调用失败时换模型重试，类型检查天然通过。
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from langchain.agents.middleware import ModelFallbackMiddleware
from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import SystemMessage

from ainative_core.config import ProviderConfig

STRUCTURED_AGENT_TEMPERATURE = 0.2
DETERMINISTIC_TEMPERATURE = 0.0

_NO_TEMPERATURE_MARKERS = ("reasoner", "o1", "o3", "o4")
"""这几类推理模型不支持自定义temperature，传了反而会报错，需要在拼kwargs时跳过。"""


class ModelProvider(str, Enum):
    ANTHROPIC = "anthropic"
    DEEPSEEK = "deepseek"
    OPENAI = "openai"
    AUTO = "auto"


class LLMQuotaExhaustedError(RuntimeError):
    """底层供应商返回"额度已用尽"类错误时抛出，供上层区分于普通请求失败。"""


class LLMRateLimitError(RuntimeError):
    """底层供应商返回"触发限流"类错误时抛出，供上层区分于普通请求失败。"""


def _supports_temperature(model_id: str) -> bool:
    lowered = model_id.lower()
    return not any(marker in lowered for marker in _NO_TEMPERATURE_MARKERS)


def temperature_kwargs(model_id: str, temperature: float) -> dict[str, Any]:
    """按model_id决定要不要传temperature——推理模型不支持，传了会报错。"""
    if not _supports_temperature(model_id):
        return {}
    return {"temperature": temperature}


def _provider_of(model_id: str) -> ModelProvider:
    prefix = model_id.split(":", 1)[0] if ":" in model_id else ""
    try:
        return ModelProvider(prefix)
    except ValueError:
        return ModelProvider.AUTO


def build_model(
    model_id: str,
    *,
    config: ProviderConfig | None = None,
    temperature: float = STRUCTURED_AGENT_TEMPERATURE,
    extra_kwargs: dict[str, Any] | None = None,
) -> BaseChatModel:
    """构建单个`BaseChatModel`，不含降级逻辑。

    `config`留空时从环境变量构造——这是大多数demo/测试场景的默认用法；
    真实项目如果有自己的配置系统，在启动时构造一次`ProviderConfig`传进来即可。
    """
    cfg = config or ProviderConfig.from_env()
    kwargs: dict[str, Any] = dict(temperature_kwargs(model_id, temperature))

    provider = _provider_of(model_id)
    if provider is ModelProvider.ANTHROPIC and cfg.anthropic_api_key:
        kwargs["api_key"] = cfg.anthropic_api_key
    elif provider is ModelProvider.OPENAI and cfg.openai_api_key:
        kwargs["api_key"] = cfg.openai_api_key
    elif provider is ModelProvider.DEEPSEEK:
        if cfg.deepseek_api_key:
            kwargs["api_key"] = cfg.deepseek_api_key
        if cfg.deepseek_base_url:
            kwargs["base_url"] = cfg.deepseek_base_url

    if extra_kwargs:
        kwargs.update(extra_kwargs)

    return init_chat_model(model_id, **kwargs)


def build_cheap_model(
    *, config: ProviderConfig | None = None, extra_kwargs: dict[str, Any] | None = None
) -> BaseChatModel:
    """构建"便宜/快速"档模型，供路由类中间件在低风险场景下降级使用。"""
    cfg = config or ProviderConfig.from_env()
    return build_model(
        cfg.cheap_model_id,
        config=cfg,
        temperature=STRUCTURED_AGENT_TEMPERATURE,
        extra_kwargs=extra_kwargs,
    )


def build_agent_model(
    *,
    config: ProviderConfig | None = None,
    model_id: str | None = None,
    temperature: float = STRUCTURED_AGENT_TEMPERATURE,
) -> BaseChatModel:
    """构建主力Agent模型，超时/重试走LangChain默认的`init_chat_model`参数。"""
    cfg = config or ProviderConfig.from_env()
    resolved_id = model_id or cfg.default_model_id
    return build_model(
        resolved_id,
        config=cfg,
        temperature=temperature,
        extra_kwargs={"timeout": 120, "max_retries": 1},
    )


def build_agent_model_with_fallback(
    *, config: ProviderConfig | None = None, model_id: str | None = None
) -> tuple[BaseChatModel, ModelFallbackMiddleware | None]:
    """构建主力模型 + 跨厂商自动降级用的`ModelFallbackMiddleware`。

    ch53-01教训（详见模块docstring）：这里绝对不能改成
    `primary.with_fallbacks([...])`——那会返回一个非`BaseChatModel`的
    `RunnableWithFallbacks`，传给`create_agent(model=...)`后，任何对`model`
    做`BaseChatModel | str`类型检查的框架代码都会崩溃。正确用法：
    `create_agent(model=primary, middleware=[..., fallback_mw])`，
    `primary`本身永远是货真价实的`BaseChatModel`。

    返回`(primary, None)`表示没有配置任何备用供应商凭证——调用方应该
    据此决定是否要把`fallback_mw`加进middleware列表。
    """
    cfg = config or ProviderConfig.from_env()
    primary = build_agent_model(config=cfg, model_id=model_id)

    fallbacks: list[BaseChatModel] = []
    if cfg.openai_api_key:
        fallbacks.append(build_model("openai:gpt-4o", config=cfg))
    if cfg.deepseek_api_key and cfg.preferred_language not in ("ja", "zh"):
        fallbacks.append(build_model("deepseek:deepseek-chat", config=cfg))

    if not fallbacks:
        return primary, None

    return primary, ModelFallbackMiddleware(*fallbacks)


def get_summarization_config(model: BaseChatModel) -> dict[str, Any]:
    """根据模型的上下文窗口，决定"何时触发摘要压缩、保留多少条最近消息"。

    优先用模型`profile`里声明的`max_input_tokens`按比例算（55%触发/20%保留——
    低于常见的85%阈值，是为了在长任务里更早触发压缩，避免临近上限才压缩导致
    单次压缩过重）；模型没有声明`profile`时退化成固定token数阈值。
    """
    max_input_tokens = None
    profile = getattr(model, "profile", None)
    if isinstance(profile, dict):
        max_input_tokens = profile.get("max_input_tokens")

    if max_input_tokens:
        return {
            "max_tokens_before_summary": int(max_input_tokens * 0.55),
            "messages_to_keep": max(4, int(max_input_tokens * 0.20 / 500)),
        }

    return {"max_tokens_before_summary": 40_000, "messages_to_keep": 8}


def make_cached_system_prompt(text: str) -> SystemMessage:
    """把纯文本包装成带Anthropic显式prompt caching标记的`SystemMessage`。"""
    return SystemMessage(
        content=[{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}]
    )
