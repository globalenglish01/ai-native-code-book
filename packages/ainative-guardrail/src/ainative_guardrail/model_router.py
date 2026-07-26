"""按每轮对话复杂度打分，路由到便宜/快速模型或主力模型。

改造自真实生产项目里验证过的评分式模型路由中间件。核心机制不变：
LangChain中间件的`wrap_model_call(request, handler)`允许通过
`handler(request.override(model=...))`按次替换模型——`ModelFallbackMiddleware`
证明了这个机制，本中间件用同样的手法做"主动选择更便宜模型"而不是
"失败后被动降级"。

提取时的改动：把原版直接`from app.agents._utils.model_factory import
build_model, _prod_safe_model_id`（项目专属耦合+本地dev-shim防护逻辑）
换成统一走`ainative_core.model_factory.build_model`，不再依赖任何
项目专属的生产环境护栏分支（该护栏逻辑是TestAgentPythonProject自己的
本地开发工具防护，不属于通用框架职责）。

ch67-01教训：任何承担成本控制职责的路由机制，必须确保系统里不存在能够
绕开它的替代路径——本中间件构造"便宜模型"时统一走`build_model()`，
不裸调`init_chat_model`，避免重蹈"多处直接调用底层接口绕开护栏"的覆辙。
"""

from __future__ import annotations

# logging是Python标准库自带的"日志"模块——比起用print直接打印，logging
# 可以区分"这条消息有多重要"（DEBUG/INFO/WARNING/ERROR等级别），也方便
# 统一控制"运行时到底要不要真的把这条消息输出出来、输出到哪里"。
import logging

# os模块用来读取"环境变量"——见ainative_core.config模块的详细解释：
# 环境变量是启动程序之前，由操作系统/部署平台设置好的"全局小纸条"。
import os

# Awaitable/Callable 用来给"函数本身"写类型注解：
# - Callable[[参数类型], 返回类型] 表示"一个接收指定参数、返回指定类型的
#   函数"。
# - Awaitable[X] 表示"一个可以被await的东西，await之后会得到类型X的结果"——
#   通常用来标注"这是一个async函数"的返回值类型。
from collections.abc import Awaitable, Callable

# Any表示"这里可以是任意类型"，用在不方便/不需要精确约束类型的地方。
from typing import Any

# 从本框架的地基包ainative_core里，导入配置类型和模型工厂函数——见
# ainative_core包对应文件的详细注释。
from ainative_core.config import ProviderConfig
from ainative_core.model_factory import (
    STRUCTURED_AGENT_TEMPERATURE,
    build_model,
    temperature_kwargs,
)

# 下面几行都是从LangChain这个第三方AI应用开发框架里导入的类型：
# AgentMiddleware——所有"中间件"类都必须继承的基类，中间件的作用是在
#   "模型调用/工具调用真正发生"的前后插入自己的逻辑（比如本文件要做的
#   "根据这一轮对话复杂度，决定要不要换一个更便宜的模型"）。
# ModelRequest——代表"即将发起的一次模型调用"的完整信息（包含消息历史、
#   原本打算用哪个模型等）。
# ModelResponse——代表"一次模型调用的结果"。
from langchain.agents.middleware.types import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, ToolMessage

# `logging.getLogger(__name__)` 创建（或复用）一个"以当前模块路径命名"
# 的日志记录器——`__name__`是Python自动帮每个模块填好的一个特殊变量，
# 值就是这个模块的完整导入路径（比如"ainative_guardrail.model_router"）。
# 这样不同模块打的日志，能清楚看出是从哪个文件发出来的。
logger = logging.getLogger(__name__)

# 下面两个元组（tuple，用小括号包起来的一组值，创建后不能修改）分别存了
# "暗示这一轮对话比较复杂"和"暗示这一轮对话比较简单"的关键词片段——如果
# 用户最新一条消息里包含这些词，就作为打分时的一个信号。
_COMPLEX_KEYWORDS = (
    "debug", "fix", "analyze", "why", "error", "failed", "broken",
    "investigate", "root cause", "stuck", "diagnose",
)
_SIMPLE_KEYWORDS = (
    "list", "show", "check", "say", "reply", "ok", "yes", "no",
    "confirm", "status", "summarize",
)

