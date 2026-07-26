"""轻量级追踪span记录——不强依赖OpenTelemetry，真实项目可按需接入真实OTel。

改造自真实项目里验证过的设计与真实踩过的坑（`otel.py`的`setup_otel`/
`_make_observable_exporter`）：

1. **导出失败必须自我监控**：追踪数据导出到后端（Tempo/Jaeger等）这个
   动作本身可能失败，如果失败被静默吞掉，会出现"看起来在追踪、实际上
   数据已经悄悄丢失"且长期不被发现的情况——`SpanExporter`协议的实现
   必须能在导出失败时被观察到（见`_ObservableSpanRecorder`的失败计数）。
2. **关联ID要真正在生产路径生效**：真实项目曾出现过`request_id`（HTTP层）/
   `trace_id`（追踪层）/`thread_id`（业务框架层）三套ID体系并行、却没有
   任何代码真正把它们串联起来的情况（`get_log_context()`函数存在但从未
   被调用）。本模块的`Span`把`correlation_id`作为构造时的必填概念，
   而不是事后才补的可选字段。
3. **后台/异步组件的span要挂在正确的父链路下**：`Span.child()`要求显式
   传入父span，不提供"隐式从某个全局变量猜测当前span是谁"的魔法，避免
   后台进程默认新开一条独立无关联链路的问题重演。

本模块不是OpenTelemetry的替代品——是一个足够轻量、demo/测试环境不需要
安装真实追踪后端就能验证"链路正确关联+导出失败可观测"这两个核心设计
原则的最小实现。真实项目如果已经在用OTel，只需要实现`SpanExporter`协议
去调用真实的OTel SDK即可复用本模块的关联/自监控逻辑。
"""

# "span"（读作"跨度/片段"）是分布式追踪里的核心概念：代表"一段有开始
# 时间和结束时间的操作"，比如"处理一次HTTP请求"或"调用一次LLM"就是一个
# span。多个span可以有父子关系（比如"处理请求"这个大span下面，套着
# "调用LLM"这个小span），串起来就能看清一次请求内部到底经历了哪些步骤、
# 每一步耗时多少。这个文件要做的事情，就是"记录span、在span结束时把它
# 导出去某个地方保存"。

# 让类型注解可以延迟解析（写在函数签名里的类型字符串，不需要在模块加载
# 时就能被解析成真正的类），是Python的标准写法，本包其余文件都会用到。
from __future__ import annotations

# time——Python标准库自带模块，`time.time()`返回"当前时间"，具体是一个
# 浮点数，表示从1970年1月1日0点到现在过了多少秒（这是计算机里常见的
# "时间戳"表示方式）。用它来记录一个span"什么时候开始、什么时候结束"，
# 两者相减就能算出这段操作耗时多久。
import time

# uuid——Python标准库自带模块，用来生成"几乎不可能重复"的唯一编号（UUID
# 全称Universally Unique Identifier，通用唯一识别码）。这里用它给每个
# span生成一个独一无二的`span_id`，这样即使系统里同时有成千上万个span
# 在跑，也不会有两个span的编号撞车。
import uuid

# Callable——给"一个函数本身"写类型注解的工具：比如下面`on_export_failure`
# 参数的类型是`Callable[[Exception], None]`，意思是"这个参数必须是一个
# 函数，接收一个Exception（异常对象）作为参数，不需要返回任何东西"。
from collections.abc import Callable

# contextmanager——一个装饰器，可以把一个"普通的生成器函数"（内部用yield
# 分成两段）改造成能配合`with`语句使用的"上下文管理器"。下面`Tracer.span`
# 方法用到它的地方会详细展开解释`with`语句和`yield`在这里具体怎么配合。
from contextlib import contextmanager

# dataclass/field——`@dataclass`是一个装饰器，自动帮你生成一个类的
# `__init__`（构造函数）、`__repr__`（打印时显示的样子）等样板代码，只
# 需要声明"这个类有哪些字段"；`field(default_factory=...)`专门用在"某个
# 字段的默认值是可变对象（比如字典/列表），需要每个实例各自独立创建一份，
# 不能所有实例共享同一份"这种场景（如果直接写`= {}`当默认值，Python会
# 报错，因为所有实例会意外共享同一个字典对象）。
from dataclasses import dataclass, field

