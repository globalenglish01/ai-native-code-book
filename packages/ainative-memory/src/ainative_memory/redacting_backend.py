"""摘要溢出落盘前的PII脱敏代理（Proxy模式）。

改造自真实项目里验证过、几乎零耦合的设计：`SummarizationMiddleware`把
对话历史溢出（overflow）内容落盘之前，需要先做PII脱敏。本模块提供一个
只拦截真正执行"写入"的方法（`write`/`edit`/`awrite`/`aedit`）、其余方法
透传给底层backend的代理类——不关心底层backend具体是本地文件、S3还是
别的什么，只要求它有这几个方法（鸭子类型）。

**关键架构约束（必须遵守，否则会误伤业务文件读写）**：这个代理只应该
传给"负责摘要溢出持久化"的组件（如`SummarizationMiddleware(backend=...)`），
绝不能传给"模型自身文件读写工具"用的backend（如`FilesystemMiddleware`）——
同一个原始backend实例通常会被两处同时使用，调用方必须持有两个独立引用：
原始backend给文件读写工具，`wrap_summarization_backend()`包过的代理给
摘要持久化组件。

`edit()`方法只脱敏`new_string`，不脱敏`old_string`——因为`old_string`
是从磁盘读回的、可能已经脱敏过的旧内容，必须原样传回才能让底层backend
正确定位替换点；脱敏函数本身是幂等的（对已脱敏文本再脱敏一次无害），
所以`new_string`整体脱敏是安全的。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


class RedactingBackend:
    """包装任意"文件系统式"backend，拦截写入方法做脱敏，其余方法透传。"""

    def __init__(self, inner: Any, redact_fn: Callable[[str], str]) -> None:
        self._inner = inner
        self._redact_fn = redact_fn

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def write(self, file_path: str, content: str, *args: Any, **kwargs: Any) -> Any:
        return self._inner.write(file_path, self._redact_fn(content), *args, **kwargs)

    def edit(self, file_path: str, old_string: str, new_string: str, *args: Any, **kwargs: Any) -> Any:
        return self._inner.edit(file_path, old_string, self._redact_fn(new_string), *args, **kwargs)

    async def awrite(self, file_path: str, content: str, *args: Any, **kwargs: Any) -> Any:
        return await self._inner.awrite(file_path, self._redact_fn(content), *args, **kwargs)

    async def aedit(self, file_path: str, old_string: str, new_string: str, *args: Any, **kwargs: Any) -> Any:
        return await self._inner.aedit(file_path, old_string, self._redact_fn(new_string), *args, **kwargs)


def wrap_summarization_backend(backend: Any, redact_fn: Callable[[str], str]) -> RedactingBackend:
    """把`backend`包装成一个只用于摘要持久化路径的脱敏代理。

    Args:
        backend: 原始的文件系统式backend（提供write/edit/awrite/aedit方法）。
        redact_fn: 脱敏函数，比如`ainative_security.pii_redaction.redact_pii_text`——
            本模块不内置任何具体脱敏规则，规则完全由调用方注入。
    """
    return RedactingBackend(backend, redact_fn)
