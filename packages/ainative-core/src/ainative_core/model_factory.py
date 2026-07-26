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

# enum是Python标准库里"枚举"的实现——枚举就是"把一组固定、有限的可能值，
# 用有意义的名字包装起来"，比如"供应商只能是这四种之一"，比起直接用
# 字符串到处比较（容易因为拼错字符串而产生bug），枚举能让IDE/类型检查
# 工具帮你发现"传了一个不存在的选项"这类错误。
# StrEnum是Python 3.11新增的枚举变体——每个枚举成员本身"就是"一个字符串
# （可以直接当字符串使用/比较/拼接），比老式`class X(str, Enum)`的写法
# 更简洁、行为更符合直觉。
from enum import StrEnum
from typing import Any

# 下面几行都是从LangChain这个第三方AI应用开发框架里导入的类型/函数：
# ModelFallbackMiddleware——LangChain提供的"模型调用失败时自动换备用
#   模型重试"中间件。
# init_chat_model——LangChain提供的"传入一个模型标识字符串（比如
#   'anthropic:claude-sonnet-4-5'），自动帮你构造出对应供应商SDK客户端"
#   的工厂函数，不需要自己为每个供应商分别写初始化代码。
# BaseChatModel——LangChain里"所有对话模型"的公共基类/类型。
# SystemMessage——代表一条"系统提示词"消息的数据结构。
from langchain.agents.middleware import ModelFallbackMiddleware
from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import SystemMessage

# 从本包自己的另外两个文件里，导入之前已经写好、这里要复用的类型。
from ainative_core.config import ProviderConfig
from ainative_core.protocols import UsageSink
from ainative_core.usage_tracking import UsageTrackingCallbackHandler

# 模块级别的常量——写在函数外面、大写命名的变量，表示"整个文件/项目
# 通用的固定数值"，方便统一修改、避免同一个数字散落在代码各处。
STRUCTURED_AGENT_TEMPERATURE = 0.2
# ↑ "结构化Agent任务"（比如要求模型输出严格遵守某种格式的结果）用的
#   temperature（模型生成时的"随机性/创造性"程度，0表示几乎不随机，
#   越大越发散）——0.2偏保守，适合需要稳定、可预测输出的场景。

DETERMINISTIC_TEMPERATURE = 0.0
# ↑ 完全确定性场景（比如同样输入必须尽量得到同样输出）用的temperature。

_NO_TEMPERATURE_MARKERS = ("reasoner", "o1", "o3", "o4")
"""这几类推理模型不支持自定义temperature，传了反而会报错，需要在拼kwargs时跳过。"""
# ↑ 这是一个"元组"（tuple，用小括号包起来的一组值，且创建后不能修改），
#   存了几个"关键词片段"——如果一个模型名字里包含这些片段之一，说明
#   它属于"推理模型"这一类，这类模型内部有自己的固定"思考"机制，不接受
#   外部传入temperature参数来调节随机性。


# class ModelProvider(StrEnum): 定义了一个叫ModelProvider的枚举类，
# 继承自StrEnum（见上面的详细解释）。下面每一行 `名字 = "值"` 就是
# 枚举的一个成员——比如`ModelProvider.ANTHROPIC`这个名字，实际值是
# 字符串"anthropic"。
class ModelProvider(StrEnum):
    ANTHROPIC = "anthropic"
    DEEPSEEK = "deepseek"
    OPENAI = "openai"
    AUTO = "auto"
    # ↑ AUTO是"没能识别出具体是哪个供应商"时的兜底值。


# 下面两个类都"继承"自RuntimeError（Python内置的"运行时错误"异常类型），
# 意味着它们本身就是一种可以被`raise`（抛出）、被`except`（捕获）的
# 异常，只是专门用来表达"这次失败具体是什么原因"，方便调用代码根据
# 异常的具体类型做出不同反应（比如"额度用尽"应该提示用户充值，
# "触发限流"应该建议稍后重试，两者处理方式完全不同）。
class LLMQuotaExhaustedError(RuntimeError):
    """底层供应商返回"额度已用尽"类错误时抛出，供上层区分于普通请求失败。"""


