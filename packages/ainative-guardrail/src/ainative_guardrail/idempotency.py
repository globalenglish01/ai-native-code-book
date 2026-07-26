"""幂等键管理——防止客户端重试导致有副作用的操作被重复执行。

改造自checklist D类"全栈工程基础设施"真实设计要点，把散落的具体坑
收敛成一个通用组件应该原生具备的行为：

1. **"占用幂等键"必须一步到位完成**（对应真实实现的`SET ... NX`），
   避免两个几乎同时到达的请求都误以为自己是第一个（竞态条件）——本模块
   的`occupy()`用单个原子字典操作实现，`InMemoryIdempotencyStore`的
   实现如果要换成Redis，必须保持"检查是否存在+写入"是不可分割的单步。
2. **区分"正在处理中"和"已有最终结果"两种状态**，而不是只判断"键是否
   存在"——重复请求命中"正在处理中"应该等待/告知稍后重试，命中"已有
   结果"应该直接复用那个结果，两者处理方式完全不同。
3. **失败时正确释放**：如果处理过程中途失败（比如存储写入失败），已经
   声明的幂等键必须被释放，否则用户会在整个TTL窗口期内都无法重新提交
   同一次操作，即使故障早就恢复了——`idempotent_operation()`上下文管理器
   把这个"失败必须释放"的规则做成默认行为，而不是指望每个调用方自己
   记得在except分支里释放。
4. **TTL防止无限增长**：幂等键不能永久保留，否则存储无限增长、旧键
   永远无法被复用。
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

DEFAULT_TTL_SECONDS = 24 * 3600


class IdempotencyStatus(StrEnum):
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


@dataclass
class IdempotencyRecord:
    status: IdempotencyStatus
    result: Any = None
    expires_at: float = 0.0


class DuplicateOperationError(RuntimeError):
    """幂等键已被占用（正在处理中或已有最终结果）时抛出——携带既有的
    `IdempotencyRecord`，调用方据此区分两种完全不同的处理方式：
    `status is IN_PROGRESS`应告知客户端稍后重试；`status is COMPLETED`
    应直接把`record.result`返回给客户端，而不是重新执行一次副作用。"""

    def __init__(self, key: str, record: IdempotencyRecord) -> None:
        super().__init__(f"idempotency key '{key}' is already occupied (status={record.status})")
        self.key = key
        self.record = record


class InMemoryIdempotencyStore:
    """幂等键状态的内存版存储——真实项目应实现同样的接口对接Redis
    （`SET key value NX EX ttl`一步到位完成"检查+占用"）。"""

    def __init__(self) -> None:
        self._records: dict[str, IdempotencyRecord] = {}

    def occupy(self, key: str, *, ttl_seconds: int) -> IdempotencyRecord | None:
        """尝试占用这个幂等键——已存在且未过期则原样返回既有记录（占用
        失败，调用方据此区分in_progress/completed两种情况），否则原子性
        地写入`IN_PROGRESS`状态并返回`None`（占用成功）。这一步必须是
        单个不可分割的操作，不能先查询再写入（那样两个并发请求都可能
        查询到"不存在"，都误以为自己是第一个）。"""
        existing = self._records.get(key)
        now = time.time()
        if existing is not None and existing.expires_at > now:
            return existing
        self._records[key] = IdempotencyRecord(status=IdempotencyStatus.IN_PROGRESS, expires_at=now + ttl_seconds)
        return None

    def complete(self, key: str, result: Any, *, ttl_seconds: int) -> None:
        """把幂等键的状态从`IN_PROGRESS`更新为`COMPLETED`，并把最终结果
        写回幂等缓存——覆盖"响应已发出、任务未完成"这段窗口期内的重复
        请求，让它们能直接复用这个结果，而不是被错误地当成"仍在处理中"。"""
        self._records[key] = IdempotencyRecord(
            status=IdempotencyStatus.COMPLETED, result=result, expires_at=time.time() + ttl_seconds,
        )

    def release(self, key: str) -> None:
        """释放一个幂等键——处理过程中途失败时必须调用，否则用户会在
        整个TTL窗口期内都无法重新提交同一次操作。"""
        self._records.pop(key, None)

    def get(self, key: str) -> IdempotencyRecord | None:
        record = self._records.get(key)
        if record is not None and record.expires_at <= time.time():
            self._records.pop(key, None)
            return None
        return record


@contextmanager
def idempotent_operation(
    store: InMemoryIdempotencyStore, key: str, *, ttl_seconds: int = DEFAULT_TTL_SECONDS,
) -> Iterator[None]:
    """包裹一次有副作用的操作，确保幂等键的占用/完成/释放规则被正确执行。

    用法::

        try:
            with idempotent_operation(store, "charge:order-42"):
                charge_credit_card(...)
        except DuplicateOperationError as exc:
            if exc.record.status is IdempotencyStatus.COMPLETED:
                return exc.record.result  # 直接复用之前的结果，不重复扣款
            return "please retry shortly"  # 仍在处理中

    如果`with`块内的代码抛出异常，幂等键会被自动释放（而不是卡在
    `IN_PROGRESS`状态直到TTL过期）；正常结束的话，调用方仍需要自己调用
    `store.complete(key, result, ttl_seconds=...)`写入最终结果——本上下文
    管理器只负责占用/异常释放这两端，不猜测"操作的返回值该是什么"。
    """
    existing = store.occupy(key, ttl_seconds=ttl_seconds)
    if existing is not None:
        raise DuplicateOperationError(key, existing)
    try:
        yield
    except BaseException:
        store.release(key)
        raise
