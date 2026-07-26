"""HITL超时业务规则：默认时长读取 + 从代码结构上禁止"超时默认放行"这类危险默认值。

改造自真实项目里验证过的设计：多条独立的HITL执行路径（不同存储/触发
机制）容易各自硬编码一份几乎相同的"超时后怎么办"业务规则，任一处调整
另一处不会感知。核心安全原则：**超时后自动决定绝不能是"批准"**——
`safe_timeout_decision()`从函数签名结构上就不接受"决定类型"这个参数，
杜绝调用方误传一个危险的默认值，而不是靠注释/约定去防范。
"""

# HITL 是 "Human-In-The-Loop"（人在回路中）的缩写——意思是流程执行到某个
# 关键节点时，不能让程序自己完全说了算，必须暂停下来，等一个真人做出
# 决定（比如"批准"还是"拒绝"某个操作）之后才能继续。常见于"AI可能做出
# 有风险的操作，需要人工兜底把关"的场景，比如自动转账、自动发邮件等。
# 这个文件专门处理"人一直不回应、等太久了该怎么办（超时）"这个子问题。
from __future__ import annotations

# logging——Python标准库自带的日志模块，用来在程序运行过程中输出
# "这里发生了什么"的记录（比如警告、报错），比直接用print更规范，
# 可以按级别（INFO/WARNING/ERROR等）过滤，也方便统一收集到日志系统里。
import logging

# os模块用来读取"环境变量"——见config.py风格文件里的详细解释：环境变量
# 是启动程序之前由部署环境设置好的一些全局配置项，程序运行时可以读取，
# 这样"超时应该设多久"这类可调参数就不用写死在代码里，可以在不同部署
# 环境里配置成不同的值。
import os

# `logging.getLogger(__name__)` 创建（或复用）一个和"当前这个模块"绑定的
# 日志记录器——`__name__`是Python自动帮每个文件填好的一个字符串，值就是
# 这个模块的完整路径名（比如"ainative_workflow.hitl_policy"），这样日志
# 输出时能看出"这条日志是从哪个模块打出来的"，方便排查问题时定位来源。
logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 86400
"""默认超时时长（秒）：24小时，足够容忍人工审批的正常延迟，又不至于让被
遗忘的审批无限期占用资源。"""
# ↑ 86400秒 = 24小时（60秒×60分×24小时）。这是一个模块级的"公共常量"——
#   写在函数外面、大写命名，是Python里"这是一个不应该被随便改动的固定值"
#   的传统写法惯例（Python本身不会强制禁止修改，全靠命名约定自觉遵守）。

# 下面两个变量名前面加了一个下划线`_`，这也是Python的命名惯例：表示
# "这是模块内部使用的实现细节，不建议外部代码直接引用"（不是语法强制，
# 只是约定；对比前面`DEFAULT_TIMEOUT_SECONDS`没有下划线，说明它是特意
# 设计成可以被外部导入使用的公开常量）。
_SAFE_TIMEOUT_DECISION_TYPE = "reject"
# ↑ 这是本文件最核心的安全设计：把"超时后应该做出的决定类型"直接写死成
#   字符串"reject"（拒绝），存成一个模块内部常量，而不是留一个函数参数
#   让调用方自己传。下面会看到`safe_timeout_decision()`函数完全不接收
#   "决定类型"这个参数——这样"不小心把超时默认行为配置成自动批准"这种
#   高风险错误，从函数签名的结构上就变得不可能发生，不需要依赖"大家都
#   记得看注释、都遵守约定"这种脆弱的人为纪律。
_DEFAULT_TIMEOUT_MESSAGE = "Approval timed out (no response within the timeout window); automatically rejected."