class LLMRateLimitError(RuntimeError):
    """底层供应商返回"触发限流"类错误时抛出，供上层区分于普通请求失败。"""


def _supports_temperature(model_id: str) -> bool:
    # `.lower()` 把字符串统一转成小写，这样后面判断"是否包含某个关键词"
    # 时，不用担心大小写不一致导致漏判（比如"O1"和"o1"应该被当作同一
    # 回事）。
    lowered = model_id.lower()
    # `any(条件 for x in 集合)` 是Python的"生成器表达式"写法，意思是
    # "只要这个集合里有任意一个元素满足条件，就返回True，否则返回False"。
    # 这里是"只要model_id里包含_NO_TEMPERATURE_MARKERS里任意一个关键词
    # 片段，就说明这是个不支持temperature的推理模型"。外层的`not`把
    # 结果取反——"支持temperature"等于"不属于推理模型"。
    return not any(marker in lowered for marker in _NO_TEMPERATURE_MARKERS)


def temperature_kwargs(model_id: str, temperature: float) -> dict[str, Any]:
    """按model_id决定要不要传temperature——推理模型不支持，传了会报错。"""
    if not _supports_temperature(model_id):
        # 不支持的话，返回一个空字典——意味着"什么参数都不额外传"，
        # 调用方后续会把这个空字典"摊开"合并进真正的调用参数里，等于
        # 什么都没加。
        return {}
    # 支持的话，返回一个只有一个key的字典，调用方之后会把它合并进
    # 真正传给供应商SDK的参数集合里。
    return {"temperature": temperature}


def _provider_of(model_id: str) -> ModelProvider:
    # 本框架约定model_id的格式是"供应商:具体模型名"，比如
    # "anthropic:claude-sonnet-4-5"——这里先判断字符串里有没有冒号，
    # 有的话用`.split(":", 1)`按第一个冒号切开，取切开后的第一段
    # （下标[0]）就是供应商名字；完全没有冒号的话，就用空字符串
    # 兜底（后面会因为匹配不到任何已知供应商，落到AUTO分支）。
    prefix = model_id.split(":", 1)[0] if ":" in model_id else ""
    # `try/except` 是Python处理"可能会出错的代码"的标准写法：先尝试
    # 执行try代码块，如果真的抛出了指定类型的异常（这里是ValueError），
    # 就转而执行except代码块，而不是让程序直接崩溃退出。
    try:
        # `ModelProvider(prefix)` 尝试把这个字符串转换成对应的枚举
        # 成员——如果prefix正好是"anthropic"/"deepseek"/"openai"/"auto"
        # 之一，就能成功转换；如果是任何别的字符串（比如拼写打错了，
        # 或者根本没有冒号），Python会抛出ValueError。
        return ModelProvider(prefix)
    except ValueError:
        # 转换失败，说明这不是一个已知的供应商前缀，统一归类为AUTO。
        return ModelProvider.AUTO


