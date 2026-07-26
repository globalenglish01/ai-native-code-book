"""通用配置读取——不绑定任何具体项目的 Pydantic Settings 类。

真实项目里，`model_factory.py` 原本直接 `from app.config.settings import
settings` 去读一个和整个项目其他配置（数据库/MinIO/SMTP等）混在一起的巨大
Settings类。这里改成一个只关心"模型调用需要的那几项"的独立小配置对象，
默认从环境变量构造，真实项目如果想接入自己的配置系统，只需要在启动时
构造一个 `ProviderConfig` 实例并传给需要它的函数，而不需要让 ainative-core
知道任何关于这个真实项目配置系统的细节。
"""

# 这一行是Python的"未来特性"声明：让下面所有类型注解（比如函数参数、
# 返回值上写的类型）都不需要在写代码的时候真正存在，Python解释器只把它们
# 当作字符串暂存，等真正需要检查类型时（比如IDE/mypy）才去解析。
# 好处：`from_env(cls) -> ProviderConfig` 这种"函数还没定义完就引用自己
# 类名"的写法，不加这一行在旧版本Python里会报错，加了就没问题。
from __future__ import annotations

# os模块是Python标准库自带的，用来读取"环境变量"——环境变量是在启动程序
# 之前，由操作系统/终端/部署平台设置好的一些"全局小纸条"，比如
# ANTHROPIC_API_KEY=sk-xxx 这种，程序运行时可以读到它。这样密钥就不用
# 写死在代码里，代码可以公开分享，密钥本身另外配置。
import os

# dataclass是Python标准库提供的一个"装饰器"（写在类前面的@xxx），
# 它的作用是自动帮你生成一个类的__init__（构造函数）、__repr__（打印时
# 显示的样子）等样板代码，让你只需要声明"这个类有哪些字段"，不用自己手写
# 一大堆重复代码。
from dataclasses import dataclass


# @dataclass(frozen=True) 这行是"装饰器"语法，意思是：
# 1. dataclass——自动生成构造函数等样板代码（见上面注释）。
# 2. frozen=True——"冻结"这个类的实例，一旦创建出来，字段就不能再被修改
#    （比如 cfg.anthropic_api_key = "new_value" 会直接报错）。这样保证
#    配置对象一旦构造完成，运行过程中不会被意外篡改，出问题时更容易排查。
@dataclass(frozen=True)
class ProviderConfig:
    """模型调用相关的最小配置集合。

    默认构造函数 `from_env()` 从环境变量读取，字段名和常见的
    LangChain/供应商官方SDK约定的环境变量名保持一致（如 `ANTHROPIC_API_KEY`），
    方便真实项目直接复用已有的 `.env` 文件。
    """

    # 下面这些是这个类的"字段"（也叫"属性"）——每一行 `字段名: 类型 = 默认值`
    # 的写法，dataclass会自动帮你变成构造函数里的一个参数。
    # `str | None` 的意思是"这个字段要么是字符串，要么是None（表示空/没有）"，
    # `= None` 是默认值——意味着构造时如果不传这个参数，它就是None。

    anthropic_api_key: str | None = None
    # ↑ Anthropic（Claude模型的公司）的API密钥——没配置就是None。

    openai_api_key: str | None = None
    # ↑ OpenAI（GPT模型的公司）的API密钥。

    deepseek_api_key: str | None = None
    # ↑ DeepSeek（深度求索，一家国产大模型公司）的API密钥。

    deepseek_base_url: str | None = None
    # ↑ DeepSeek接口的服务器地址——如果用的是官方以外的中转/自建服务，
    #   可以通过这个字段指定不同的地址。

    is_production: bool = False
    """是否生产环境——用于决定要不要对某些危险配置做更严格的拦截。"""
    # ↑ bool类型只有两个值：True（真/是）或False（假/否）。默认False，
    #   意味着"默认当作不是生产环境"（更宽松），除非显式配置成生产环境。

    preferred_language: str | None = None
    """部署语言（如 "ja"/"zh"），用于决定是否跳过对某些语言支持较弱的供应商。"""

    default_model_id: str = "anthropic:claude-sonnet-4-5-20250929"
    """未显式指定 model_id/provider 时使用的主力模型。"""
    # ↑ 这个字段类型是`str`（不是`str | None`），意味着它永远有一个真实的
    #   字符串值——即使调用方没有配置，也会用等号后面写的这个默认值兜底，
    #   保证"到底要用哪个模型"这件事永远有明确答案，不会是"不知道"。

    cheap_model_id: str = "anthropic:claude-haiku-4-5"
    """快慢路由（ModelRouterMiddleware）里"便宜/快速"这一档使用的模型。"""

    # @classmethod 是另一个装饰器，表示下面这个函数是"类方法"——
    # 调用方式是 `ProviderConfig.from_env()`（直接在类名上调用），而不是
    # 先创建一个实例再调用。类方法的第一个参数习惯写成`cls`（class的缩写），
    # 代表"这个类本身"（这里就是ProviderConfig类）。
    @classmethod
    def from_env(cls) -> ProviderConfig:
        """从环境变量构造一份配置——这是不传参数时的默认行为。"""
        # `cls(...)` 等价于 `ProviderConfig(...)`——用cls而不是直接写死
        # 类名，是Python里类方法的标准写法（这样子类继承时也能正确工作）。
        return cls(
            # `os.environ.get("变量名")` 表示"去读取这个环境变量的值，
            # 读不到就返回None（不会报错）"——这是比 `os.environ["变量名"]`
            # （读不到会直接崩溃报错）更安全的读取方式。
            anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY"),
            openai_api_key=os.environ.get("OPENAI_API_KEY"),
            deepseek_api_key=os.environ.get("DEEPSEEK_API_KEY"),
            deepseek_base_url=os.environ.get("DEEPSEEK_BASE_URL"),
            # 这一行做了两件事：先读环境变量`ENVIRONMENT`（读不到就当作
            # 空字符串""），再用`.lower()`把它统一转成小写，最后判断是不是
            # 等于字符串"production"——这样不管用户配置的是"Production"
            # 还是"PRODUCTION"还是"production"，都能正确识别成"是生产环境"。
            is_production=os.environ.get("ENVIRONMENT", "").lower() == "production",
            preferred_language=os.environ.get("AGENT_PREFERRED_LANGUAGE"),
            # 这里出现了"环境变量的回退链"——外层的`os.environ.get(A, 默认值)`
            # 的"默认值"本身又是另一个`os.environ.get(...)`调用，形成了
            # "优先读AGENT_MODEL_RELIABLE，读不到就退而读AGENT_MODEL，
            # 两个都读不到就用写死的默认模型名"这样一条三层回退链。
            default_model_id=os.environ.get(
                "AGENT_MODEL_RELIABLE",
                os.environ.get("AGENT_MODEL", "anthropic:claude-sonnet-4-5-20250929"),
            ),
            cheap_model_id=os.environ.get("AGENT_MODEL_CHEAP", "anthropic:claude-haiku-4-5"),
        )
