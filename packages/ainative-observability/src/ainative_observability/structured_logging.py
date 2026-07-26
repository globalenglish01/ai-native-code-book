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

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Callable
from typing import Any

DEFAULT_SENSITIVE_KEYS: frozenset[str] = frozenset({
    "password", "passwd", "pwd", "token", "api_key", "apikey", "secret",
    "authorization", "auth", "access_token", "refresh_token", "session_id",
    "cookie", "private_key", "credit_card", "ssn",
})
"""默认的字段名黑名单——日志record的`extra=`字段里，key名（不区分大小写）
命中这个集合的，值会被整体替换成`[REDACTED]`，不管值本身长什么样。"""

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

    def __init__(
        self,
        *,
        sensitive_keys: frozenset[str] = DEFAULT_SENSITIVE_KEYS,
        value_patterns: tuple[re.Pattern[str], ...] = _DEFAULT_VALUE_PATTERNS,
    ) -> None:
        super().__init__()
        self._sensitive_keys = {k.lower() for k in sensitive_keys}
        self._value_patterns = value_patterns

    def filter(self, record: logging.LogRecord) -> bool:
        # 永远返回True——这是"脱敏"过滤器，不是"丢弃"过滤器，绝不能让
        # 敏感信息处理本身的逻辑意外拦截掉整条日志的产生。
        try:
            record.msg = self._redact_text(str(record.msg))
            record.args = ()  # msg已经是最终字符串，避免%%格式化再次拼回原始参数
            for key in list(vars(record).keys()):
                if key.lower() in self._sensitive_keys:
                    setattr(record, key, "[REDACTED]")
        except Exception:
            pass
        return True

    def _redact_text(self, text: str) -> str:
        for pattern in self._value_patterns:
            text = pattern.sub(self._replace_value_group, text)
        return text

    @staticmethod
    def _replace_value_group(match: re.Match[str]) -> str:
        """只替换捕获组里的"值"部分，保留匹配到的其余前缀（如key名/`bearer `），
        用span偏移量定位value在整个match里的精确位置，而不是靠字符串内容
        搜索——避免value本身的内容恰好在match别处重复出现时定位错误。"""
        full = match.group(0)
        value_group_index = 2 if match.lastindex and match.lastindex >= 2 else 1
        start, end = match.span(value_group_index)
        match_start = match.start(0)
        return full[: start - match_start] + "[REDACTED]" + full[end - match_start :]


class JsonFormatter(logging.Formatter):
    """把日志record格式化成单行JSON——可以按字段查询/过滤的结构化数据。

    默认字段：`timestamp`（ISO-ish epoch秒，不受系统时间被人为调整影响，
    用`time.time()`而非`datetime.now()`格式化字符串）、`level`、`logger`、
    `message`，外加所有非标准`LogRecord`属性的`extra=`字段。
    """

    _STANDARD_ATTRS = frozenset(vars(logging.LogRecord("", 0, "", 0, "", (), None)).keys())

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": time.time(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in vars(record).items():
            if key not in self._STANDARD_ATTRS and key not in payload:
                payload[key] = value
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        try:
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
    handler = handler_factory()
    handler.setFormatter(JsonFormatter())
    handler.addFilter(SensitiveDataFilter(sensitive_keys=sensitive_keys))
    logger.addHandler(handler)
    return handler
