"""Human-in-the-loop 中断检测 + 超时安全默认值。

改造自真实项目里验证过的设计：Agent运行命中需要人工审批的节点时不抛
异常，而是在返回结果里带上一个标记键（原版是LangGraph约定的
`__interrupt__`），调用方必须显式检查这个键，否则会把"等待人工审批"
误判为"执行完成"。

提取时的改动：`interrupt_key`做成可配置参数（默认`"__interrupt__"`
以兼容LangGraph约定），不假设调用方一定用LangGraph——任何返回dict里
带一个"中断标记键"的编排框架都能复用这套检测逻辑。
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_INTERRUPT_KEY = "__interrupt__"


def extract_interrupt(
    result: dict[str, Any], *, interrupt_key: str = DEFAULT_INTERRUPT_KEY
) -> dict[str, Any] | None:
    """从agent执行结果中提取中断payload（未命中中断则返回`None`）。

    已知限制：中断标记理论上可以是一个列表（多个并行中断），本函数只返回
    第一个元素。这在"每次运行只会挂载一个会触发中断的中间件/节点"这个
    前提下是安全的——如果检测到多个中断，会记录一条WARNING并仍然只返回
    第一个，其余被静默丢弃。如果你的编排里存在多个独立触发中断的分支，
    需要改用能处理`list[dict]`的调用方式，不要依赖本函数的单值返回。
    """
    interrupts = result.get(interrupt_key)
    if not interrupts:
        return None
    if len(interrupts) > 1:
        logger.warning(
            "[HITL] detected %d parallel interrupts, only the first is handled, %d ignored",
            len(interrupts), len(interrupts) - 1,
        )
    first = interrupts[0]
    return first.value if hasattr(first, "value") else first


def count_pending_decisions(interrupt_payload: dict[str, Any] | None, *, requests_key: str = "action_requests") -> int:
    """中断payload里等待人工提交决定的数量。"""
    if not interrupt_payload:
        return 0
    return len(interrupt_payload.get(requests_key, []))
