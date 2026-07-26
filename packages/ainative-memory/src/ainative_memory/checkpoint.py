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

# 见ainative_core/config.py里的详细解释：这一行让本文件里所有类型注解
# （比如函数参数、返回值上写的类型）都只被当作字符串暂存，不需要在
# "写代码这一刻"就真的存在——`build_saver: Callable[[], Awaitable[Any]]`
# 这类写法因此不会在旧版本Python上报错。
from __future__ import annotations

# asyncio是Python标准库自带的"异步编程"模块——见下面`asyncio.Lock`第一次
# 出现的地方详细解释它具体是什么、为什么这里需要它。
import asyncio

# time是Python标准库自带的、跟"时间"相关的模块。这里用到的是
# `time.monotonic()`——见下面第一次出现的地方详细解释它和`time.time()`
# 的区别，以及为什么这个场景必须用它而不是更常见的`time.time()`。
import time

# Awaitable/Callable都是"给函数本身写类型注解"用的工具：
# - `Callable[[], Awaitable[Any]]`表示"一个不接收任何参数、调用后会
#   返回一个'可以被await的东西'的函数"——也就是"一个异步函数"。
#   之所以要嵌套写`Awaitable[Any]`而不是直接写`Any`，是因为异步函数
#   被调用时不会立刻执行、立刻返回结果，而是先返回一个"承诺稍后给你
#   结果"的对象（协程对象），必须用`await`才能真正拿到里面的结果。
from collections.abc import Awaitable, Callable

