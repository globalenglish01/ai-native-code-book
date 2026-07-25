"""HITL超时业务规则：默认时长读取 + 从代码结构上禁止"超时默认放行"这类危险默认值。

改造自真实项目里验证过的设计：多条独立的HITL执行路径（不同存储/触发
机制）容易各自硬编码一份几乎相同的"超时后怎么办"业务规则，任一处调整
另一处不会感知。核心安全原则：**超时后自动决定绝不能是"批准"**——
`safe_timeout_decision()`从函数签名结构上就不接受"决定类型"这个参数，
杜绝调用方误传一个危险的默认值，而不是靠注释/约定去防范。
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 86400
"""默认超时时长（秒）：24小时，足够容忍人工审批的正常延迟，又不至于让被
遗忘的审批无限期占用资源。"""

_SAFE_TIMEOUT_DECISION_TYPE = "reject"
_DEFAULT_TIMEOUT_MESSAGE = "Approval timed out (no response within the timeout window); automatically rejected."


def read_timeout_seconds(env_name: str, *, default: int = DEFAULT_TIMEOUT_SECONDS) -> int:
    """读取指定环境变量的超时秒数；缺失/非法/非正数都回退到`default`（fail-safe）。"""
    raw = os.getenv(env_name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        logger.warning("HITL timeout config %s=%r is invalid, falling back to %ds", env_name, raw, default)
        return default
    if value <= 0:
        logger.warning("HITL timeout config %s=%d is non-positive, falling back to %ds", env_name, value, default)
        return default
    return value


def safe_timeout_decision(message: str | None = None) -> dict:
    """构造一个"超时自动决定"。

    安全原则：类型恒为`reject`，调用方无法指定其他类型（本函数不接收
    type参数）——这是唯一被允许的超时默认决定构造入口。
    """
    return {"type": _SAFE_TIMEOUT_DECISION_TYPE, "message": message or _DEFAULT_TIMEOUT_MESSAGE}


def safe_timeout_decisions(count: int, message: str | None = None) -> list[dict]:
    """为`count`个待审批操作各生成一个安全的超时reject决定。"""
    return [safe_timeout_decision(message) for _ in range(max(0, count))]
