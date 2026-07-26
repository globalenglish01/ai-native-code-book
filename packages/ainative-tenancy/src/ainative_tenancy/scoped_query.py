"""强制检索/查询携带租户限定条件——从数据源头隔离，而不是事后过滤。

改造自checklist E类"多租户检索/查询范围隔离子类"两个真实事故：

1. `scope="all"`模式完全不做租户过滤——代码注释明确写着"None = 不过滤"，
   任何持有合法凭证的调用方都能读到其他租户的全部文档。
2. 即使`scope`看似限定了范围，实际检索依然是先对整个共享索引发起不带
   租户限制的查询，再靠事后过滤（如按`file_path`）实现隔离——隔离效果
   完全依赖过滤逻辑本身不出现任何疏漏，是比"完全未隔离"更隐蔽的失效
   模式，因为表面上看起来像是做了隔离。

设计原则：`ScopedQuery`不是"帮你把租户过滤条件拼接进查询"的便利工具，
而是一个**强制约束**——构造函数直接要求传入非空的`tenant_id`，没有任何
"不加过滤"的合法取值；`build_filter()`返回的过滤条件必须被调用方真正
用在发起查询的那一步（而不是查询之后再筛选结果），这是本模块唯一
关心的正确用法，其余交给具体的存储后端SDK。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class MissingTenantScopeError(ValueError):
    """构造`ScopedQuery`时`tenant_id`为空/None时抛出——没有"不限定租户"
    这个合法选项，如果真的需要跨租户的系统级查询，应该走专门的、需要
    额外授权检查的管理员接口，而不是把"忘记传tenant_id"和"有意跨租户
    查询"这两种完全不同的情况混为一谈。"""


@dataclass(frozen=True)
class ScopedQuery:
    """一个必须携带租户限定条件的查询描述。"""

    tenant_id: str
    extra_filters: dict[str, Any] = field(default_factory=dict)
    """除租户限定之外的其他过滤条件（如`document_type`），会和租户限定条件
    合并进`build_filter()`的结果，但不能覆盖`tenant_id`键本身。"""

    def __post_init__(self) -> None:
        if not self.tenant_id:
            raise MissingTenantScopeError(
                "ScopedQuery requires a non-empty tenant_id — there is no valid "
                "'unscoped' query; use a dedicated admin/system query path instead"
            )

    def build_filter(self, *, tenant_field: str = "tenant_id") -> dict[str, Any]:
        """返回一份必须被传给底层存储查询本身（而不是查询之后再筛选结果）
        的过滤条件dict——`tenant_field`键始终存在且值为`self.tenant_id`，
        `extra_filters`里即使意外包含同名键也会被`tenant_id`覆盖，保证
        租户限定条件永远生效、不会被其他过滤条件意外顶掉。"""
        return {**self.extra_filters, tenant_field: self.tenant_id}


def assert_result_belongs_to_tenant(result_tenant_id: str, query: ScopedQuery) -> None:
    """查询返回结果之后的最后一道防线：断言拿到的每一条结果确实属于
    `query.tenant_id`。这不能替代`build_filter()`在查询源头就过滤（事后
    过滤本身正是checklist记录的失效模式之一），只用来在真正返回给调用方
    之前，对"存储层过滤逻辑本身是否正确生效"做一次独立复核。
    """
    if result_tenant_id != query.tenant_id:
        raise MissingTenantScopeError(
            f"query scoped to tenant '{query.tenant_id}' returned a result belonging "
            f"to tenant '{result_tenant_id}' — the underlying store's filter did not "
            f"actually apply the tenant scope; treat this as a critical isolation failure"
        )
