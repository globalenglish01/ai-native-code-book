"""`ainative_core.protocols.AgentTransport`的进程内默认实现。

进程内直接函数调用——不引入任何跨进程/网络基础设施。真实项目如果需要
跨服务委派任务（比如目标agent运行在另一个部署单元），实现同一个
`AgentTransport`协议接入HTTP/消息队列等，本模块的`Dispatcher`不关心
具体传输方式。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from ainative_core.protocols import A2AResult, A2ATask

AgentHandler = Callable[[A2ATask], Awaitable[dict]]
"""一个agent处理某个能力任务的实际逻辑：接收`A2ATask`，返回结果payload（dict）。
处理失败应该直接抛异常，由`InProcessTransport`捕获并转换成`A2AResult(status="error")`，
调用方不需要自己处理异常转换。"""


class InProcessTransport:
    """把已注册的agent handler函数，包装成`AgentTransport`协议要求的`send()`接口。"""

    def __init__(self) -> None:
        self._handlers: dict[str, AgentHandler] = {}

    def register_handler(self, agent_name: str, handler: AgentHandler) -> None:
        """登记某个agent名称对应的实际处理函数。"""
        self._handlers[agent_name] = handler

    async def send(self, agent_name: str, task: A2ATask) -> A2AResult:
        handler = self._handlers.get(agent_name)
        if handler is None:
            return A2AResult(
                task_id=task.task_id, status="error",
                error_message=f"no handler registered for agent '{agent_name}'",
            )
        try:
            output = await handler(task)
        except Exception as exc:  # noqa: BLE001
            return A2AResult(task_id=task.task_id, status="error", error_message=str(exc))
        return A2AResult(task_id=task.task_id, status="success", output=output)
