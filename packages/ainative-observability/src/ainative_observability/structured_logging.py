"""结构化JSON日志 + 统一敏感信息过滤器。

改造自真实项目里验证过的设计（`logging_config.py`的JSON格式化 +
`log_redaction.py`的`SensitiveDataFilter`）：应用日志必须是可以按字段
查询/过滤的结构化数据，而不是纯文本拼接；敏感信息脱敏必须是一个独立、
统一挂载在日志处理链路（`Handler.addFilter`）上的过滤器，对所有日志
无差别生效，而不是指望每个调用点自己记得脱敏。

真实事故背景（对应checklist H类"结构化日志与安全性子类"）：
- 用f-string等字符串拼接方式直接写日志消息，会把本该是独立字段的数据
  （如请求头/请求体）压缩进消息文本，绕过结构化字段与字段级脱敏。
- "调试模式开关"（如某个环境变量）一旦打开，容易让原本受保护的敏感数据
  未经脱敏流向日志文件——脱敏过滤器必须挂在Handler层面、对所有日志级别
  和所有debug开关状态都无差别生效，而不是只在"正常路径"生效。
"""

# 让类型注解可以延迟解析，详见`tracing.py`里的详细解释，这里不再重复。
from __future__ import annotations

# json——Python标准库自带模块，`json.dumps(某个字典)`把一个Python字典
# 转换成JSON格式的字符串（JSON是一种通用的、几乎所有编程语言和日志系统
# 都能识别的结构化文本格式），这样日志才能被"按字段查询/过滤"，而不是
# 一整段没有结构的纯文本。
import json

# logging——Python标准库自带的日志模块。程序里到处都会用`logger.info(...)`
# `logger.warning(...)`这类调用来记录"发生了什么事情"，这个模块负责把
# 这些调用最终变成实际输出（打印到终端/写入文件等），中间可以插入
# "过滤器(Filter)"和"格式化器(Formatter)"来分别控制"要不要输出这条日志"
# 和"输出成什么样子"——这个文件里的`SensitiveDataFilter`/`JsonFormatter`
# 正是分别实现这两个角色。
import logging

# re——Python标准库自带的"正则表达式"模块。正则表达式是一种描述"文本
# 模式"的迷你语言，比如"先出现几个字母，再出现一个等号，再出现引号包起来
# 的内容"这种模式可以写成一个正则表达式，然后用它去一段文本里查找/替换
# 符合这个模式的部分。这个文件用它来"从日志文本里找出看起来像密钥/密码
# 的片段"。
import re

# time——见上面`json`的说明的姊妹模块：`time.time()`返回当前时间戳（从
# 1970年到现在的秒数），用来给每条日志打上时间戳字段。
import time

# Callable——给"一个函数本身"写类型注解的工具，见下面`install_structured_logging`
# 的`handler_factory`参数处的说明。
from collections.abc import Callable

# Any——表示"任意类型都可以"，用在下面`payload: dict[str, Any]`上，因为
# 一条日志record可以携带各种类型的额外数据。
from typing import Any

# `frozenset(...)`——Python内置的"不可变集合"类型：和普通的`set`一样，
# 内部元素不重复、支持"这个值在不在集合里"这种`in`判断（而且判断速度是
# 常数级的，不需要一个个遍历比较），但`frozenset`一旦创建就不能再增删
# 元素（普通`set`可以`.add()`/`.remove()`）。这里用它是因为"哪些字段名
# 算作敏感信息"这份名单，创建之后不应该在运行过程中被意外修改，而且
# 作为一个"公共默认值"暴露给外部时，用不可变类型能防止调用方拿到手后
# 不小心改动了这份共享的默认名单，意外影响到其他使用者。
DEFAULT_SENSITIVE_KEYS: frozenset[str] = frozenset({
    "password", "passwd", "pwd", "token", "api_key", "apikey", "secret",
    "authorization", "auth", "access_token", "refresh_token", "session_id",
    "cookie", "private_key", "credit_card", "ssn",
})
"""默认的字段名黑名单——日志record的`extra=`字段里，key名（不区分大小写）
命中这个集合的，值会被整体替换成`[REDACTED]`，不管值本身长什么样。"""