# Any——表示"任意类型都可以"，用在下面`attributes: dict[str, Any]`上，
# 因为一个span身上可以附加各种各样类型的额外信息（字符串/数字/布尔值都
# 行），这里不做限定。
# Protocol/runtime_checkable——`Protocol`是Python的"结构化类型"工具：
# 定义一个"只要某个类具备这些方法（不要求显式继承它），就算实现了这个
# 协议"的接口约定，类似其他语言里的"接口(interface)"，但不需要调用方
# 显式声明"我实现了这个接口"，只要方法签名对得上就行。`runtime_checkable`
# 让这个协议还可以用在`isinstance(obj, SomeProtocol)`这种运行时检查上
# （默认的Protocol只能用于静态类型检查，加了这个装饰器才能在运行时判断）。
from typing import Any, Protocol, runtime_checkable


# @dataclass(frozen=True)——见上面`field`的说明；`frozen=True`表示这个类
# 的实例一旦创建出来，字段就不能再被修改（比如`record.status = "x"`会
# 直接报错）。一个"已经结束的span"记录的应该是历史事实，不应该在事后被
# 悄悄改动，所以冻结起来。
@dataclass(frozen=True)
class SpanRecord:
    """一个已完成的span——导出到后端的最小schema。"""

    # 下面这些是这个类的"字段"——`@dataclass`会自动把它们变成构造函数的
    # 参数，按声明顺序依次是：span自己的编号、父span的编号（可能没有父
    # span，所以是"str或者None"）、关联ID、名字、开始时间、结束时间……

    span_id: str
    """这个span自己的唯一编号，构造时由`uuid.uuid4()`随机生成。"""

    parent_span_id: str | None
    """父span的编号；`None`表示这是一条链路里最顶层、没有父节点的span。"""

    correlation_id: str
    """串联同一次请求/操作跨层级ID体系的关联字段（对应HTTP层request_id、
    业务框架层thread_id等）——真实项目应该把这个字段设成request_id本身
    或者能反查到request_id的值，而不是另起一套无关的ID。"""

    name: str
    """这个span代表的操作名字，比如"handle_request"、"call_llm"。"""

    start_time: float
    """span开始时的时间戳（`time.time()`的返回值，单位是秒）。"""

    end_time: float
    """span结束时的时间戳。"""

    # `field(default_factory=dict)`——见文件顶部import处的说明：每个
    # `SpanRecord`实例都会得到独立的一个新字典作为`attributes`的默认值，
    # 而不是所有实例共享同一个字典（那样一个span往里加数据会污染到
    # 另一个span）。
    attributes: dict[str, Any] = field(default_factory=dict)
    """附加在这个span上的额外信息，比如`model="claude-sonnet"`、
    `tokens=42`这类调用方关心的上下文数据。"""

    status: str = "ok"
    """这个span代表的操作是否成功——"ok"表示正常结束，"error"表示span
    执行过程中抛出了异常。"""

    error_message: str | None = None
    """如果`status`是"error"，这里存具体的错误信息文本；正常结束则为None。"""

    # @property——把一个方法"伪装"成一个普通属性来访问：调用方写
    # `record.duration_ms`（不用加括号），而不是`record.duration_ms()`。
    # 这里用它是因为"耗时多少毫秒"本质上是从`start_time`/`end_time`这
    # 两个已有字段直接算出来的派生值，没必要让调用方自己去写减法乘法。
    @property
    def duration_ms(self) -> float:
        # `(结束时间 - 开始时间)`算出的是"秒"，乘以1000转换成更常用的
        # "毫秒"单位（1秒=1000毫秒），因为大多数操作耗时都在毫秒量级，
        # 用秒表示会有很多不直观的小数点。
        return (self.end_time - self.start_time) * 1000


# @runtime_checkable——见文件顶部import处的说明：让这个Protocol可以用于
# `isinstance()`运行时检查，不只是静态类型检查。
@runtime_checkable
class SpanExporter(Protocol):
    """"已完成的span该被导出到哪里"这件事的抽象接口。

    真实实现应该调用真实的追踪后端SDK（OTel/Jaeger/自建HTTP上报等）；
    本包默认提供`InMemorySpanExporter`（见`memory_backends.py`）。
    """

    # `Protocol`类里的方法体写成`...`（省略号）——表示"这里只是声明这个
    # 方法必须存在、长什么签名，不提供具体实现"。任何类只要有一个同名同
    # 签名的`export`方法，就自动被认为"实现了这个协议"，不需要显式写
    # `class Foo(SpanExporter):`去继承它。
    def export(self, record: SpanRecord) -> None: ...


