"""`UsageSink` 协议的内存版默认实现。

配合`ainative_core.usage_tracking.UsageTrackingCallbackHandler`使用——
`build_model`/`build_agent_model`等工厂函数接受一个可选的`usage_sink`
参数，传入`InMemoryUsageSink`实例即可脱离任何真实数据库直接跑通
用量统计的demo和测试。

`PromptStore`协议的内存版默认实现在`ainative_prompt.store.InMemoryPromptStore`
（更贴近实际使用场景：与`load_prompt()`/`ab_select_deterministic()`等
Prompt管理逻辑放在同一个包里），不在这里重复实现。
"""

from __future__ import annotations

import copy
from typing import Any


class InMemoryUsageSink:
    """`UsageSink` 的内存版实现——把所有用量事件存进一个list。

    `record()`对传入的`event`做深拷贝再存储，`events`属性也对每个已存储
    事件做深拷贝再返回——不能只存/返回原始dict引用：调用方常见的用法是
    复用一个"模板"事件dict（比如`base = {...}; event = {**base, "field": x}`
    这类不一致的构造方式，或者拿到`events`列表后就地修改某一条做二次
    处理），如果内部存储/对外返回的是同一个dict对象，这类操作会静默
    篡改"已经记录完成"的历史用量数据，且没有任何报错信号。
    """

    def __init__(self) -> None:
        self._events: list[dict[str, Any]] = []

    def record(self, event: dict[str, Any]) -> None:
        self._events.append(copy.deepcopy(event))

    @property
    def events(self) -> list[dict[str, Any]]:
        """只读视图，供测试/demo读取已记录的全部事件。"""
        return copy.deepcopy(self._events)

    def total_tokens(self) -> int:
        """把所有事件的 input_tokens+output_tokens 累加——demo/测试里常用的快捷统计。"""
        total = 0
        for event in self._events:
            total += event.get("input_tokens", 0) or 0
            total += event.get("output_tokens", 0) or 0
        return total

    def total_for_agent(self, agent_name: str) -> int:
        """按`agent_name`过滤后再累加——用于统计某个具体agent的用量。"""
        total = 0
        for event in self._events:
            if event.get("agent_name") == agent_name:
                total += event.get("input_tokens", 0) or 0
                total += event.get("output_tokens", 0) or 0
        return total
