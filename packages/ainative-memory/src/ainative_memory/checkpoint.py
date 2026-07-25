"""Checkpoint存储句柄的单例懒加载工厂 + 永久性/暂时性失败分类重试。

改造自真实项目里验证过的LangGraph checkpoint持久化设计模式（原版深度耦合
`AsyncPostgresSaver`/`psycopg_pool`，这里把"单例懒加载 + 失败分类重试"这个
与具体存储后端无关的策略骨架抽出来，具体连接哪种数据库由调用方传入的
`build_saver`回调决定）。

核心设计：
1. **永久性失败 vs 暂时性失败**：`ImportError`（依赖包未安装）之类的错误，
   重试没有意义——锁定状态，不再重试，直到`reset()`被显式调用（通常在
   测试里，或者运维确认修复依赖后重启进程）。其余异常（数据库连接抖动等）
   视为暂时性——在可配置的重试窗口内、按节流间隔重新尝试，故障自愈后
   自动恢复，不需要重启进程。
2. **`asyncio.Lock`防止并发重复初始化**：多个协程同时调用`get()`时，
   只有一个真正执行`build_saver()`，其余等待同一个结果。
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any


class CheckpointSaverFactory:
    """`ainative_core.protocols.CheckpointSaverFactory`的通用实现。

    Args:
        build_saver: 实际构建存储句柄的异步回调（真实项目里会去连接
            Postgres/Redis等）。构建失败时应该抛出异常，本类据此分类。
        permanent_failure_types: 视为"永久性失败"的异常类型元组，命中后
            不再重试，直到`reset()`被调用。默认只包含`ImportError`。
        retry_interval_seconds: 暂时性失败后，两次重试之间的节流间隔——
            故障自愈（比如数据库连接恢复）后，下一次`get()`调用会自动
            重新尝试并成功，不会无限期锁定，也不需要重启进程。
    """

    def __init__(
        self,
        build_saver: Callable[[], Awaitable[Any]],
        *,
        permanent_failure_types: tuple[type[BaseException], ...] = (ImportError,),
        retry_interval_seconds: float = 30.0,
    ) -> None:
        self._build_saver = build_saver
        self._permanent_failure_types = permanent_failure_types
        self._retry_interval_seconds = retry_interval_seconds

        self._saver: Any | None = None
        self._permanently_failed = False
        self._last_attempt_at: float | None = None
        self._lock = asyncio.Lock()

    async def get(self) -> Any | None:
        if self._saver is not None:
            return self._saver
        if self._permanently_failed:
            return None

        async with self._lock:
            if self._saver is not None:
                return self._saver
            if self._permanently_failed:
                return None

            now = time.monotonic()
            if self._last_attempt_at is not None and (now - self._last_attempt_at) < self._retry_interval_seconds:
                return None

            self._last_attempt_at = now
            try:
                self._saver = await self._build_saver()
            except self._permanent_failure_types:
                self._permanently_failed = True
                return None
            except Exception:  # noqa: BLE001
                # 暂时性失败：不锁定，下一次达到 retry_interval_seconds 后会再次尝试。
                return None
            return self._saver

    def reset(self) -> None:
        self._saver = None
        self._permanently_failed = False
        self._last_attempt_at = None