# `tuple[re.Pattern[str], ...]`——一个"元组(tuple)"类型注解：元组和列表
# 类似，都是"一串按顺序排列的元素"，区别是元组一旦创建就不能再增删/替换
# 里面的元素（列表可以）。`re.Pattern[str]`表示"元组里每一个元素都是一个
# 编译好的正则表达式对象"，末尾的`...`表示"这个元组可以有任意多个这样的
# 元素"（不是固定长度）。`re.compile(某个正则字符串)`预先把正则表达式
# "编译"成一个可以重复高效使用的对象，比每次匹配都重新解析原始字符串
# 更快，适合这种会被反复调用很多次的场景。
_DEFAULT_VALUE_PATTERNS: tuple[re.Pattern[str], ...] = (
    # 带引号的值：允许值内部出现空格（真实密码/密钥可能包含空格），只在
    # 遇到闭合引号时停止——不能用`[^\s"\']`这类排除空格的字符类，那样
    # 值里第一个空格之后的部分会逃过脱敏（真实误报过的漏检模式）。
    re.compile(r'(?i)(api[_\-]?key|apikey|token|secret|password|passwd|pwd)\s*[:=]\s*"([^"]{1,200})"'),
    re.compile(r"(?i)(api[_\-]?key|apikey|token|secret|password|passwd|pwd)\s*[:=]\s*'([^']{1,200})'"),
    # 不带引号的值：只能用空白/常见分隔符作为值的边界，最短长度降到1——
    # 短密钥/PIN（比如"pwd=abc12"这类5-6位真实场景）不能因为凑不满原来
    # {6,}的最短长度要求就被当作"太短所以不算敏感信息"而放过。
    re.compile(r'(?i)(api[_\-]?key|apikey|token|secret|password|passwd|pwd)\s*[:=]\s*([^\s,;"\']{1,200})'),
    re.compile(r'(?i)bearer\s+([A-Za-z0-9\-._~+/]{1,200})'),
)
"""日志消息文本本身（不只是extra字段）里可能意外携带的敏感信息模式——
即使调用方图省事用f-string把整段数据拼进了消息文本，这一层也能兜底。
故意把最短长度降到1、允许引号内出现空格——这是"宁可误伤几个字符短的
非敏感值，也不能放过真实存在的短密钥/带空格密码"这个防御性设计取舍。
"""
# ↑ 补充说明这四条正则表达式各自在找什么（正则表达式初学者可以对照着看）：
#   - `(?i)`：放在正则开头，表示"接下来的匹配不区分大小写"（比如
#     "API_KEY"和"api_key"都能匹配上）。
#   - `(api[_\-]?key|apikey|token|secret|password|passwd|pwd)`：括号
#     包起来的是"捕获组"——表示"记住这部分具体匹配到的文本，之后可以
#     单独取出来用"；里面用竖线`|`分隔的是"任选其一"，比如
#     `api[_\-]?key`表示"api"后面可选地跟一个下划线或短横线，再跟"key"
#     （能匹配"api_key"、"api-key"、"apikey"）。这一段是在找"像是密钥
#     名字的关键词"。
#   - `\s*[:=]\s*`：表示"零个或多个空白字符，然后是一个冒号或等号，
#     再零个或多个空白字符"——对应日志里"key: value"或"key=value"
#     这类常见写法。
#   - `"([^"]{1,200})"`：又一个捕获组，表示"被双引号包起来的、1到200个
#     字符长度的、不含双引号本身的内容"——这就是要脱敏的"值"部分。
#   - 第三条正则里的`([^\s,;"\']{1,200})`：表示"1到200个字符，且不是
#     空白、逗号、分号、引号"——用于处理没有加引号包裹的值，靠常见的
#     分隔符/空白来判断值到哪里结束。
#   - 第四条正则专门处理HTTP里常见的`Authorization: Bearer xxxxx`格式，
#     `[A-Za-z0-9\-._~+/]`表示"值只能由字母、数字和几个常见符号组成"。


