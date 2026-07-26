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

# time模块是Python标准库自带的，`time.time()`返回"现在是什么时候"，
# 用一个浮点数表示——具体含义是"从1970年1月1日0点到现在，一共过了多少
# 秒"，详见ainative_core.usage_tracking模块里的详细解释。
import time

# Iterator用来给"生成器函数"（用yield而不是return的函数）写返回值类型
# 注解——见下面idempotent_operation函数的详细解释。
from collections.abc import Iterator

# contextmanager是Python标准库提供的一个装饰器，能把一个"生成器函数"
# 转换成一个"上下文管理器"——也就是可以配合`with ...:`语法使用的对象。
# 见下面idempotent_operation函数的详细解释。
from contextlib import contextmanager

# dataclass见limits.py/config.py里的详细解释：自动生成构造函数等
# 样板代码的装饰器。
from dataclasses import dataclass

# StrEnum是Python 3.11新增的"字符串枚举"——枚举成员本身"就是"一个
# 字符串，见ainative_core.model_factory模块里的详细解释。
from enum import StrEnum

# Any表示"这里可以是任意类型"。
from typing import Any

DEFAULT_TTL_SECONDS = 24 * 3600
# ↑ 幂等键默认的存活时长（单位：秒）——`24 * 3600`就是"24小时对应的
#   秒数"，写成乘法算式而不是直接写死86400，是为了让人一眼就能看出
#   "这是24小时"，不需要自己心算。


# class IdempotencyStatus(StrEnum): 定义了一个枚举类——把"幂等键当前
# 处于哪个状态"这件事，从"随便用字符串比较"（容易因为拼错字符串而
# 产生bug）变成"只能是这两个明确定义好的状态之一"，IDE/类型检查工具
# 能帮你发现"传了一个不存在的状态"这类错误。
class IdempotencyStatus(StrEnum):
    IN_PROGRESS = "in_progress"
    # ↑ 表示"这个操作正在处理中，还没有最终结果"。
    COMPLETED = "completed"
    # ↑ 表示"这个操作已经处理完成，有最终结果可以直接复用"。


# 这里是`@dataclass`，没有加`frozen=True`——意味着IdempotencyRecord创建
# 之后，字段仍然可以被修改。这是刻意的：同一个幂等键对应的记录，会随着
# 处理进度从IN_PROGRESS变成COMPLETED（见下面InMemoryIdempotencyStore的
# complete方法），概念上就是"一个会被更新的可变状态"，不适合冻结。
@dataclass
class IdempotencyRecord:
    status: IdempotencyStatus
    # `result: Any = None` ——处理完成后的最终结果，类型不限（因为不同
    # 业务场景的"结果"形状完全不同），默认是None（表示"还没有结果"，
    # 对应IN_PROGRESS状态）。
    result: Any = None
    # 这条记录到期失效的具体时刻（Unix时间戳），默认0.0——一个远早于
    # "现在"的值，纯粹是给字段一个类型正确的占位默认值，真正使用时
    # 总会显式传入一个有意义的过期时间。
    expires_at: float = 0.0