def _with_usage_tracking(
    extra_kwargs: dict[str, Any] | None,
    *,
    model_id: str,
    usage_sink: UsageSink | None,
    agent_name: str,
) -> dict[str, Any] | None:
    """如果传了`usage_sink`，在`extra_kwargs["callbacks"]`里追加一个用量采集回调
    （保留调用方自己可能已经传入的其他callbacks，不覆盖）。"""
    # 没有传usage_sink（说明调用方不关心用量统计），就原样把extra_kwargs
    # 返回，什么都不做——这是这个函数最简单的一条路径。
    if usage_sink is None:
        return extra_kwargs
    # `dict(extra_kwargs) if extra_kwargs else {}` 的意思是：如果
    # extra_kwargs不是None/不是空字典（"truthy"，Python里非空字典、
    # 非空字符串等都会被当作True），就用`dict(...)`复制一份（避免
    # 直接修改调用方传进来的原始字典），否则就新建一个空字典开始。
    kwargs = dict(extra_kwargs) if extra_kwargs else {}
    # 构造一个"用量采集回调处理器"实例——把这次调用相关的信息
    # （用哪个sink记录、哪个agent发起、哪个供应商、具体哪个model_id）
    # 都传给它，方便它之后在调用结束时知道该怎么记录这条用量事件。
    handler = UsageTrackingCallbackHandler(
        usage_sink, agent_name=agent_name, provider=_provider_of(model_id).value, model_id=model_id,
    )
    # `kwargs.get("callbacks", [])` 读取kwargs里已有的callbacks列表
    # （没有就当作空列表）；`[*已有列表, handler]` 用"展开语法"（星号）
    # 把已有列表的每一项都摊开放进新列表，再把新的handler追加到末尾——
    # 这样保证不会覆盖/丢失调用方本来就想挂的其他回调处理器。
    kwargs["callbacks"] = [*kwargs.get("callbacks", []), handler]
    return kwargs


def build_model(
    model_id: str,
    *,
    config: ProviderConfig | None = None,
    temperature: float = STRUCTURED_AGENT_TEMPERATURE,
    extra_kwargs: dict[str, Any] | None = None,
    usage_sink: UsageSink | None = None,
    agent_name: str = "unknown",
) -> BaseChatModel:
    """构建单个`BaseChatModel`，不含降级逻辑。

    `config`留空时从环境变量构造——这是大多数demo/测试场景的默认用法；
    真实项目如果有自己的配置系统，在启动时构造一次`ProviderConfig`传进来即可。

    `usage_sink`不为空时，会通过`callbacks`参数（而不是`model.with_config()`——
    后者返回`RunnableBinding`会破坏`BaseChatModel`类型不变量）挂一个用量采集
    回调，每次调用结束后自动把token用量记进`usage_sink`。
    """
    # `config or ProviderConfig.from_env()` ——如果调用方传了config就用
    # 调用方传的，没传（config是None，Python里None相当于False）就调用
    # from_env()现场从环境变量构造一份。
    cfg = config or ProviderConfig.from_env()
    # 先算出"要不要传temperature、传多少"，用`dict(...)`包一层是为了
    # 拿到一份独立的新字典，后面还会继续往这个字典里塞别的参数。
    kwargs: dict[str, Any] = dict(temperature_kwargs(model_id, temperature))

    # 判断这次调用具体是哪个供应商，然后按供应商分别决定要不要往kwargs
    # 里塞对应的API密钥——`if/elif/elif`是"依次判断多个互斥条件"的写法，
    # 只有第一个满足的分支会被执行，后面的会被跳过。
    provider = _provider_of(model_id)
    if provider is ModelProvider.ANTHROPIC and cfg.anthropic_api_key:
        # 只有"确实是Anthropic供应商"且"确实配置了对应密钥"两个条件同时
        # 满足，才把密钥塞进kwargs——避免在没配置密钥时传一个None进去。
        kwargs["api_key"] = cfg.anthropic_api_key
    elif provider is ModelProvider.OPENAI and cfg.openai_api_key:
        kwargs["api_key"] = cfg.openai_api_key
    elif provider is ModelProvider.DEEPSEEK:
        # DeepSeek这里拆成了两个独立的if（而不是接着用elif的"二选一"
        # 逻辑），因为api_key和base_url是两件互相独立的事——可能只配置
        # 了密钥、没配置自定义地址（用官方默认地址），也可能两者都配置了。
        if cfg.deepseek_api_key:
            kwargs["api_key"] = cfg.deepseek_api_key
        if cfg.deepseek_base_url:
            kwargs["base_url"] = cfg.deepseek_base_url

    # 调用前面写好的辅助函数，看看要不要挂用量采集回调，挂好之后重新
    # 赋值回extra_kwargs这个变量（可能返回的还是原来的值，也可能是
    # 追加了callbacks之后的新字典）。
    extra_kwargs = _with_usage_tracking(extra_kwargs, model_id=model_id, usage_sink=usage_sink, agent_name=agent_name)
    if extra_kwargs:
        # `dict.update(另一个字典)` 会把另一个字典里的所有键值对，合并
        # 进当前字典——如果key相同就覆盖，不同就新增。这里把
        # extra_kwargs（调用方自定义的额外参数+可能追加的callbacks）
        # 合并进已经准备好的kwargs里。
        kwargs.update(extra_kwargs)

    # `**kwargs` 是"关键字参数展开"语法——把字典里的每一对key:value，
    # 变成"key=value"的形式传给函数，等价于手动写出
    # `init_chat_model(model_id, temperature=0.2, api_key="xxx", ...)`
    # 这一长串，只是用字典统一收集起来更灵活。
    return init_chat_model(model_id, **kwargs)