class SensitiveDataFilter(logging.Filter):
    """统一挂载在Handler上的敏感信息过滤器——对所有日志record无差别生效。

    用法::

        handler = logging.StreamHandler()
        handler.addFilter(SensitiveDataFilter())
        logger.addHandler(handler)

    这样无论调用方是用`logger.info("...", extra={...})`还是直接
    `logger.debug(f"...{secret}...")`，敏感信息都会在真正输出前被拦截，
    而不依赖每个调用点自觉遵守脱敏规范。
    """

    # 这个类继承自`logging.Filter`（Python日志模块内置的"过滤器"基类）
    # ——继承它之后，只要实现一个`filter(self, record)`方法，就能挂载到
    # 任何`Handler`上，在每条日志真正被输出之前，都会先经过这个方法处理
    # 一遍。

    def __init__(
        self,
        *,
        sensitive_keys: frozenset[str] = DEFAULT_SENSITIVE_KEYS,
        value_patterns: tuple[re.Pattern[str], ...] = _DEFAULT_VALUE_PATTERNS,
    ) -> None:
        # 参数列表里单独的`*`——表示它后面的两个参数都必须用"参数名=值"
        # 的方式调用，不能只按位置传参，强制调用方写清楚意图。
        # `super().__init__(...)`——调用父类`logging.Filter`自己的构造
        # 函数，完成基类需要的初始化工作（这里没有额外传参数，用父类的
        # 默认初始化行为）。
        super().__init__()
        # `{k.lower() for k in sensitive_keys}`——这是一个"集合推导式"
        # （set comprehension，和列表推导式类似，只是外层用花括号、结果
        # 是一个集合而不是列表）：把调用方传入的每一个字段名都转换成小写，
        # 重新收集成一个新的集合。这样后面判断"这个字段名是不是敏感"时，
        # 只需要把待判断的字段名也转成小写再比较，就能做到"不区分大小写"
        # 的匹配（比如"Password"和"PASSWORD"和"password"都能命中）。
        self._sensitive_keys = {k.lower() for k in sensitive_keys}
        self._value_patterns = value_patterns

    def filter(self, record: logging.LogRecord) -> bool:
        # `logging.LogRecord`——Python日志模块内部用来表示"一条日志"的
        # 标准对象，包含了消息文本、日志级别、附加的extra字段等所有信息。
        # `filter`方法的返回值决定"这条日志到底要不要真的被输出"——
        # 这里的返回值永远是True，也就是说这个过滤器绝不会拦下任何一条
        # 日志本身，它的职责只是"篡改"日志内容（把敏感部分替换掉），而
        # 不是决定日志的去留。
        # 永远返回True——这是"脱敏"过滤器，不是"丢弃"过滤器，绝不能让
        # 敏感信息处理本身的逻辑意外拦截掉整条日志的产生。
        try:
            # `record.msg`——这条日志最原始的消息内容（可能是字符串，
            # 也可能像测试里那样是一个整数之类的非字符串对象，所以先用
            # `str(...)`统一转换成字符串再处理）。把处理后脱敏过的文本
            # 重新写回`record.msg`，之后不管这条日志最终被格式化成什么
            # 样子，用的都会是这份已经脱敏过的文本。
            record.msg = self._redact_text(str(record.msg))
            # `record.args`——Python日志模块里，`logger.info("hello %s", name)`
            # 这种写法允许消息文本里带`%s`占位符，`args`就是用来填充这些
            # 占位符的原始参数。这里把它清空成一个空元组`()`，是因为上一行
            # 已经把`record.msg`替换成了"最终展示用的完整字符串"（脱敏后
            # 的），如果不清空`args`，日志模块之后格式化输出时还会尝试
            # 用`record.args`去对`record.msg`做一次`%`格式化，那样反而会
            # 把原始（未脱敏）的参数又重新拼接回消息里，白白抵消了刚才的
            # 脱敏效果。
            record.args = ()  # msg已经是最终字符串，避免%%格式化再次拼回原始参数
            # `vars(record)`——Python内置函数，返回一个对象所有属性组成
            # 的字典（属性名作为key）。`list(vars(record).keys())`把这些
            # 属性名收集成一个列表——之所以要包一层`list(...)`，是因为
            # 接下来的循环里会调用`setattr`去修改`record`的属性，如果
            # 直接对着"实时反映当前属性状态"的视图做遍历，一边遍历一边
            # 改会有风险（可能引发运行时错误或者遍历行为不确定），所以
            # 先完整复制一份属性名列表，再安全地基于这份"快照"去遍历修改。
            for key in list(vars(record).keys()):
                if key.lower() in self._sensitive_keys:
                    # `setattr(对象, 属性名, 新值)`——Python内置函数，
                    # 等价于`对象.属性名 = 新值`，只是属性名是用字符串
                    # 变量传入的（因为这里的`key`是循环变量，不是写死的
                    # 具体属性名，没法直接写`record.key = ...`那种字面量
                    # 形式）。命中敏感字段名的，整个值直接替换成固定的
                    # "[REDACTED]"（意思是"已隐去"），不管原始值具体是
                    # 什么内容，一律整体替换。
                    setattr(record, key, "[REDACTED]")
        except Exception:
            # 这里故意捕获所有异常且什么都不做（`pass`表示"什么也不
            # 执行"）——因为这个方法唯一不能容忍的后果，就是"脱敏逻辑
            # 本身出了bug，导致整条日志因此丢失或者让调用方的程序崩溃"。
            # 万一处理过程中出现意料之外的问题（比如某个属性的值类型很
            # 奇怪），宁可这条日志脱敏不完整，也要保证`filter`方法能
            # 正常返回、日志能继续走完输出流程。
            pass
        return True

    def _redact_text(self, text: str) -> str:
        # 依次用每一条预编译好的正则表达式，去处理这段文本——注意这里是
        # "累积处理"：每一轮`pattern.sub(...)`的结果都会覆盖赋值回`text`，
        # 作为下一条正则表达式处理的输入，所以一段文本可能会被多条规则
        # 依次检查、每条规则各自负责脱敏自己关心的那种格式。
        for pattern in self._value_patterns:
            # `pattern.sub(替换函数, text)`——正则表达式对象的"替换"方法：
            # 在`text`里查找所有匹配这条正则的片段，每找到一处，就调用
            # 一次`self._replace_value_group`（传入这次匹配的详细信息），
            # 用它的返回值替换掉原文里这一段匹配到的文本。
            text = pattern.sub(self._replace_value_group, text)
        return text

    @staticmethod
    def _replace_value_group(match: re.Match[str]) -> str:
        """只替换捕获组里的"值"部分，保留匹配到的其余前缀（如key名/`bearer `），
        用span偏移量定位value在整个match里的精确位置，而不是靠字符串内容
        搜索——避免value本身的内容恰好在match别处重复出现时定位错误。"""
        # `@staticmethod`——表示这个方法不需要访问`self`（不需要用到这个
        # 类实例自己的任何状态/数据），可以直接通过类名调用，也可以像
        # 这里一样，作为普通函数传给`pattern.sub(...)`使用。写成
        # staticmethod是因为它的逻辑完全只依赖传入的`match`参数，和
        # "是哪一个`SensitiveDataFilter`实例在调用"完全无关。
        # `re.Match[str]`——正则表达式一次成功匹配的结果对象，里面包含
        # 了"匹配到了原文的哪一段"、"各个捕获组分别对应原文的哪一段"等
        # 详细信息。
        # `match.group(0)`——第0个"组"永远表示"整条正则表达式匹配到的
        # 完整文本"（不是某个具体的捕获组），比如对"api_key: \"sk-xxx\""
        # 这段文本，`group(0)`就是这一整段，包含"api_key"、冒号、引号
        # 和值本身。
        full = match.group(0)
        # `match.lastindex`——这次匹配里，编号最大的、真正参与了匹配的
        # 捕获组序号（有些正则的捕获组是"可选的"，可能没有真正匹配上，
        # `lastindex`只统计确实匹配上的那些）。这里的意思是：如果这次
        # 匹配确实用到了第2个捕获组（对应带引号的两条正则，第1组是key
        # 名字、第2组才是引号内的真正值），就取第2组作为"值"；否则
        # （对应不带引号的第三条正则和bearer那条正则，只有1个捕获组）
        # 取第1组作为"值"。
        value_group_index = 2 if match.lastindex and match.lastindex >= 2 else 1
        # `match.span(组编号)`——返回这个捕获组在原始文本里的起止位置
        # （一对数字：开始下标、结束下标）。这是"用位置定位"，而不是
        # "用文本内容去反查"——docstring里解释了为什么要这样做：如果值
        # 本身的内容恰好在整段匹配文本里的别的地方也出现了一次（虽然
        # 概率不高，但并非不可能），单纯靠字符串内容搜索/替换可能替换错
        # 位置，用精确的数字下标就不会有这个问题。
        start, end = match.span(value_group_index)
        # 这次整体匹配（`group(0)`）在原始文本里的起始位置——下面要把
        # 上面拿到的"值"的绝对位置，换算成"相对于这段匹配文本自身的
        # 相对位置"，因为`full`只是原文的一小段截取，不能直接拿绝对
        # 下标去切它。
        match_start = match.start(0)
        # `full[: start - match_start]`——取`full`里，从开头到"值"开始
        # 位置之前的这一段（也就是key名字、冒号/等号、可能的引号这些
        # "前缀"部分，原样保留）；
        # 中间拼接固定的替换文本"[REDACTED]"；
        # `full[end - match_start :]`——取`full`里，"值"结束位置之后到
        # 末尾的这一段（比如闭合引号这类"后缀"部分，同样原样保留）。
        # 三段拼起来，效果就是"只把中间真正的敏感值换成[REDACTED]，
        # 其余的key名字/标点符号原样保留"。
        return full[: start - match_start] + "[REDACTED]" + full[end - match_start :]