class _ObservableSpanRecorder:
    """包装一个`SpanExporter`，导出失败时递增失败计数并调用`on_export_failure`
    回调——这就是ch39"追踪数据导出失败要能被自我监控"这条要求的具体实现，
    而不是让导出异常被静默吞掉。"""

    # 类名前面加一个下划线`_`，是Python的命名惯例：表示"这是本模块内部
    # 使用的实现细节，不建议外部代码直接引用"（不是语法强制，只是约定）。
    # `Tracer`类内部会用到它，但它不出现在`__init__.py`的`__all__`列表里。

    def __init__(
        self, exporter: SpanExporter, *, on_export_failure: Callable[[Exception], None] | None = None
    ) -> None:
        # 参数列表里单独的这个`*`，表示它后面的参数（这里是
        # `on_export_failure`）必须用"参数名=值"的方式调用，不能只按
        # 位置传参——这样调用方必须显式写出参数名，代码自带说明性，也
        # 避免以后给函数加新参数时打乱位置对应关系。
        self._exporter = exporter
        # 保存"导出失败时要不要通知调用方"的回调函数；如果调用方没传，
        # 就是`None`，后面`export`方法里会先判断它是否为`None`再调用。
        self._on_export_failure = on_export_failure
        # 记录"到目前为止累计失败了多少次"——这是本类存在的核心目的：
        # 让"导出失败"这件事变得可统计、可观察，而不是发生了也没人知道。
        self.export_failure_count = 0

    def export(self, record: SpanRecord) -> None:
        # `try`/`except`是Python的"异常处理"语法：先尝试执行`try`块里的
        # 代码，如果执行过程中出错，程序不会直接崩溃退出，而是跳转去
        # 执行匹配的`except`块，做补救处理。
        try:
            # 真正把span交给底层的exporter去处理（比如写入内存列表、
            # 发送到远程追踪后端等，具体行为由传入的`exporter`决定）。
            self._exporter.export(record)
        except Exception as exc:  # noqa: BLE001
            # 这里故意捕获几乎所有异常类型（比ruff默认推荐的"只捕获
            # 明确预期的异常类型"更宽泛）：因为导出后端可能因为各种各样
            # 意料之外的原因失败（网络断开、后端服务挂了、序列化失败等），
            # 而这个方法的职责就是"不管具体因为什么原因失败，都不能让
            # 追踪系统本身的问题影响到被追踪的业务代码正常运行"。这一行
            # 末尾还有一条给代码检查工具ruff看的抑制警告标记，显式告诉它
            # "这处的宽泛捕获是刻意设计，不是疏忽遗漏"，避免每次代码检查
            # 都重复提示同一个已经想清楚的选择。
            self.export_failure_count += 1
            if self._on_export_failure is not None:
                # 调用方注册了失败回调——把这次的异常对象传给它，让调用方
                # 有机会做进一步处理（比如打一条告警日志、上报监控系统）。
                self._on_export_failure(exc)


