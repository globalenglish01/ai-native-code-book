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

# 让类型注解可以延迟解析，详见context.py里的详细解释，这里不再重复。
from __future__ import annotations

# dataclass/field——`@dataclass`自动生成`__init__`等样板代码；
# `field(default_factory=...)`专门用于"默认值是可变对象（比如列表/
# 字典），需要每个实例各自独立创建一份"的场景。原因：如果直接写
# `extra_filters: dict = {}`，Python会让**所有**实例共享同一个字典对象
# 作为默认值（这是Python著名的"可变默认参数"陷阱），一个实例改了这个
# 字典，其他没显式传参的实例会看到同样的改动。`field(default_factory=dict)`
# 则是告诉dataclass："每次构造一个新实例、且调用方没有传这个参数时，
# 都调用一次`dict()`现造一个全新的空字典"，各实例之间互不影响。
from dataclasses import dataclass, field

# Any——表示"任意类型都可以"，用在`extra_filters: dict[str, Any]`上，
# 因为额外的过滤条件到底是字符串、数字还是别的类型，本模块不做限定，
# 完全由调用方根据自己的存储后端需要决定。
from typing import Any


# 自定义异常类：继承Python内置的`ValueError`（表示"传入的值不合法"这
# 一大类），语义上比继承`RuntimeError`更贴切——"tenant_id为空"本质上是
# 一个"构造参数值不合法"的问题。
class MissingTenantScopeError(ValueError):
    """构造`ScopedQuery`时`tenant_id`为空/None时抛出——没有"不限定租户"
    这个合法选项，如果真的需要跨租户的系统级查询，应该走专门的、需要
    额外授权检查的管理员接口，而不是把"忘记传tenant_id"和"有意跨租户
    查询"这两种完全不同的情况混为一谈。"""


# @dataclass(frozen=True)——自动生成构造函数，并且"冻结"这个类的实例，
# 一旦创建出来字段就不能再被修改（比如`query.tenant_id = "other"`会
# 直接报错）。一个查询对象一旦确定了归属的租户，不应该在传递过程中被
# 悄悄改到别的租户，冻结能在结构上杜绝这类风险。
@dataclass(frozen=True)
class ScopedQuery:
    """一个必须携带租户限定条件的查询描述。"""

    # 必填字段（没有默认值），意味着构造`ScopedQuery(...)`时必须显式
    # 传入`tenant_id`，不存在"不小心漏传就悄悄变成不限定租户"这种情况
    # ——Python的dataclass在没有默认值的字段上会强制要求调用方传值，
    # 否则构造时直接报错。
    tenant_id: str

    # `field(default_factory=dict)`——见文件顶部import处的详细解释：
    # 默认值是"每次构造一个新的空字典"，不是所有实例共享同一个字典。
    extra_filters: dict[str, Any] = field(default_factory=dict)
    """除租户限定之外的其他过滤条件（如`document_type`），会和租户限定条件
    合并进`build_filter()`的结果，但不能覆盖`tenant_id`键本身。"""

    def __post_init__(self) -> None:
        # `__post_init__`是dataclass专门支持的一个"钩子方法"：dataclass
        # 自动生成的`__init__`（构造函数）在把所有字段都赋值完毕之后，
        # 会自动调用这个方法——常用来做"构造函数参数的合法性校验"，
        # 因为这时候`self.tenant_id`等字段已经被正常赋值好了，可以直接
        # 拿来检查。
        if not self.tenant_id:
            # `not self.tenant_id`——对空字符串`""`和`None`都会成立
            # （两者在Python里都是"假值"），所以这一行同时拦住了
            # "传了空字符串"和"传了None"这两种非法输入，测试文件里
            # `test_scoped_query_requires_non_empty_tenant_id`和
            # `test_scoped_query_rejects_none_tenant_id`两个用例分别
            # 验证的就是这两种情况。
            raise MissingTenantScopeError(
                "ScopedQuery requires a non-empty tenant_id — there is no valid "
                "'unscoped' query; use a dedicated admin/system query path instead"
            )

    def build_filter(self, *, tenant_field: str = "tenant_id") -> dict[str, Any]:
        """返回一份必须被传给底层存储查询本身（而不是查询之后再筛选结果）
        的过滤条件dict——`tenant_field`键始终存在且值为`self.tenant_id`，
        `extra_filters`里即使意外包含同名键也会被`tenant_id`覆盖，保证
        租户限定条件永远生效、不会被其他过滤条件意外顶掉。"""
        # `{**self.extra_filters, tenant_field: self.tenant_id}`——字典的
        # "展开合并"写法：先把`extra_filters`里的所有键值对原样铺开，
        # 再额外放进（或覆盖）一个`tenant_field`键、值是`self.tenant_id`。
        # 关键点在于**书写顺序**：在Python里，字典字面量中如果同一个key
        # 出现了两次，后写的那次会覆盖先写的那次——这里`tenant_field`
        # 写在`**self.extra_filters`**之后**，所以哪怕`extra_filters`
        # 里本身就带了一个同名的`tenant_id`键（比如调用方不小心/恶意
        # 传了`extra_filters={"tenant_id": "别的租户"}`），最终结果里
        # 这个键的值也一定会被`self.tenant_id`覆盖，不会被意外顶掉。
        return {**self.extra_filters, tenant_field: self.tenant_id}


def assert_result_belongs_to_tenant(result_tenant_id: str, query: ScopedQuery) -> None:
    """查询返回结果之后的最后一道防线：断言拿到的每一条结果确实属于
    `query.tenant_id`。这不能替代`build_filter()`在查询源头就过滤（事后
    过滤本身正是checklist记录的失效模式之一），只用来在真正返回给调用方
    之前，对"存储层过滤逻辑本身是否正确生效"做一次独立复核。
    """
    if result_tenant_id != query.tenant_id:
        # 结果归属的租户和查询本身限定的租户对不上——说明底层存储的
        # 过滤逻辑根本没有正确生效（否则压根不应该返回这条结果），
        # 这是一次严重的隔离失效，必须直接抛异常，把两个租户ID都写进
        # 错误信息里方便排查，而不是悄悄放过这条数据。
        raise MissingTenantScopeError(
            f"query scoped to tenant '{query.tenant_id}' returned a result belonging "
            f"to tenant '{result_tenant_id}' — the underlying store's filter did not "
            f"actually apply the tenant scope; treat this as a critical isolation failure"
        )