class JsonFormatter(logging.Formatter):
    """把日志record格式化成单行JSON——可以按字段查询/过滤的结构化数据。

    默认字段：`timestamp`（ISO-ish epoch秒，不受系统时间被人为调整影响，
    用`time.time()`而非`datetime.now()`格式化字符串）、`level`、`logger`、
    `message`，外加所有非标准`LogRecord`属性的`extra=`字段。
    """

    # 继承自`logging.Formatter`（Python日志模块内置的"格式化器"基类）
    # ——负责"把一条`LogRecord`最终变成什么样的文本"，和上面的`Filter`
    # 分工不同：`Filter`决定"要不要输出/要不要篡改内容"，`Formatter`
    # 决定"输出成什么格式的字符串"。

    # `logging.LogRecord("", 0, "", 0, "", (), None)`——手动构造一个"空白"
    # 的`LogRecord`实例，只是为了拿到"一个全新的、什么额外信息都没加过
    # 的日志record，本身自带哪些标准属性"这份名单（比如`levelname`/
    # `pathname`/`lineno`这些Python日志模块自己内部使用的标准字段）。
    # `vars(...)`把这个空白record的所有属性转换成字典，`.keys()`取出
    # 所有属性名，`frozenset(...)`把这些名字收集成一个不可变集合。这份
    # 集合的作用：区分"哪些是LogRecord自带的标准属性"和"哪些是调用方
    # 通过`extra={...}`额外塞进来的自定义字段"——只有后者才需要被单独
    # 加进JSON输出里（标准属性里该展示的，前面已经手动挑出来放进
    # `timestamp`/`level`/`logger`/`message`了）。这一行写在类体里、
    # 不在任何方法内部，所以只在类刚被定义时执行一次，不会每次格式化
    # 一条日志都重新构造一遍。
    _STANDARD_ATTRS = frozenset(vars(logging.LogRecord("", 0, "", 0, "", (), None)).keys())

    def format(self, record: logging.LogRecord) -> str:
        # 构造最终要输出的JSON对象——先填好四个"永远都有"的标准字段。
        payload: dict[str, Any] = {
            # 用`time.time()`（纯粹的数字时间戳）而不是格式化过的日期
            # 字符串——docstring里解释了原因：数字时间戳不依赖"当前系统
            # 时区/日期格式设置"，不会因为服务器时钟被人为调整或者时区
            # 配置不同，导致日志时间戳出现难以对齐、难以排查的偏差。
            "timestamp": time.time(),
            # `record.levelname`——日志级别的文字名称，比如"INFO"、
            # "WARNING"、"ERROR"。
            "level": record.levelname,
            # `record.name`——发出这条日志的logger的名字（通常对应
            # 模块路径，比如"ainative_observability.tracing"）。
            "logger": record.name,
            # `record.getMessage()`——Python日志模块提供的方法，会自动
            # 把`record.msg`和`record.args`按`%`格式化规则拼接成最终
            # 的消息文本（如果`args`是空的，就直接返回`msg`本身）。
            "message": record.getMessage(),
        }
        # `vars(record).items()`——把这条record所有的属性，转换成
        # "属性名, 属性值"这样的键值对，逐个遍历。
        for key, value in vars(record).items():
            # 只有"不属于标准属性集合"（说明是调用方通过`extra={...}`
            # 额外传进来的自定义字段）、并且还没有被加进`payload`（避免
            # 万一某个自定义字段名恰好和上面手动填的"timestamp"这些
            # key重名，导致意外覆盖）的属性，才被加进最终输出的JSON里。
            if key not in self._STANDARD_ATTRS and key not in payload:
                payload[key] = value
        # `record.exc_info`——如果这条日志是通过`logger.exception(...)`
        # 或者在处理异常时记录的，这里会包含"当前正在处理的异常"的详细
        # 信息（异常类型、值、调用栈），否则是`None`。
        if record.exc_info:
            # `self.formatException(...)`——`logging.Formatter`基类自带
            # 的方法，把`exc_info`那份原始异常信息，转换成人类可读的、
            # 类似平时在终端看到的完整报错堆栈文本。
            payload["exc_info"] = self.formatException(record.exc_info)
        try:
            # `json.dumps(...)`——把上面拼好的`payload`字典，转换成一行
            # JSON格式的字符串。
            # `default=str`——遇到`json`模块本身不认识、不知道该怎么
            # 转换成JSON的数据类型时（比如一个自定义类的实例），不要
            # 直接报错，而是调用`str(...)`把它转换成字符串再放进JSON里
            # ——这样即使调用方往`extra`里塞了一个奇怪的对象，也大概率
            # 能兜底生成合法的JSON，而不是让整条日志因此失败。
            # `ensure_ascii=False`——默认情况下`json.dumps`会把非ASCII
            # 字符（比如中文）转义成`\uXXXX`这种形式，设成False后中文
            # 等字符会保持原样输出，日志文件看起来更直观、不用额外解码。
            return json.dumps(payload, default=str, ensure_ascii=False)
        except Exception as exc:
            # `default=str` only helps when json.dumps itself doesn't know a
            # type — it does NOT protect against a value's own __str__/__repr__
            # raising, which would otherwise make json.dumps raise and the
            # entire log line get silently dropped by logging's default
            # error handling (or, worse, escape into the caller's real
            # business logic if a non-standard Handler doesn't catch it).
            # A structured logging module's core promise is "never silently
            # lose a log line" — fall back to a minimal payload that is
            # guaranteed to serialize, rather than propagating the failure.
            # ↑ 上面这段英文docstring是原有内容，保留不动。用中文补充
            # 解释一下：`default=str`只能兜底"json不认识的类型"，但如果
            # 某个字段的值本身在被转换成字符串时（调用它的`__str__`方法）
            # 就直接抛出了异常（就像本文件对应的测试用例里构造的那个
            # 故意在`__str__`里`raise`的类），那么连`default=str`这个
            # 兜底机制自己都会失败，导致上面`json.dumps(...)`那一行本身
            # 抛出异常——如果这里不用`try/except`接住，整条日志就会因为
            # 这一个"调皮"的字段而彻底丢失，这就违背了"结构化日志绝不能
            # 无声无息丢失一整条记录"这个核心承诺。所以这里退而求其次，
            # 构造一份不包含任何"来路不明"字段的、必定能被安全序列化的
            # 最小payload（只保留最基础的时间戳/级别/logger名/消息本身，
            # 外加一条说明"哪里序列化失败了"的错误提示），保证这条日志
            # 记录本身、以及整个程序，都不会因此崩溃或者凭空消失。
            return json.dumps({
                "timestamp": time.time(), "level": record.levelname, "logger": record.name,
                "message": record.getMessage(), "logging_error": f"failed to serialize log record: {exc}",
            }, ensure_ascii=False)


