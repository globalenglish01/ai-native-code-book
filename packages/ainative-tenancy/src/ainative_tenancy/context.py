"""租户身份传播——contextvar驱动，避免"策略定义了但生效条件没满足"这类坑。

改造自真实项目里验证过的运行身份传播模式（`contextvars`驱动的
`set_run_identity`+`assert_*_authorized`），应用到多租户场景：真实事故
背景是Postgres RLS（行级安全）多租户隔离——策略本身定义正确，但运行时
真正激活RLS所需要的会话变量从未被设置，导致隔离策略形同虚设、完全没有
生效（详见checklist E类"多租户资源/容量隔离子类"）。

设计原则：
1. **"当前租户是谁"必须通过显式的contextvar传播，不依赖调用方手动透传
   参数**——这样任何深层调用（比如ORM查询构造、缓存key生成）都能拿到
   当前租户身份，不需要每一层函数签名都加一个`tenant_id`参数。
2. **断言函数而不是"返回True/False让调用方自己判断"**——`assert_tenant_authorized`
   在租户不匹配时直接抛异常，让"忘记检查返回值"这类疏漏在开发阶段就
   暴露出来，而不是悄悄放行未授权访问。
3. **contextvar必须有明确的作用域边界**（`tenant_scope`上下文管理器），
   避免"忘记清理导致下一个请求意外沿用了上一个请求的租户身份"这类
   跨请求串号事故——这在复用连接/线程池的场景下是真实风险。
"""

# 这一行是Python的"未来特性"声明：让下面所有类型注解（函数参数/返回值
# 上写的类型）都不需要在写代码的这一刻真正存在，Python解释器只把它们当
# 作字符串暂存，等真正需要检查类型（比如IDE/mypy）时才去解析。好处是
# `TenantIdentity | None`这种"要么是TenantIdentity类型、要么是None"的
# 写法，在较旧版本Python里如果不加这一行会直接报错，加了就没问题。
from __future__ import annotations

# Iterator——用来给"生成器函数"（下面`tenant_scope`里用到`yield`的函数）
# 写类型注解的工具：表示"这个函数返回一个可以被迭代、每次产出一个
# TenantIdentity的东西"，不是真正提前一次性算好的列表。
from collections.abc import Iterator

# contextmanager——一个装饰器（写在函数前面的@xxx），能把一个"写了
# yield"的普通函数，变成可以配合`with ...:`语句使用的"上下文管理器"。
# 用途：让`with tenant_scope("tenant-42"):`这种写法自动做到"进入with块
# 时执行yield之前的代码，退出with块时（不管是正常结束还是抛了异常）
# 都会执行yield之后的收尾代码"，不需要手写一个完整的类。
from contextlib import contextmanager

# ContextVar——Python标准库`contextvars`模块提供的一种特殊"全局变量"：
# 和普通全局变量不同的是，它的值是"按当前执行上下文（可以理解成大致
# 对应一个请求/一条协程调用链）隔离"的——A请求设置的值，不会被B请求
# 看到，即使两个请求是并发跑在同一个进程里的（普通全局变量做不到这点，
# 会被所有并发请求共享、互相污染）。这正是本文件"租户身份怎么在深层
# 调用里传播、又不会请求间串号"这个核心问题的解决方案。
# Token——调用`ContextVar.set(...)`时返回的一个"回执"，用来在之后调用
# `ContextVar.reset(token)`时，精确恢复到"set之前的那个值"（哪怕中间
# 又被其他代码set过多次，reset也能准确回到当初的状态，支持嵌套场景）。
from contextvars import ContextVar, Token

# dataclass——Python标准库提供的装饰器，自动帮你生成一个类的__init__
# （构造函数）、__repr__（打印时显示的样子）等样板代码，你只需要声明
# "这个类有哪些字段"，不用自己手写一大堆重复代码。
from dataclasses import dataclass