# frozenset是"不可变的集合"——集合（set）的特点是"内容无序、不允许重复
# 元素、判断'某个值是否在里面'的速度非常快"，frozen（冻结）意味着创建
# 之后不能再增删元素，适合这里这种"一组固定不变的工具名单"的场景。
# `frozenset({...})` 这里的`{...}`是"集合字面量"写法，花括号里用逗号
# 分隔一组值。
_ANALYTICAL_TOOLS = frozenset({"browser_evaluate", "browser_console_messages", "browser_network_requests"})
# ↑ "分析类"工具——调用这些工具通常意味着AI正在做比较深入的排查/分析，
#   是"这一轮偏复杂"的信号。
_PRIMITIVE_TOOLS = frozenset({
    "browser_click", "browser_navigate", "browser_type", "browser_press_key", "browser_hover",
    "read_file", "ls", "glob", "grep", "write_file", "edit_file",
})
# ↑ "基础操作类"工具——调用这些工具通常只是执行简单直接的动作，是"这一轮
#   偏简单"的信号。


def _extract_text(content: Any) -> str:
    # 一条消息的`content`字段在LangChain里可能有两种形状：要么是一个
    # 普通字符串，要么是一个"结构化内容块"列表（比如同时包含文本、图片
    # 引用等不同类型的部分）。这个函数统一把两种情况都转换成"纯文本、
    # 全部小写"的字符串，方便后面统一用"关键词是否出现"来判断。
    if isinstance(content, str):
        # `.lower()` 把字符串转成全小写，避免后面比较关键词时因为大小写
        # 不一致而漏判（比如"Debug"和"debug"应该被当作同一回事）。
        return content.lower()
    if isinstance(content, list):
        # 这是一个"列表推导式"（list comprehension）——一种用一行代码
        # "遍历一个集合、对每一项做点什么、把结果收集成一个新列表"的
        # 简洁写法，等价于写一个for循环不断往一个空列表里append结果，
        # 只是更紧凑。这里的意思是："遍历content里的每一个block，如果
        # block是字典且它的type字段是'text'，就取出它的text字段（取不到
        # 就用空字符串），最终收集成一个字符串列表parts"。
        parts = [
            str(block.get("text", ""))
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        # `" ".join(parts)` 把列表里的多个字符串，用一个空格拼接成一个
        # 完整的长字符串。
        return " ".join(parts).lower()
    # 两种已知形状都不匹配（比如content是None或别的奇怪类型），保守地
    # 返回空字符串，而不是报错。
    return ""


# class ModelRouterMiddleware(AgentMiddleware): 表示这个类"继承"自
# AgentMiddleware——自动获得中间件框架已经写好的基础能力，同时通过
# "重写"（覆盖）`wrap_model_call`/`awrap_model_call`这两个方法，加上
# 自己的路由逻辑。
class ModelRouterMiddleware(AgentMiddleware):
    """按复杂度打分，把简单轮次路由到便宜模型，复杂轮次留给主力模型。

    Args:
        parent_agent_name: 仅用于日志标识，区分是哪个agent的路由决策。
        config: 供应商配置，留空则从环境变量构造。
        cheap_model_id: 便宜模型的`provider:model_id`，留空则用`config.cheap_model_id`。
        threshold: 路由阈值，score小于等于该值时路由到便宜模型。默认0，
            意味着至少需要一个"简单"信号才会被路由到便宜模型。
    """

    def __init__(
        self,
        parent_agent_name: str,
        *,
        config: ProviderConfig | None = None,
        cheap_model_id: str | None = None,
        threshold: int | None = None,
    ) -> None:
        # `super().__init__()` 表示"先执行父类（AgentMiddleware）自己的
        # 构造逻辑"——子类重写`__init__`时，通常都应该先调用一次父类的，
        # 确保父类内部需要的状态被正确初始化。
        super().__init__()
        # `config or ProviderConfig.from_env()` ——如果调用方传了config
        # 就用调用方传的，没传（config是None，Python里None相当于False）
        # 就现场从环境变量构造一份。
        cfg = config or ProviderConfig.from_env()
        cheap_id = cheap_model_id or cfg.cheap_model_id
        # 提前在构造函数里把"便宜模型"这个对象真正创建出来（而不是每次
        # 需要路由时才现场创建）——这样每一轮对话真正触发路由时，直接
        # 复用这个已经准备好的模型实例，不需要重复初始化的开销。
        # 类型注解`: BaseChatModel`只是给人/工具看的说明，不影响运行。
        self._cheap_model: BaseChatModel = build_model(
            cheap_id,
            config=cfg,
            temperature=STRUCTURED_AGENT_TEMPERATURE,
            extra_kwargs=temperature_kwargs(cheap_id, STRUCTURED_AGENT_TEMPERATURE),
        )
        # 三元表达式`A if 条件 else B`——如果threshold不是None（调用方
        # 显式传了值），就用threshold；否则读取环境变量
        # AGENT_MODEL_ROUTER_THRESHOLD（读不到就用字符串"0"兜底），再用
        # `int(...)`把它从字符串转换成整数。
        self._threshold = (
            threshold if threshold is not None
            else int(os.environ.get("AGENT_MODEL_ROUTER_THRESHOLD", "0"))
        )
        self._parent_name = parent_agent_name

    def _score(self, request: ModelRequest) -> tuple[int, dict[str, int]]:
        # `getattr(对象, "属性名", 默认值)` ——尝试读取request的messages
        # 属性，读不到就用None代替；`(... or [])`再把"读到了但恰好是None"
        # 的情况也兜底成空列表；最外层`list(...)`确保拿到的是一个真正的
        # 列表（而不是别的"可迭代但不是list"的对象），方便后面用下标
        # 访问（比如`msgs[-1]`）。
        msgs = list(getattr(request, "messages", None) or [])
        # score是"这一轮对话复杂度"的累计打分——起点是0，后面根据各种
        # 信号累加或者扣减。signals字典记录"具体是哪些信号，各自贡献了
        # 多少分"，纯粹用于日志里展示，方便排查"为什么这次路由到了
        # 某个模型"。
        score = 0
        signals: dict[str, int] = {}

        # `len(msgs)` 是这一轮对话总共有多少条消息。
        n = len(msgs)
        if n <= 3:
            # 消息很少——通常意味着这是对话刚开始，任务大概率还比较简单，
            # 减分（更倾向于路由到便宜模型）。
            score -= 2
            signals["history_short"] = -2
        elif n >= 10:
            # 消息很多——通常意味着已经经过了不少轮交互，任务可能比较
            # 复杂/棘手，加分（更倾向于用主力模型）。
            score += 2
            signals["history_long"] = 2

        # `msgs[-1] if msgs else None` ——如果msgs不是空列表，取最后一条
        # 消息（Python里下标-1表示"倒数第一个"）；如果msgs是空列表，
        # 直接用None代替，避免对空列表取下标导致报错。
        last = msgs[-1] if msgs else None
        # `isinstance(x, 类型)` 检查x是不是某个类型（或其子类）的实例——
        # 这里判断"最后一条消息是不是一次工具调用的结果"。
        if isinstance(last, ToolMessage):
            if getattr(last, "status", None) == "error":
                # 上一次工具调用失败了——说明当前遇到了问题，需要更强的
                # 模型来排查，加分。
                score += 3
                signals["last_tool_error"] = 3
            # `last.name or ""` ——工具消息的name字段可能是None，用空
            # 字符串兜底，避免后面判断"是否在某个集合里"时因为None报错。
            tname = last.name or ""
            if tname in _ANALYTICAL_TOOLS:
                score += 3
                # f-string（前缀f的字符串）允许直接在字符串里用花括号
                # `{变量名}`嵌入变量的值——这里拼出类似
                # "analytical_tool:browser_evaluate"这样的信号名称。
                signals[f"analytical_tool:{tname}"] = 3
            elif tname in _PRIMITIVE_TOOLS:
                score -= 1
                signals[f"simple_tool:{tname}"] = -1

        # `sum(1 for m in msgs if isinstance(m, ToolMessage))` 是一个
        # "生成器表达式"（写法和列表推导式很像，但用小括号而不是方括号，
        # 且不会一次性在内存里生成一个完整列表，而是"边算边给"）：
        # 对msgs里每一条是ToolMessage的消息，贡献一个1，`sum(...)`把这些
        # 1加起来——效果就是"数一数这一轮总共有多少条工具调用结果"。
        tool_count = sum(1 for m in msgs if isinstance(m, ToolMessage))
        if tool_count < 3:
            # 工具调用次数很少——可能是一个直接能回答的简单问题，减分。
            score -= 1
            signals["few_tool_calls"] = -1
        elif tool_count > 8:
            # 工具调用次数很多——说明任务需要不断尝试/排查，加分。
            score += 2
            signals["many_tool_calls"] = 2

        # `next(生成器表达式, 默认值)` ——从一个生成器里取出"第一个满足
        # 条件的值"，取不到（生成器提前耗尽）就用默认值None代替。
        # `reversed(msgs)` 把消息列表倒过来遍历（从最后一条往前找），
        # 配合`next(...)`就实现了"找到最近的一条人类消息，找不到就是
        # None"的效果。条件里`isinstance(m, HumanMessage) or
        # getattr(m, "type", None) == "human"`是两种判断"这是不是人类
        # 消息"的方式一起用，兼容不同来源的消息对象可能不完全是标准
        # HumanMessage类型的情况。
        last_human = next(
            (m for m in reversed(msgs) if isinstance(m, HumanMessage) or getattr(m, "type", None) == "human"),
            None,
        )
        if last_human is not None:
            txt = _extract_text(getattr(last_human, "content", ""))
            # `any(k in txt for k in _COMPLEX_KEYWORDS)` 是"生成器表达式
            # + any()"的组合写法：只要_COMPLEX_KEYWORDS里有任意一个关键词
            # 出现在txt里，就返回True。
            if any(k in txt for k in _COMPLEX_KEYWORDS):
                # 用户这句话里出现了"debug"/"fix"/"why"等暗示排查/修复的
                # 关键词——大幅加分，倾向于用主力模型。
                score += 4
                signals["complex_keywords"] = 4
            elif len(txt) < 200 and any(k in txt for k in _SIMPLE_KEYWORDS):
                # 这句话本身很短（少于200个字符）且包含"list"/"show"/
                # "ok"等暗示简单指令的关键词——减分，倾向于用便宜模型。
                # 用`elif`（不是单独的if）意味着"只有在没命中复杂关键词
                # 的前提下，才检查是否命中简单关键词"——两者互斥，不会
                # 同时加分又减分。
                score -= 2
                signals["simple_keywords"] = -2

        # Python可以用逗号一次返回多个值，调用方接收时会自动打包成一个
        # "元组"（tuple）。
        return score, signals

    def _route(self, request: ModelRequest) -> tuple[BaseChatModel | None, int, dict[str, int]]:
        score, signals = self._score(request)
        if score <= self._threshold:
            # 分数不超过阈值——判定这一轮"够简单"，返回便宜模型作为
            # "要替换成的模型"。
            return self._cheap_model, score, signals
        # 分数超过阈值——返回None，表示"不替换，维持原本打算用的模型"。
        return None, score, signals

    # wrap_model_call 是AgentMiddleware规定好的"钩子方法"名字——LangChain
    # 在每次真正发起模型调用之前，会依次调用所有挂载的中间件的这个方法，
    # 并把"下一步该做什么"包装成`handler`这个可调用对象传进来。中间件
    # 只需要决定"要不要在调用handler之前，先对request做点什么改动"。
    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        override, score, signals = self._route(request)
        if override is not None:
            # `logger.info(...)` 打一条"信息级别"的日志——`%d`/`%s`是
            # logging模块自己的占位符写法（不是f-string），后面依次传入
            # 对应的值，logging库会在真正需要输出时才做字符串格式化，
            # 比提前用f-string拼好字符串更省性能（尤其是当这条日志因为
            # 级别设置根本不会被输出时）。
            logger.info("[ModelRouter:%s] score=%d → cheap model. signals=%s", self._parent_name, score, signals)
            # `request.override(model=override)` 基于原始request，生成
            # 一份"只是把model字段换成了便宜模型"的新request，再交给
            # handler继续往下走——这就是"按次替换模型"的具体实现方式。
            return handler(request.override(model=override))
        logger.debug("[ModelRouter:%s] score=%d → default model. signals=%s", self._parent_name, score, signals)
        # 判定不需要替换，原样把request交给handler，用原本打算用的模型
        # 继续调用。
        return handler(request)

    # `async def` 定义的是一个"异步函数"——调用它需要配合`await`使用。
    # 异步的意义在于：真正发起模型调用往往需要等待网络请求返回，
    # "异步"让程序在等待期间可以先去处理别的事情，而不是傻等在这里
    # 浪费资源。这个方法和上面的`wrap_model_call`做的是完全相同的事，
    # 只是给"异步风格调用agent"的场景使用（`Awaitable[ModelResponse]`
    # 表示handler本身也是一个需要await的异步调用）。
    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        override, score, signals = self._route(request)
        if override is not None:
            logger.info("[ModelRouter:%s] score=%d → cheap model. signals=%s", self._parent_name, score, signals)
            # `await handler(...)` ——调用这个异步的handler，并"等待"它
            # 真正执行完毕、拿到结果，而不是立刻往下走。
            return await handler(request.override(model=override))
        logger.debug("[ModelRouter:%s] score=%d → default model. signals=%s", self._parent_name, score, signals)
        return await handler(request)
