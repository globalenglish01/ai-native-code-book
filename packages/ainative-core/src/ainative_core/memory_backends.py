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

from typing import Any


class InMemoryUsageSink:
    """`UsageSink` 的内存版实现——把所有用量事件存进一个list。"""

    def __init__(self) -> None:
        self._events: list[dict[str, Any]] = []

    def record(self, event: dict[str, Any]) -> None:
        self._events.append(event)

    @property
    def events(self) -> list[dict[str, Any]]:
        """只读视图，供测试/demo读取已记录的全部事件。"""
        return list(self._events)

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