# @dataclass(frozen=True) 这行是"装饰器"语法，意思是：
# 1. dataclass——自动生成构造函数等样板代码（见上面导入处的解释）。
# 2. frozen=True——"冻结"这个类的实例，一旦创建出来，字段就不能再被
#    修改（比如 identity.tenant_id = "other" 会直接报错）。租户身份一旦
#    确定，运行过程中就不应该被悄悄改动，冻结能在结构上杜绝这类风险。
@dataclass(frozen=True)
class TenantIdentity:
    """当前请求归属的租户身份——是"数据隔离"判断的唯一依据。"""

    # 这是这个类唯一的字段：`字段名: 类型`的写法，dataclass会自动帮你
    # 变成构造函数的一个参数，比如`TenantIdentity(tenant_id="tenant-a")`。
    tenant_id: str
    """租户的唯一标识——真实项目里必须对应到一个真正独立计费/独立数据边界的
    实体，而不是仅仅"看起来像租户"的字段（checklist E类真实事故：`tenant_id`
    语义和真实组织概念脱节，这个字段必须严肃对待其语义边界）。"""


# 自定义异常类：直接继承Python内置的`RuntimeError`（表示"程序运行过程
# 中出现的错误"这一大类），不用自己重新实现异常的机制，只是给它起一个
# 更贴合业务含义的名字，方便调用方用`except TenantAuthorizationError:`
# 精确捕获这一类特定错误，而不是笼统地捕获所有RuntimeError。
class TenantAuthorizationError(RuntimeError):
    """`assert_tenant_authorized`发现资源归属的租户和当前上下文租户不一致时抛出。"""


class NoActiveTenantError(RuntimeError):
    """在`tenant_scope`作用域之外调用`get_current_tenant()`时抛出——避免
    "忘记进入租户作用域却仍然执行了本该受隔离保护的操作"这类疏漏被静默放行。"""


# 模块级的私有contextvar：变量名前面加下划线`_`是Python的命名惯例，
# 表示"这是模块内部使用的实现细节，不建议外部代码直接导入/引用"（不是
# 语法强制，只是约定）。类型注解`ContextVar[TenantIdentity | None]`的
# 意思是"这个contextvar存的值，要么是一个TenantIdentity实例，要么是
# None（表示还没设置过）"。`ContextVar(...)`的第一个参数是这个变量的
# 名字（主要用于调试时显示），`default=None`表示"如果从来没有`.set()`
# 过，`.get()`时的兜底值是None"。
_current_tenant: ContextVar[TenantIdentity | None] = ContextVar("ainative_tenancy_current_tenant", default=None)


# @contextmanager——见文件顶部import处的解释：把下面这个带`yield`的
# 普通函数，变成可以配合`with tenant_scope(...):`使用的上下文管理器。
@contextmanager
def tenant_scope(tenant_id: str) -> Iterator[TenantIdentity]:
    """进入一个租户身份的作用域——所有在`with`块内的代码，`get_current_tenant()`
    都能拿到这个身份；退出作用域后自动恢复成外层状态（不是简单清空，支持
    嵌套：比如系统级后台任务临时代入某个租户身份执行一次性操作）。

    用法::

        with tenant_scope("tenant-42"):
            handle_request()  # 内部任何代码都能 get_current_tenant()

    重要边界（`asyncio.create_task`场景）：contextvars的传播是"创建时
    拷贝"语义——在`tenant_scope()`作用域内`asyncio.create_task()`派生出
    的后台任务，会拷贝一份创建时刻的租户身份快照，即使父作用域后续
    退出、`_current_tenant`已经`reset`，这个已经在运行的后台任务也
    **不会**感知到这次reset，会在自己的整个生命周期里继续沿用创建时刻
    那个租户身份。这不是bug（是`contextvars`本身的标准行为），但意味着
    "生命周期可能超出父请求作用域"的后台任务，必须自己在任务体内部
    重新显式调用一次`tenant_scope(...)`来锚定正确的租户身份，而不能
    依赖"我是在某个`tenant_scope`内被创建的，所以租户身份一定是对的"
    这种隐式假设。
    """
    # 用调用方传入的租户ID字符串，构造一个真正的身份对象。
    identity = TenantIdentity(tenant_id=tenant_id)
    # `_current_tenant.set(identity)`——把这个contextvar在"当前执行上下文"
    # 里的值设置成identity，并返回一个Token（回执），用于之后精确恢复。
    token: Token = _current_tenant.set(identity)
    # try/finally——不管`yield`让出去之后、with块内部的代码是正常跑完，
    # 还是中途抛出了异常，`finally`里的代码都保证一定会被执行到，不会
    # 因为异常而被跳过——这正是"作用域退出时必须恢复现场"这个承诺能够
    # 可靠兑现的关键（哪怕with块内部代码出错，也不会导致租户身份泄漏
    # 到作用域之外）。
    try:
        # `yield identity`——把identity交给`with ... as x:`里的x（如果
        # 调用方写了as的话），并且"暂停"在这里，让with块内部的代码开始
        # 执行；等with块执行完毕（或抛出异常），才会继续往下走到finally。
        yield identity
    finally:
        # `_current_tenant.reset(token)`——精确地把这个contextvar恢复成
        # "调用.set()之前"的那个值，而不是简单粗暴地设成None——这样才能
        # 正确支持嵌套的`tenant_scope`（内层退出后，外层设置的身份能够
        # 正确恢复，而不是被清空）。
        _current_tenant.reset(token)