def build_cheap_model(
    *,
    config: ProviderConfig | None = None,
    extra_kwargs: dict[str, Any] | None = None,
    usage_sink: UsageSink | None = None,
    agent_name: str = "unknown",
) -> BaseChatModel:
    """构建"便宜/快速"档模型，供路由类中间件在低风险场景下降级使用。"""
    cfg = config or ProviderConfig.from_env()
    # 直接复用上面的build_model函数，只是把model_id换成配置里指定的
    # "便宜档"模型——这是很常见的代码组织方式：用一个通用的底层函数，
    # 上面包一层"预设好某些参数"的专用函数，减少重复代码。
    return build_model(
        cfg.cheap_model_id,
        config=cfg,
        temperature=STRUCTURED_AGENT_TEMPERATURE,
        extra_kwargs=extra_kwargs,
        usage_sink=usage_sink,
        agent_name=agent_name,
    )


def build_agent_model(
    *,
    config: ProviderConfig | None = None,
    model_id: str | None = None,
    temperature: float = STRUCTURED_AGENT_TEMPERATURE,
    usage_sink: UsageSink | None = None,
    agent_name: str = "unknown",
) -> BaseChatModel:
    """构建主力Agent模型，超时/重试走LangChain默认的`init_chat_model`参数。"""
    cfg = config or ProviderConfig.from_env()
    # `model_id or cfg.default_model_id` ——调用方传了具体model_id就用
    # 调用方的，没传（是None）就用配置里的默认模型。
    resolved_id = model_id or cfg.default_model_id
    return build_model(
        resolved_id,
        config=cfg,
        temperature=temperature,
        # 这里直接写死了timeout（超时时间，单位秒）和max_retries
        # （最多自动重试几次）——"主力Agent模型"这个使用场景默认需要
        # 这两个稳定性参数，而build_cheap_model没有传，是因为"便宜/
        # 快速"档通常用在更轻量、可以接受不同超时策略的场景。
        extra_kwargs={"timeout": 120, "max_retries": 1},
        usage_sink=usage_sink,
        agent_name=agent_name,
    )