class Tracer:
    """构造span并在完成时导出——所有span都要求显式的`correlation_id`，
    子span要求显式传入父span，不提供隐式全局current-span魔法。"""

    def __init__(
        self, exporter: SpanExporter, *, on_export_failure: Callable[[Exception], None] | None = None
    ) -> None:
        # 内部持有一个`_ObservableSpanRecorder`（而不是直接持有调用方
        # 传入的`exporter`）——这样每一次导出都自动带上失败计数/回调这层
        # 自我监控能力，调用方无需额外做任何事情就能获得这个保障。
        self._recorder = _ObservableSpanRecorder(exporter, on_export_failure=on_export_failure)

    @property
    def export_failure_count(self) -> int:
        # 把内部`_recorder`的失败计数，转发暴露成`Tracer`自己的一个只读
        # 属性，调用方不需要知道"实际计数其实存在一个内部的_recorder对象
        # 里"这个实现细节。
        return self._recorder.export_failure_count

    # @contextmanager——把下面这个函数从"一个普通的生成器函数"改造成
    # 可以配合`with`语句使用的"上下文管理器"。生成器函数的特点是内部用
    # `yield`把整个函数分成两段：`yield`之前的代码在`with`语句刚进入时
    # 执行一次，`yield`那一行把某个值交给`as`后面的变量，`with`代码块
    # 执行完毕（不管是正常执行完，还是中途抛了异常）之后，会回到这个
    # 生成器函数里`yield`那一行的下一行继续往下执行——这就是为什么下面
    # 能在`finally`块里做"span结束后的收尾工作"（记录结束时间、导出）。
    @contextmanager
    def span(self, name: str, *, correlation_id: str, parent: SpanRecord | None = None, **attributes: Any):
        """用法::

            with tracer.span("handle_request", correlation_id=request_id) as s:
                ...
                with tracer.span("call_llm", correlation_id=request_id, parent=s):
                    ...
        """
        # 参数列表末尾的`**attributes`——这是Python的"关键字可变参数"
        # 写法：调用方可以传任意数量、任意名字的"参数名=值"，它们全部会
        # 被自动收集进一个名叫`attributes`的字典里。比如调用
        # `tracer.span("x", correlation_id="r1", model="claude", tokens=42)`，
        # 这里的`attributes`就会是`{"model": "claude", "tokens": 42}`——
        # 这样调用方可以给span附加任意自定义的额外信息，不需要这个方法
        # 提前把每一种可能的字段名都写进参数列表。

        # `uuid.uuid4()`生成一个随机的、几乎不可能重复的UUID对象，
        # `str(...)`把它转换成字符串形式，作为这个span自己的唯一编号。
        span_id = str(uuid.uuid4())
        # 记录span"刚刚开始"这一刻的时间戳。
        start_time = time.time()
        # 先假设这个span会正常成功结束（"ok"），如果下面的`with`代码块
        # 执行过程中抛出了异常，会在`except`分支里改成"error"。
        status = "ok"
        error_message: str | None = None
        record: SpanRecord | None = None
        try:
            # 构造一个"占位"的`SpanRecord`——此时`with`代码块（调用方
            # 写在`with tracer.span(...) as s:`缩进块里的代码）还没有
            # 真正执行，所以`end_time`暂时先填成和`start_time`一样的值
            # （反正这份占位记录本身不会被导出，只是给调用方一个可以在
            # `with`块内部读取`span_id`之类信息的对象，比如把它当作子
            # span的`parent`传下去）。
            placeholder = SpanRecord(
                span_id=span_id, parent_span_id=parent.span_id if parent else None,
                correlation_id=correlation_id, name=name, start_time=start_time, end_time=start_time,
                attributes=dict(attributes),
            )
            # `yield`——把`placeholder`交给`with ... as s:`里的`s`变量，
            # 然后暂停在这一行，转去执行`with`代码块里调用方写的代码；
            # 等那段代码执行完毕（或者中途抛出异常），才会回到这里，
            # 继续往下执行`except`/`finally`部分。
            yield placeholder
        except Exception as exc:
            # `with`代码块内部的代码抛出了异常——记录下"这个span失败了"
            # 以及具体的错误信息，然后`raise`（不带参数的`raise`表示
            # "原样重新抛出刚才捕获到的这个异常"，不生吞掉它、不改变
            # 异常类型），让调用方依然能感知到原本发生的异常，本方法
            # 只是"顺便"记录一下这次失败对应哪个span，不代替调用方
            # 处理这个异常。
            status = "error"
            error_message = str(exc)
            raise
        finally:
            # `finally`块里的代码，不管上面的`try`是正常执行完（span
            # 成功）还是中途抛出异常被`except`捕获（span失败），都一定
            # 会被执行——这保证"span一定会被记录并导出"这件事，不会因为
            # 业务代码出错就被跳过（这正是文件docstring里提到的、以及
            # 下面测试文件里专门验证的"span还是要被导出"这条承诺）。
            end_time = time.time()
            # 构造这次span真正的、完整的记录——这次`end_time`是真实的
            # 结束时间，`status`/`error_message`也反映了刚才`try`块
            # 执行的真实结果。
            record = SpanRecord(
                span_id=span_id, parent_span_id=parent.span_id if parent else None,
                correlation_id=correlation_id, name=name, start_time=start_time, end_time=end_time,
                attributes=dict(attributes), status=status, error_message=error_message,
            )
            # 把这份完整的span记录交给内部的"自我监控"recorder去导出——
            # 见`_ObservableSpanRecorder.export`：即使导出本身失败了，
            # 也不会让这个异常从这里冒出去影响调用方的代码。
            self._recorder.export(record)