def get_current_tenant() -> TenantIdentity:
    """获取当前上下文的租户身份；不在任何`tenant_scope`作用域内时抛出
    `NoActiveTenantError`，而不是返回`None`让调用方自己记得判断——这是刻意
    的设计：任何需要租户身份的代码路径，如果在没有租户上下文的情况下被
    调用，本身就是一个应该在开发阶段就暴露出来的编程错误。"""
    # 读取当前执行上下文里这个contextvar的值——如果从没进入过任何
    # `tenant_scope`，这里会拿到构造时设置的默认值None。
    identity = _current_tenant.get()
    if identity is None:
        # 没有身份——按照本函数docstring里说明的设计原则，直接抛异常
        # 拒绝，而不是返回None让调用方可能忘记检查、悄悄跳过本该有的
        # 租户隔离逻辑。
        raise NoActiveTenantError(
            "no active tenant_scope() — this code path requires a tenant identity to be set first"
        )
    return identity


def try_get_current_tenant() -> TenantIdentity | None:
    """`get_current_tenant()`的非抛异常版本——仅用于"这段代码确实可能在
    系统级/无租户上下文里运行，需要区分对待"的场景，不应该被当作绕开
    `assert_tenant_authorized`强制检查的手段。"""
    # 直接原样返回contextvar当前的值（可能是TenantIdentity，也可能是
    # None），不做任何检查或抛异常——调用方需要自己处理"可能是None"这
    # 种情况，所以函数名里带"try"（"尝试获取"，不保证一定拿得到）。
    return _current_tenant.get()


def assert_tenant_authorized(resource_tenant_id: str) -> None:
    """断言"这份资源归属的租户"和"当前上下文租户"一致——不一致时直接抛
    `TenantAuthorizationError`。

    这是从数据源头强制隔离的关键一环：调用方在真正读取/返回一份资源之前
    必须显式调用这个断言，而不是依赖"检索时已经按租户过滤过了、理论上
    不会拿到别的租户的数据"这种脆弱的隐式假设（checklist E类真实事故：
    `scope="all"`模式完全不做租户过滤，且鉴权机制本身也不校验租户身份，
    任何持有合法凭证的调用方都能读到其他租户的全部数据）。
    """
    # 先拿到当前上下文的租户身份——如果压根没有租户上下文，这一行本身
    # 就会先抛出`NoActiveTenantError`（复用了上面的函数，不用重复写
    # 一遍"没有身份该怎么办"的逻辑）。
    current = get_current_tenant()
    if current.tenant_id != resource_tenant_id:
        # 当前租户和资源归属的租户对不上——这是一次真实的、潜在的越权
        # 访问，必须直接抛异常拒绝，把两个租户ID都写进错误信息里，方便
        # 排查问题时一眼看出"是谁想访问了不属于自己的资源"。
        raise TenantAuthorizationError(
            f"tenant '{current.tenant_id}' is not authorized to access a resource "
            f"belonging to tenant '{resource_tenant_id}'"
        )
