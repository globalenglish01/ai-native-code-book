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

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass


@dataclass(frozen=True)
class TenantIdentity:
    """当前请求归属的租户身份——是"数据隔离"判断的唯一依据。"""

    tenant_id: str
    """租户的唯一标识——真实项目里必须对应到一个真正独立计费/独立数据边界的
    实体，而不是仅仅"看起来像租户"的字段（checklist E类真实事故：`tenant_id`
    语义和真实组织概念脱节，这个字段必须严肃对待其语义边界）。"""


class TenantAuthorizationError(RuntimeError):
    """`assert_tenant_authorized`发现资源归属的租户和当前上下文租户不一致时抛出。"""


class NoActiveTenantError(RuntimeError):
    """在`tenant_scope`作用域之外调用`get_current_tenant()`时抛出——避免
    "忘记进入租户作用域却仍然执行了本该受隔离保护的操作"这类疏漏被静默放行。"""


_current_tenant: ContextVar[TenantIdentity | None] = ContextVar("ainative_tenancy_current_tenant", default=None)


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
    identity = TenantIdentity(tenant_id=tenant_id)
    token: Token = _current_tenant.set(identity)
    try:
        yield identity
    finally:
        _current_tenant.reset(token)


def get_current_tenant() -> TenantIdentity:
    """获取当前上下文的租户身份；不在任何`tenant_scope`作用域内时抛出
    `NoActiveTenantError`，而不是返回`None`让调用方自己记得判断——这是刻意
    的设计：任何需要租户身份的代码路径，如果在没有租户上下文的情况下被
    调用，本身就是一个应该在开发阶段就暴露出来的编程错误。"""
    identity = _current_tenant.get()
    if identity is None:
        raise NoActiveTenantError(
            "no active tenant_scope() — this code path requires a tenant identity to be set first"
        )
    return identity


def try_get_current_tenant() -> TenantIdentity | None:
    """`get_current_tenant()`的非抛异常版本——仅用于"这段代码确实可能在
    系统级/无租户上下文里运行，需要区分对待"的场景，不应该被当作绕开
    `assert_tenant_authorized`强制检查的手段。"""
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
    current = get_current_tenant()
    if current.tenant_id != resource_tenant_id:
        raise TenantAuthorizationError(
            f"tenant '{current.tenant_id}' is not authorized to access a resource "
            f"belonging to tenant '{resource_tenant_id}'"
        )