def install_structured_logging(
    logger: logging.Logger,
    *,
    sensitive_keys: frozenset[str] = DEFAULT_SENSITIVE_KEYS,
    handler_factory: Callable[[], logging.Handler] = logging.StreamHandler,
) -> logging.Handler:
    """一步到位给`logger`挂上JSON格式化 + 敏感信息过滤器的Handler。

    `handler_factory`默认是`StreamHandler`（输出到stderr）；真实项目可以
    传入自己的工厂函数（比如写文件/发到日志收集系统的Handler），本函数
    只负责"这个Handler必须同时具备JSON格式化和脱敏过滤器"这个组合约束。
    """
    # 参数列表里单独的`*`——表示`sensitive_keys`/`handler_factory`都必须
    # 用"参数名=值"的方式调用。
    # `handler_factory: Callable[[], logging.Handler]`——类型注解表示
    # "这个参数必须是一个函数，不接收任何参数，返回一个logging.Handler
    # 实例"；默认值直接就是`logging.StreamHandler`这个类本身（在Python
    # 里，类名本身就是一个"可以被调用、调用后创建出一个实例"的东西，
    # 所以类可以当作"零参数构造函数"来满足这个类型要求）。这样设计的
    # 好处：调用方不需要自己先创建好一个Handler实例再传进来，而是传入
    # "怎么创建"这件事本身（一个函数），本函数内部统一负责真正创建，
    # 也方便真实项目替换成"创建一个写文件的Handler"之类的自定义逻辑。
    handler = handler_factory()
    # 给这个新创建的handler，安装本文件定义的JSON格式化器——之后经过
    # 这个handler的每一条日志，最终输出时都会是JSON格式的一行文本。
    handler.setFormatter(JsonFormatter())
    # 再给它加上敏感信息过滤器——`addFilter`可以给一个handler挂载多个
    # 过滤器，这里只加了这一个，用调用方传入（或默认）的敏感字段名单。
    handler.addFilter(SensitiveDataFilter(sensitive_keys=sensitive_keys))
    # 把这个配置好的handler，挂到调用方传入的`logger`上——之后这个
    # logger输出的每一条日志，都会经过这个handler（JSON格式化+脱敏）。
    logger.addHandler(handler)
    # 把这个handler本身也返回给调用方——方便调用方后续需要的话，可以
    # 拿着这个handler做进一步操作（比如日后想移除它、或者调整它的
    # 日志级别）。
    return handler
