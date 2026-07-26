"""ainative-tenancy —— 租户身份传播、资源配额隔离、检索作用域断言。"""

# 让类型注解可以延迟解析，详见context.py里的详细解释，这里不再重复。
from __future__ import annotations

# 从包内的各个模块文件里，把真正对外公开的类/函数/异常，统一导入到
# 这个`__init__.py`（包的"入口文件"）里——这样使用这个包的人可以直接
# 写`from ainative_tenancy import TenantIdentity`，而不需要知道
# `TenantIdentity`具体定义在`ainative_tenancy.context`这个子模块里，
# 把包的"内部文件划分方式"和"对外暴露的公开API"解耦开。
from ainative_tenancy.context import (
    NoActiveTenantError,
    TenantAuthorizationError,
    TenantIdentity,
    assert_tenant_authorized,
    get_current_tenant,
    tenant_scope,
    try_get_current_tenant,
)
from ainative_tenancy.quota import QuotaExceededError, TenantQuota, TenantResourceTracker
from ainative_tenancy.scoped_query import MissingTenantScopeError, ScopedQuery, assert_result_belongs_to_tenant

# 包的版本号——一个普通字符串常量，供打包工具/依赖管理工具（比如
# pip/uv）读取，标识"这是ainative-tenancy的第几个版本"。
__version__ = "0.1.0"

# `__all__`是Python的一个特殊模块级变量：显式声明"当别人写
# `from ainative_tenancy import *`（导入这个包里的所有东西）时，究竟
# 应该导入哪些名字"。这里列出的，就是本包真正想暴露给外部使用的公开
# API——按字母顺序排列，纯粹是为了让这份列表本身在代码审查/浏览时更
# 易读，不影响实际功能。
__all__ = [
    "MissingTenantScopeError",
    "NoActiveTenantError",
    "QuotaExceededError",
    "ScopedQuery",
    "TenantAuthorizationError",
    "TenantIdentity",
    "TenantQuota",
    "TenantResourceTracker",
    "assert_result_belongs_to_tenant",
    "assert_tenant_authorized",
    "get_current_tenant",
    "tenant_scope",
    "try_get_current_tenant",
]