# Any表示"任意类型"——这里用在"存储句柄具体是什么类型"上，因为本类
# 完全不关心调用方传入的`build_saver`具体连接的是Postgres/Redis还是
# 别的什么，只关心"能不能拿到一个东西、拿不到时该怎么处理"。
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

    # `__init__`是Python类的"构造函数"——创建一个`CheckpointSaverFactory()`
    # 实例时，Python会自动调用这个方法，把传入的参数存到`self`（代表
    # "正在构造的这个实例自己"）上，供之后其他方法使用。
    def __init__(
        self,
        build_saver: Callable[[], Awaitable[Any]],
        # `*`出现在参数列表中间：它后面的参数（permanent_failure_types/
        # retry_interval_seconds）必须用"参数名=值"的方式传入，不能只按
        # 位置传（比如不能写`CheckpointSaverFactory(fn, (ValueError,), 10)`，
        # 必须写`CheckpointSaverFactory(fn, permanent_failure_types=(ValueError,), retry_interval_seconds=10)`）。
        # 好处：调用方一眼就能看出每个参数具体控制的是什么，不用去翻函数
        # 签名数参数顺序。
        *,
        # `tuple[type[BaseException], ...]`：一个元组，里面每一项都是
        # "某个异常类型本身"（不是异常的实例），长度不限。默认值只有
        # `ImportError`一项——表示"默认情况下，只有'依赖包没装'这种
        # 错误会被当成永久性失败"。
        permanent_failure_types: tuple[type[BaseException], ...] = (ImportError,),
        retry_interval_seconds: float = 30.0,
    ) -> None:
        # 把构造时传入的三个参数，分别存成`self.`开头的"实例属性"——
        # 前缀下划线（`_build_saver`而不是`build_saver`）是Python的
        # 命名约定，表示"这是这个类内部使用的实现细节，不打算让外部
        # 代码直接读写"。
        self._build_saver = build_saver
        self._permanent_failure_types = permanent_failure_types
        self._retry_interval_seconds = retry_interval_seconds

        # 下面四个是这个类内部用来"记住当前状态"的字段，初始状态全部
        # 是"什么都还没发生"：
        self._saver: Any | None = None
        # ↑ 一旦成功构建出存储句柄，就缓存在这里——这就是"单例懒加载"
        #   里"懒"的意思：不在构造函数里立刻去连接数据库，而是等第一次
        #   真正调用`get()`时才尝试，一旦成功就复用同一个句柄，不用每次
        #   都重新连接。

        self._permanently_failed = False
        # ↑ 一旦命中"永久性失败"，就把这个标志设成True，之后`get()`
        #   直接返回None，不再浪费时间重试，直到`reset()`被调用清零。

        self._last_attempt_at: float | None = None
        # ↑ 记录"上一次尝试构建"发生在什么时刻，用于计算"距离上次失败
        #   过去了多久"，从而判断"现在该不该再试一次"。

        # asyncio.Lock是Python标准库`asyncio`（用来写"异步/并发"程序）
        # 提供的一把"锁"——可以把它想象成一间只能容纳一个人的房间：
        # 谁想进去（执行`async with self._lock:`包住的代码块），先要
        # 排队等前一个人出来。这里用它防止的场景是：如果有好几个协程
        # （可以理解成"好几段可以交替执行的代码"）几乎同时调用`get()`，
        # 而这时候`self._saver`还是None，如果不加锁，每个协程都会各自
        # 跑一遍`await self._build_saver()`，重复连接数据库好几次，
        # 浪费资源还可能引发别的问题。加了这把锁之后，只有第一个协程
        # 真正执行构建逻辑，其余协程会在锁外先排队等待，等第一个协程
        # 释放锁之后，会发现`self._saver`已经有值了，直接复用，不会
        # 重复构建。
        self._lock = asyncio.Lock()

    # 方法名前面的`async`表示这是一个"异步方法"——调用方需要写
    # `await factory.get()`，而不是直接`factory.get()`。之所以要异步，
    # 是因为内部真正构建存储句柄（`self._build_saver()`）几乎总是要
    # 连接数据库/网络，这个过程需要"等待"，异步让程序在等待期间可以先
    # 去处理别的任务，而不是傻等在这里浪费CPU时间。
    async def get(self) -> Any | None:
        # 第一次"快速检查"（不加锁）：如果已经有缓存好的句柄，直接
        # 返回，省去每次调用都要去抢锁排队的开销——绝大多数调用会走
        # 这条最快的路径。
        if self._saver is not None:
            return self._saver
        # 同理，如果已经确定是永久性失败，直接返回None，不用再往下走。
        if self._permanently_failed:
            return None

        # 只有"看起来还需要真正尝试构建"的情况，才会走到这里去抢锁。
        # `async with self._lock:`表示"进入这个代码块前，先等这把锁
        # 空出来（如果已经有别的协程占着），拿到锁之后才继续往下执行；
        # 代码块结束时（不管是正常结束还是中途抛异常），自动释放锁，
        # 让排在后面的协程能拿到锁"。
        async with self._lock:
            # 拿到锁之后要"再检查一遍"（这是并发编程里的标准套路，叫
            # "双重检查锁定"）：因为有可能在"我们决定要抢锁"和"我们
            # 真正抢到锁"这段时间里，另一个协程已经抢先一步、完整地
            # 构建好了句柄——如果不再检查一次，我们会重复构建一遍，
            # 白白浪费资源。
            if self._saver is not None:
                return self._saver
            if self._permanently_failed:
                return None

            # `time.monotonic()`返回一个"只会一直往前走、不会因为系统
            # 时间被人为调整（比如手动改系统时钟、夏令时切换）而跳变"
            # 的计时器读数，单位是秒，但这个数字本身没有实际日历含义
            # （不能拿它当"现在几点"用）——专门用于"测量两个时间点之间
            # 过了多久"这类场景，比更常见的`time.time()`（返回真实的
            # "从1970年到现在的秒数"，可能因系统时钟被调整而跳变）更
            # 适合用来做"距离上次重试过去了多久"这种计时判断，不会因为
            # 系统时钟被意外调整而算错重试间隔。
            now = time.monotonic()
            # 判断"是否还在节流冷却期内"：如果上一次尝试过（不是None），
            # 并且"现在减去上一次尝试的时间"小于设定的重试间隔，说明
            # 冷却时间还没到，直接返回None，不去真正尝试构建（避免暂时
            # 性失败之后疯狂无间断重试，给数据库/网络添乱）。
            if self._last_attempt_at is not None and (now - self._last_attempt_at) < self._retry_interval_seconds:
                return None

            # 走到这里说明确实要真正尝试一次了，先记下这次尝试的时间点，
            # 供下一次调用计算冷却时间用。
            self._last_attempt_at = now
            # try/except：Python里"尝试执行一段可能出错的代码，出错时
            # 不让程序直接崩溃，而是转去执行对应的except分支"的语法结构。
            try:
                # 真正调用调用方传入的构建函数——因为它是异步函数，
                # 需要用`await`等待它执行完、拿到真正的结果（存储句柄）。
                self._saver = await self._build_saver()
            # `except self._permanent_failure_types:`：捕获"类型属于
            # `self._permanent_failure_types`这个元组里任意一种"的异常。
            # Python的except支持直接传一个"异常类型的元组"，表示"这几种
            # 异常里任意一种发生了，都走这个分支处理"，不需要写多个
            # except。这是判定"永久性失败"的地方。
            except self._permanent_failure_types:
                self._permanently_failed = True
                return None
            # `except Exception:`：捕获几乎所有其他类型的异常（比`except
            # self._permanent_failure_types`更宽泛的兜底分支）——这里
            # 把它们全部归类为"暂时性失败"。这一行末尾还有一条给代码检查
            # 工具ruff看的特殊指令，意思是"这一行故意宽泛捕获异常，请
            # 不要对这行报'捕获过于宽泛的Exception'这类警告"——因为这里
            # 的"宽泛"是刻意设计，不是疏忽。
            except Exception:  # noqa: BLE001
                # 暂时性失败：不锁定，下一次达到 retry_interval_seconds 后会再次尝试。
                return None
            # 走到这里说明构建成功，把刚构建好的句柄返回给调用方。
            return self._saver

    def reset(self) -> None:
        # 把三项内部状态全部恢复成"最初始"的样子——常用于测试之间
        # 相互隔离（避免上一个测试留下的缓存句柄/失败状态影响下一个
        # 测试），或者运维在确认修复了导致永久性失败的问题后，不重启
        # 进程也能让程序重新尝试。这个方法本身不是`async`（不是异步
        # 方法）——因为它只是简单地把几个变量重新赋值，不涉及任何
        # 需要"等待"的操作。
        self._saver = None
        self._permanently_failed = False
        self._last_attempt_at = None