def build_agent_model_with_fallback(
    *,
    config: ProviderConfig | None = None,
    model_id: str | None = None,
    usage_sink: UsageSink | None = None,
    agent_name: str = "unknown",
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

    `usage_sink`提供时，主力模型和每一个备用模型都会各自挂上用量采集回调
    ——降级发生时，实际执行调用的备用模型的用量同样会被记录，不会因为
    降级而在用量统计里"消失"。
    """
    cfg = config or ProviderConfig.from_env()
    # 先构建"主力模型"——这是正常情况下会被使用的那一个。
    primary = build_agent_model(config=cfg, model_id=model_id, usage_sink=usage_sink, agent_name=agent_name)

    # 准备一个空列表，用来收集"配置好了密钥、可以作为备用选项"的模型。
    fallbacks: list[BaseChatModel] = []
    if cfg.openai_api_key:
        # 只有真的配置了OpenAI密钥，才会构建这个备用模型（没配置密钥
        # 硬要构建，调用真实API时会因为缺少密钥直接失败，不如一开始
        # 就不把它加进候选列表）。
        fallbacks.append(build_model("openai:gpt-4o", config=cfg, usage_sink=usage_sink, agent_name=agent_name))
    if cfg.deepseek_api_key and cfg.preferred_language not in ("ja", "zh"):
        # DeepSeek同样需要检查密钥；额外多了一个条件——如果部署语言是
        # 日语或中文，就跳过DeepSeek这个备用选项（可能是因为特定语言下
        # 这家供应商的表现不够理想，`preferred_language not in (...)`
        # 表示"当前语言不在这个需要排除的列表里"）。
        fallbacks.append(
            build_model("deepseek:deepseek-chat", config=cfg, usage_sink=usage_sink, agent_name=agent_name)
        )

    if not fallbacks:
        # 一个备用模型都没能成功配置（比如只填了Anthropic密钥，没填
        # 别的），就明确返回None表示"没有降级链路可用"，而不是返回一个
        # 空的Middleware（那样反而容易让调用方误以为"有降级机制，只是
        # 恰好没有触发"）。
        return primary, None

    # `*fallbacks` 把列表展开成一个个独立的参数，等价于手动写
    # `ModelFallbackMiddleware(model1, model2, ...)`。
    return primary, ModelFallbackMiddleware(*fallbacks)


def get_summarization_config(model: BaseChatModel) -> dict[str, Any]:
    """根据模型的上下文窗口，决定"何时触发摘要压缩、保留多少条最近消息"。

    优先用模型`profile`里声明的`max_input_tokens`按比例算（55%触发/20%保留——
    低于常见的85%阈值，是为了在长任务里更早触发压缩，避免临近上限才压缩导致
    单次压缩过重）；模型没有声明`profile`时退化成固定token数阈值。
    """
    max_input_tokens = None
    # `getattr(对象, "属性名", 默认值)` ——见usage_tracking.py里的详细
    # 解释：安全地尝试读取model这个对象是否有profile这个属性，没有就
    # 用None代替，避免因为不同模型实现细节不同而直接报错。
    profile = getattr(model, "profile", None)
    if isinstance(profile, dict):
        max_input_tokens = profile.get("max_input_tokens")

    if max_input_tokens:
        # 找到了具体的上下文窗口大小，就按比例动态计算：
        # - 累计token达到窗口的55%时就触发摘要压缩（不是等到接近100%
        #   才压缩，提前留出安全余量）。
        # - 压缩后保留最近的消息数量，按窗口大小的20%换算成"大概多少条
        #   消息"（每条消息粗略按500 token估算），但至少保留4条，避免
        #   窗口特别小的模型算出"保留0条"这种荒谬结果。
        return {
            "max_tokens_before_summary": int(max_input_tokens * 0.55),
            "messages_to_keep": max(4, int(max_input_tokens * 0.20 / 500)),
        }

    # 没有拿到具体的窗口大小信息，就用一组固定的保守默认值兜底。
    return {"max_tokens_before_summary": 40_000, "messages_to_keep": 8}


def make_cached_system_prompt(text: str) -> SystemMessage:
    """把纯文本包装成带Anthropic显式prompt caching标记的`SystemMessage`。"""
    # Anthropic的API支持"提示词缓存"——如果同一段system prompt在多次
    # 调用里完全一样，标记了cache_control之后，供应商那边可以复用之前
    # 已经处理过的结果，省去重复计算的成本。这里把普通文本包装成
    # LangChain要求的"content是一个列表，每一项是带类型标记的字典"
    # 这种格式，并加上`"cache_control": {"type": "ephemeral"}`这个
    # 具体的缓存标记。
    return SystemMessage(
        content=[{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}]
    )