# class DuplicateOperationError(RuntimeError): 表示这个类"继承"自
# RuntimeError（Python内置的"运行时错误"异常类型）——意味着它本身就是
# 一种可以被`raise`（抛出）、被`except`（捕获）的异常。专门用来表达
# "这次操作因为幂等键已经被占用而没有真正执行"这个具体情况，方便调用
# 代码用`except DuplicateOperationError`单独捕获处理。
class DuplicateOperationError(RuntimeError):
    """幂等键已被占用（正在处理中或已有最终结果）时抛出——携带既有的
    `IdempotencyRecord`，调用方据此区分两种完全不同的处理方式：
    `status is IN_PROGRESS`应告知客户端稍后重试；`status is COMPLETED`
    应直接把`record.result`返回给客户端，而不是重新执行一次副作用。"""

    def __init__(self, key: str, record: IdempotencyRecord) -> None:
        # `super().__init__(...)` 调用父类（RuntimeError）的构造函数，
        # 传入一句描述这次异常的文字信息——这样即使调用方只是简单地
        # `print(exc)`或者让异常信息显示在日志里，也能看到一句有意义的
        # 说明，而不是空白。
        super().__init__(f"idempotency key '{key}' is already occupied (status={record.status})")
        # 除了父类的标准异常信息，额外把key和record这两个具体数据存到
        # 这个异常实例自己的属性上——这样调用方在`except`代码块里，可以
        # 通过`exc.key`/`exc.record`直接拿到结构化的数据，不需要从
        # 异常信息的文字里反过来解析。
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
            # 已经有一条记录、而且还没过期——说明这个幂等键已经被占用
            # （不管是正在处理中还是已经完成），把既有记录原样返回，
            # 让调用方（idempotent_operation）据此抛出DuplicateOperationError。
            return existing
        # 走到这里，说明这个key之前没有记录，或者记录已经过期——可以
        # 把它当作"全新的"来占用：写入一条状态是IN_PROGRESS的新记录，
        # 过期时间设为"现在+ttl_seconds"。因为Python里字典的单次赋值
        # 操作`self._records[key] = ...`天然是不可分割的一步（不会被
        # 拆成"先查再写"两步暴露给其他并发代码看到中间状态），这就是
        # docstring里强调的"原子性"的具体实现方式。
        self._records[key] = IdempotencyRecord(status=IdempotencyStatus.IN_PROGRESS, expires_at=now + ttl_seconds)
        # 返回None表示"占用成功"（调用方据此认为"我是第一个正在处理
        # 这个操作的人，可以放心继续往下执行"）。
        return None

    def complete(self, key: str, result: Any, *, ttl_seconds: int) -> None:
        """把幂等键的状态从`IN_PROGRESS`更新为`COMPLETED`，并把最终结果
        写回幂等缓存——覆盖"响应已发出、任务未完成"这段窗口期内的重复
        请求，让它们能直接复用这个结果，而不是被错误地当成"仍在处理中"。"""
        # 直接用一条全新的IdempotencyRecord覆盖旧记录——状态改成
        # COMPLETED，带上真正的结果，过期时间也重新从"现在"开始计算
        # （而不是延续之前IN_PROGRESS阶段设置的过期时间）。
        self._records[key] = IdempotencyRecord(
            status=IdempotencyStatus.COMPLETED, result=result, expires_at=time.time() + ttl_seconds,
        )

    def release(self, key: str) -> None:
        """释放一个幂等键——处理过程中途失败时必须调用，否则用户会在
        整个TTL窗口期内都无法重新提交同一次操作。"""
        # `dict.pop(key, None)` ——尝试从字典里删除这个key对应的记录，
        # 如果key本身根本不存在，就返回None而不报错（普通的`del
        # self._records[key]`如果key不存在会直接抛KeyError崩溃，这里
        # 用pop加默认值是更安全的写法）。
        self._records.pop(key, None)

    def get(self, key: str) -> IdempotencyRecord | None:
        record = self._records.get(key)
        if record is not None and record.expires_at <= time.time():
            # 找到了记录，但已经过期——顺手把它从存储里清理掉（避免
            # 过期数据一直占着内存），并且明确返回None，告诉调用方
            # "这个键现在等同于不存在"。
            self._records.pop(key, None)
            return None
        return record


# @contextmanager 是一个装饰器，作用是把下面这个"生成器函数"（内部
# 用了`yield`关键字的函数）转换成一个可以配合`with`语句使用的"上下文
# 管理器"。工作原理：调用这个函数时，代码只会执行到`yield`那一行就
# 暂停，把控制权交还给`with`语句块，让块内的代码开始执行；等`with`块
# 内的代码执行完毕（不管是正常结束还是抛出了异常），才会回到这个函数
# 里`yield`之后剩下的代码继续执行——这样就能把"进入with块之前要做什么"
# 和"离开with块之后要做什么（包括异常处理）"写在同一个函数里，而不用
# 单独定义一个带`__enter__`/`__exit__`方法的类。
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
    # 先尝试占用这个幂等键——如果occupy返回的不是None，说明占用失败
    # （这个键已经被别的请求占用了）。
    existing = store.occupy(key, ttl_seconds=ttl_seconds)
    if existing is not None:
        # 占用失败，直接抛出DuplicateOperationError，携带已有记录——
        # 这会在`with`语句真正进入代码块之前就中断，调用方写的
        # `with idempotent_operation(...):`那一行本身就会抛出这个异常，
        # 块内的代码根本不会被执行到。
        raise DuplicateOperationError(key, existing)
    try:
        # `yield`——把控制权交还给调用方的`with`代码块，让块内的真正
        # 业务逻辑（比如"扣款"）开始执行。这里`yield`不带任何值（对应
        # 类型注解`Iterator[None]`），因为`with idempotent_operation(...)
        # as x:`这种写法在这里用不上，调用方不需要从这个上下文管理器
        # 拿到什么东西，只是需要它管理"占用/释放"这件事本身。
        yield
    except BaseException:
        # `BaseException`是Python里"所有异常的总祖先"（比更常见的
        # `Exception`范围更广，连`KeyboardInterrupt`、`SystemExit`这类
        # 特殊情况也会被捕获）——这里故意捕获最广的范围，是因为"只要
        # with块内发生了任何形式的中断，都必须释放幂等键"，不能有任何
        # 例外情况被漏掉。
        store.release(key)
        # `raise`（不带任何参数）表示"把刚刚捕获到的这个异常原样重新
        # 抛出"——释放幂等键只是一个"顺便要做的清理动作"，不代表这次
        # 异常已经被处理掉了，异常本身还是要继续往外传播，让调用方的
        # `except DuplicateOperationError`或别的更外层代码能看到并处理
        # 真正的错误原因。
        raise