def read_timeout_seconds(env_name: str, *, default: int = DEFAULT_TIMEOUT_SECONDS) -> int:
    """读取指定环境变量的超时秒数；缺失/非法/非正数都回退到`default`（fail-safe）。"""
    # 函数参数列表里的这个单独的`*`，表示它后面的参数（这里是`default`）
    # 必须用"参数名=值"的方式调用，比如`read_timeout_seconds("X", default=10)`，
    # 不能写成`read_timeout_seconds("X", 10)`这种只按位置传参的方式。
    # 好处：调用者必须把参数名写出来，代码本身自带说明性，也避免以后
    # 给函数加新参数时不小心打乱了位置对应关系。

    # `os.getenv(env_name)` 去读取一个指定名字的环境变量；读不到时返回
    # `None`（不会报错崩溃）——这是刻意的容错设计，因为"这个环境变量
    # 到底有没有配置"本来就是完全合理、经常发生的情况，不该被当作错误。
    raw = os.getenv(env_name)
    if raw is None:
        # 环境变量压根没配置——直接用调用方传入的default（或者默认的
        # DEFAULT_TIMEOUT_SECONDS）兜底，这是最常见、最正常的一条路径。
        return default
    # `try`/`except` 是Python的"异常处理"语法：先尝试执行`try`块里的
    # 代码，如果执行过程中出错（比如字符串没法转成整数），程序不会
    # 直接崩溃退出，而是跳转去执行匹配的`except`块，做一些"补救"处理。
    try:
        # `int(raw)` 尝试把从环境变量读到的字符串转换成整数——比如
        # 环境变量本身是文本"3600"，这里转换成数字3600。
        value = int(raw)
    except (TypeError, ValueError):
        # 如果环境变量的值压根不是一个能转成整数的字符串（比如被手滑
        # 配置成了"abc"或者空字符串），转换会抛出ValueError（有些极端
        # 情况会是TypeError）——这里捕获住，不让程序崩溃，而是记一条
        # WARNING级别的日志说明"配置有问题，我们用了兜底值"，然后照常
        # 返回默认值，保证即使运维配错了这个参数，整个系统也不会因此
        # 直接挂掉（这就是注释里说的"fail-safe"，出问题时优先保证系统
        # 能继续运行，而不是硬崩溃）。
        logger.warning("HITL timeout config %s=%r is invalid, falling back to %ds", env_name, raw, default)
        return default
    if value <= 0:
        # 即使字符串成功转换成了整数，也要再检查一下这个数字本身是否
        # "合理"——0秒或负数秒的超时时长在业务上没有意义（比如"负1小时
        # 后自动拒绝"是荒谬的），所以同样记警告日志并回退到默认值。
        logger.warning("HITL timeout config %s=%d is non-positive, falling back to %ds", env_name, value, default)
        return default
    # 只有真正读到了一个"合法且为正数"的超时秒数，才真正采用它。
    return value


def safe_timeout_decision(message: str | None = None) -> dict:
    """构造一个"超时自动决定"。

    安全原则：类型恒为`reject`，调用方无法指定其他类型（本函数不接收
    type参数）——这是唯一被允许的超时默认决定构造入口。
    """
    # 注意看这个函数的参数列表：只有一个可选的`message`（给人看的提示
    # 文案），完全没有"decision_type"或类似的参数——这正是文件顶部
    # docstring强调的"从函数签名结构上就禁止误传危险默认值"的具体体现。
    # 不管调用方是谁、在哪里调用，这个函数产出的"决定"永远、只能是
    # "reject"（拒绝）这一种类型，物理上无法被改成"approve"（批准）。
    return {
        "type": _SAFE_TIMEOUT_DECISION_TYPE,
        # `message or _DEFAULT_TIMEOUT_MESSAGE` 是Python常见的"短路"写法：
        # 如果调用方传了`message`（且不是空字符串/None这类"假值"），就用
        # 调用方传入的文案；否则（调用方没传，`message`是None）就用模块
        # 里预先写好的默认提示文案兜底。
        "message": message or _DEFAULT_TIMEOUT_MESSAGE,
    }


def safe_timeout_decisions(count: int, message: str | None = None) -> list[dict]:
    """为`count`个待审批操作各生成一个安全的超时reject决定。"""
    # 这是一个"列表推导式"（list comprehension）——Python里用一行代码
    # 快速生成一个新列表的写法，等价于写一个for循环、每次往空列表里
    # append一个元素，但更简洁。这里的意思是：调用`count`次
    # `safe_timeout_decision(message)`，把每次的返回结果收集成一个列表。
    # `for _ in range(...)` 里的下划线`_`是Python的命名惯例：表示
    # "这个循环变量本身的值我们根本不关心，只是想循环这么多次"。
    # `max(0, count)` 是防御性写法：万一调用方不小心传入了负数
    # （比如`count=-1`），`range(-1)`本身也会得到一个空的循环（不会报错），
    # 但这里显式用`max(0, count)`把负数强制归零，让代码意图更清晰、
    # 也更保险，不依赖`range()`对负数的隐含行为。
    return [safe_timeout_decision(message) for _ in range(max(0, count))]
