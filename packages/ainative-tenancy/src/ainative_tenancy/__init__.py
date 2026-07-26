"""AI Native Framework多租户模块——租户身份传播、资源配额隔离、检索作用域断言。"""

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
