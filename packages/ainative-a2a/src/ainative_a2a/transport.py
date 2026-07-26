"""`ainative_core.protocols.AgentTransport`的进程内默认实现。

进程内直接函数调用——不引入任何跨进程/网络基础设施。真实项目如果需要
跨服务委派任务（比如目标agent运行在另一个部署单元），实现同一个
`AgentTransport`协议接入HTTP/消息队列等，本模块的`Dispatcher`不关心
具体传输方式。
"""

# 让类型注解可以延迟解析，见__init__.py里的详细解释，本文件不再重复。
from __future__ import annotations

# Awaitable/Callable 用来给"一个函数/协程"本身写类型注解：
# - `Callable[[参数类型...], 返回类型]` 表示"一个函数，接收这些类型的
#   参数，返回这个类型的结果"。
# - `Awaitable[X]` 表示"一个可以用`await`等待、最终会产出X类型结果的
#   东西"——像`async def`定义出来的函数，调用它得到的就是一个
#   Awaitable对象，需要`await`一下才能拿到真正的返回值。
# 两者组合起来，`Callable[[A2ATask], Awaitable[dict]]`就表示"一个
# 接收A2ATask、返回值需要await才能拿到dict"的异步函数类型——也就是
# 下面`AgentHandler`这个类型别名的真实含义。
from collections.abc import Awaitable, Callable

# A2AResult/A2ATask 是在ainative-core里定义好的通用数据结构（分别表示
# "一次委派的结果"和"一次委派请求本身"），本模块只负责"怎么把任务真正
# 送到目标agent手里、再把它的返回值包装成标准结果"，不重新定义这些数据
# 长什么样。
from ainative_core.protocols import A2AResult, A2ATask

# 这一行不是定义类，而是定义一个"类型别名"——给右边这一长串
# `Callable[[A2ATask], Awaitable[dict]]`起一个短名字`AgentHandler`，
# 之后代码里写`AgentHandler`就等同于写这一整串类型，避免反复重复。
# `AgentHandler` 描述的是"每个agent真正干活的那个函数长什么样子"：
# 接收一个`A2ATask`（这次要处理的任务），返回一个字典（处理结果）。
AgentHandler = Callable[[A2ATask], Awaitable[dict]]
"""一个agent处理某个能力任务的实际逻辑：接收`A2ATask`，返回结果payload（dict）。
处理失败应该直接抛异常，由`InProcessTransport`捕获并转换成`A2AResult(status="error")`，
调用方不需要自己处理异常转换。"""


class InProcessTransport:
    """把已注册的agent handler函数，包装成`AgentTransport`协议要求的`send()`接口。"""

    def __init__(self) -> None:
        # `_handlers`是一个字典，key是agent名称（字符串），value是这个
        # agent真正的处理函数（类型是上面定义的`AgentHandler`）。
        # 前缀下划线`_`是Python的约定写法，表示"这是内部实现细节，不建议
        # 外部代码直接访问"（不是强制的语言级别限制，只是一种"君子协定"）。
        # 构造函数刚创建实例时，还没有任何agent注册进来，所以是空字典。
        self._handlers: dict[str, AgentHandler] = {}

    def register_handler(self, agent_name: str, handler: AgentHandler) -> None:
        """登记某个agent名称对应的实际处理函数。"""
        # 把"这个agent名字"和"它对应的处理函数"存进字典——就像往一份
        # "员工名单"里登记"张三负责接电话"，以后想找张三处理事情，
        # 直接按名字查这份名单就行。
        self._handlers[agent_name] = handler

    # `async def` 定义的是一个"异步方法"——调用它需要写成
    # `await transport.send(...)`。之所以是异步的，是因为这个方法内部
    # 要调用目标agent的handler，而真实场景里handler很可能要等待网络/
    # 数据库等耗时操作，异步写法能让程序在等待期间腾出手处理别的事情，
    # 而不是傻等在原地浪费资源。这里虽然是"进程内直接调用"，也统一采用
    # async是为了和`AgentTransport`协议的方法签名保持一致（这样以后
    # 换成真正跨网络的传输实现，调用方代码不需要任何改动）。
    async def send(self, agent_name: str, task: A2ATask) -> A2AResult:
        # `dict.get(key)`——去字典里查这个key对应的值，查不到就返回None
        # （不会像`dict[key]`那样直接报错崩溃），这是比直接用方括号取值
        # 更安全的写法，适合"这个key可能压根没登记过"的场景。
        handler = self._handlers.get(agent_name)
        if handler is None:
            # 目标agent压根没有注册过处理函数——这不是程序bug，而是
            # 一种可预期的业务失败（比如拼错了agent名字），所以不抛异常
            # 中断程序，而是构造一个"status='error'"的正常返回值，让
            # 调用方自己决定怎么处理这个失败（比如重试/换个agent/报警）。
            return A2AResult(
                task_id=task.task_id, status="error",
                error_message=f"no handler registered for agent '{agent_name}'",
            )
        # try/except 是Python里"尝试执行一段代码，如果中途出错就转而
        # 执行except里的代码"的语法——这里的意图是"优雅地把异常转换成
        # 结果对象"：调用方（这里是handler的编写者/目标agent自己的代码）
        # 完全不需要关心"万一处理失败该怎么告诉委派方"这件事，只管直接
        # `raise`一个异常，`InProcessTransport`会自动帮它捕获、转换成
        # 标准的`A2AResult(status="error", error_message=...)`格式，
        # 委派方永远只需要处理"A2AResult"这一种统一的返回值形态，不需要
        # 到处写`try/except`去接住handler可能抛出的各种五花八门的异常。
        try:
            # `await handler(task)`——真正调用登记好的处理函数，并且
            # "等待"它执行完、拿到最终结果，因为handler本身是异步函数。
            output = await handler(task)
        # `except Exception as exc` 捕获几乎所有可能发生的异常（`Exception`
        # 是Python里绝大多数内置异常的公共基类），`as exc`把捕获到的这个
        # 异常对象存进变量`exc`，方便下面把它的文字描述取出来使用。这一行
        # 末尾还有一条给代码检查工具ruff看的"抑制警告"标记——ruff通常
        # 建议"不要捕获过于宽泛的Exception，应该只捕获明确预期的异常
        # 类型"，但这里是刻意设计：这个方法的整体职责就是"不管handler
        # 内部抛出什么异常，统统转换成标准错误结果"，所以显式标记告诉
        # ruff"这是故意这么写的，不是疏忽"。
        except Exception as exc:  # noqa: BLE001
            # `str(exc)`——把异常对象转换成人类可读的文字描述，存进
            # 最终返回结果的`error_message`字段。
            return A2AResult(task_id=task.task_id, status="error", error_message=str(exc))
        # 如果上面`await handler(task)`没有抛出任何异常，说明处理成功，
        # 把handler返回的字典包装进`status="success"`的结果对象里。
        return A2AResult(task_id=task.task_id, status="success", output=output)
