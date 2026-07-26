"""GDPR数据主体权利（Data Subject Rights）实现骨架——导出/删除+审计留痕。

改造自checklist J类"GDPR数据主体权利实现子类"真实设计要点：

1. 数据主体权利接口必须明确对应到具体法规条款（导出→GDPR Art.20数据
   可携权，删除→GDPR Art.17被遗忘权），不能只在隐私政策文档里做承诺。
2. "删除用户数据"必须系统性梳理该用户数据分散存储在哪些不同表/存储系统
   （包括容易被忽视的关联数据，如对话历史检查点、缓存），逐一清理——
   `DataSubjectRightsService`要求调用方注册`ResourceCleaner`列表而不是
   一次性写一个大函数，方便审查时能一眼看出"有没有漏掉某类数据"。
3. 功能相近的多个接口（导出/删除）必须获得同等合规保护级别——真实项目
   曾出现过删除接口从一开始就有审计留痕、导出接口最初完全没有的不一致，
   本模块把审计留痕做成两个接口共享的统一步骤，不给"忘记补上另一边"
   留下空子。
4. 审计留痕写入失败时，是否阻塞用户核心操作是一个需要显式权衡的问题——
   本模块默认"审计失败不阻塞核心操作"（宁可数据已经导出/删除、审计记录
   有缺口，也不能让审计系统故障连累用户本该享有的合规权利无法行使），
   但用可见的日志级别记录这类失败。
"""

from __future__ import annotations

# logging是Python标准库自带的"日志"模块——比起直接用print()打印信息，
# logging可以区分"这条信息有多重要"（debug/info/warning/error等级别），
# 方便部署到生产环境后，只显示/保存真正需要关注的错误信息，过滤掉
# 无关紧要的调试细节。
import logging

# time模块见ainative_core/usage_tracking.py里的详细解释：`time.time()`
# 返回"从1970年1月1日到现在过了多少秒"这样一个数字，是记录"这件事发生
# 在什么时刻"的常见方式。
import time

# dataclass/field见ainative_core/protocols.py里的详细解释：dataclass
# 自动生成构造函数等样板代码；field(default_factory=...)专门用于"默认值
# 是需要每次重新生成的东西（比如当前时间、空列表）"这种场景，避免所有
# 实例共享同一个默认值对象。
from dataclasses import dataclass, field

# Any——任意类型；Literal——只能是列出的几个固定值之一的类型；
# Protocol/runtime_checkable——定义"接口约定"而非具体实现，见
# ainative_core/protocols.py里对这两个概念的详细解释。
from typing import Any, Literal, Protocol, runtime_checkable

# `logging.getLogger(__name__)`：创建（或者说"取得"）一个日志记录器
# 实例，`__name__`是Python自动帮每个模块文件填好的一个特殊变量，值
# 是这个模块的完整路径名（比如"ainative_eval.gdpr"）——用模块名给
# logger命名是Python的标准约定，这样当日志信息被打印出来时，能一眼
# 看出"这条日志是从哪个文件产生的"，方便排查问题时定位源头。
logger = logging.getLogger(__name__)

# DSRAction是一个"类型别名"（给下面这个Literal类型起一个简短的名字）——
# 数据主体权利（Data Subject Rights，缩写DSR）目前只支持"导出"和
# "删除"这两种操作，用Literal把取值范围明确限定成这两个字符串，别处
# 写`DSRAction`就等同于写`Literal["export", "delete"]`，既省字又统一。
DSRAction = Literal["export", "delete"]

# `_GDPR_ARTICLE`：前缀下划线表示"内部使用的常量，不打算被外部代码
# 直接引用"（命名约定，见ainative_core/memory_backends.py里的解释）。
# 这是一个字典，把"导出"/"删除"这两个动作，分别映射到它们各自对应的
# 具体GDPR法规条款文字说明——`dict[DSRAction, str]`这个类型注解表示
# "这个字典的key只能是DSRAction这个类型允许的值（也就是'export'或
# 'delete'），value是普通字符串"。把法规条款和动作类型的对应关系
# 写成数据（字典），而不是分散在代码各处的字符串字面量，是为了保证
# "导出永远对应Art.20、删除永远对应Art.17"这件事只需要在一个地方
# 维护，不会因为在不同地方各写一遍而产生不一致。
_GDPR_ARTICLE: dict[DSRAction, str] = {"export": "Art.20 (Right to Data Portability)", "delete": "Art.17 (Right to Erasure)"}


@runtime_checkable
class ResourceCleaner(Protocol):
    """"某一类数据该怎么导出/删除"这件事的抽象接口——每一类分散存储的
    用户数据（对话历史、检查点、缓存……）各自实现一个，而不是在一个
    大函数里把所有存储系统的清理逻辑堆在一起（那样容易遗漏、也难以
    在代码审查时一眼看出覆盖是否完整）。
    """

    # 注意：这一行`name: str`出现在Protocol类里，表示"任何想满足这份
    # 协议约定的类，都必须有一个叫name的字符串属性"——和下面两个方法
    # 一样，这里只声明"必须具备什么"，不提供具体实现。
    name: str
    """这类数据的简短标识（如"conversation_history"/"checkpoints"），
    出现在审计记录和覆盖率报告里。"""

    def export(self, user_id: str) -> dict[str, Any]:
        """导出这个用户在这类数据里的全部记录。"""
        # `...`（Ellipsis）：Protocol里方法体的占位符，表示"具体怎么
        # 实现，由真正实现这份协议的类决定"，见ainative_core/protocols.py
        # 里的详细解释。
        ...

    def delete(self, user_id: str) -> int:
        """删除这个用户在这类数据里的全部记录，返回删除的记录数。"""
        ...


# 注意：这里是普通的`@dataclass`，没有加`frozen=True`——和
# ainative_core/protocols.py里`GateResult`选择不冻结的原因类似，
# 但这里的具体考量是：AuditRecord的字段在构造时就已经确定齐全
# （不像GateResult那样后续可能需要调整score），选择不冻结更多是
# 沿用"审计记录类通常允许调用方按需要补充调整"的宽松处理方式，而非
# 有强烈的必须可变的理由。
@dataclass
class AuditRecord:
    """一次数据主体权利操作的审计留痕——导出和删除共用同一份schema，
    避免"两个接口各自维护一套审计记录格式，后来发现不一致"的真实教训。"""

    action: DSRAction
    user_id: str
    regulation_article: str
    resource_types: list[str]
    # `field(default_factory=time.time)`：默认值是"调用一次time.time()
    # 函数得到的返回值"——注意这里传的是`time.time`这个函数本身（没有
    # 加括号），而不是`time.time()`（调用后的结果）。`default_factory`
    # 要求的正是"一个函数"，它会在每次创建新实例时自动调用一次，取得
    # "创建这条记录那一刻"的时间戳，而不是所有实例共享同一个固定时间。
    timestamp: float = field(default_factory=time.time)


class AuditSink(Protocol):
    """审计记录该被持久化到哪里——由调用方决定，本模块只负责生成记录。"""

    # 注意这个方法体写成了`...`跟在同一行（`def record(...) -> None: ...`），
    # 和上面ResourceCleaner里换行写`...`是完全等价的写法，只是更紧凑——
    # Protocol里的占位方法体，写在同一行还是换行纯粹是风格选择。
    def record(self, entry: AuditRecord) -> None: ...


class InMemoryAuditSink:
    """`AuditSink`的内存版默认实现——供demo/测试使用。"""

    def __init__(self) -> None:
        # 初始化一个空列表，用来存放所有已经记录进来的审计记录。
        # 类型注解`list[AuditRecord]`表示"列表里每一项都是AuditRecord
        # 实例"。
        self._records: list[AuditRecord] = []

    def record(self, entry: AuditRecord) -> None:
        # `.append(x)`：Python列表内置方法，把x加到列表末尾。这里直接
        # 存了`entry`本身（没有像ainative_core/memory_backends.py里的
        # InMemoryUsageSink那样做deepcopy深拷贝）——因为AuditRecord
        # 记录的场景下，调用方通常不会拿着同一个entry对象反复修改后
        # 再次调用record，深拷贝防御在这里不是必需的取舍。
        self._records.append(entry)

    def all(self) -> list[AuditRecord]:
        # `list(self._records)`：用list()包一层，构造出一个新的列表
        # 对象，但列表里的元素（AuditRecord实例）还是同一批对象——
        # 这叫"浅拷贝"（见ainative_core/memory_backends.py里对深拷贝/
        # 浅拷贝区别的详细解释）。这样做的好处是：调用方如果对返回的
        # 列表本身做增删（比如误删了一项），不会影响到`self._records`
        # 内部真正存储的列表，但如果调用方修改列表里某一条记录内部的
        # 字段，仍然会影响到内部数据（因为AuditRecord不是frozen的）。
        return list(self._records)

    def for_user(self, user_id: str) -> list[AuditRecord]:
        # 这是一个"列表推导式"（见fairness.py里的详细解释），意思是
        # "遍历`self._records`里的每一条记录r，只保留r.user_id和传入的
        # user_id完全一致的那些，组成一个新列表"——这就是"按用户过滤"
        # 的实现方式。
        return [r for r in self._records if r.user_id == user_id]


class DataSubjectRightsService:
    """数据主体权利接口的通用骨架——导出/删除共用同一套"遍历已注册
    ResourceCleaner + 写审计留痕"流程，保证两个接口获得同等合规保护级别。
    """

    def __init__(self, audit_sink: AuditSink) -> None:
        # 保存传入的审计记录目标（具体实现可以是内存版，也可以是真实
        # 项目自己写的、写进数据库的版本，本类不关心具体实现）。
        self._audit_sink = audit_sink
        # 准备一个空列表，用来存放之后陆续注册进来的各类数据清理器。
        self._cleaners: list[ResourceCleaner] = []

    def register_cleaner(self, cleaner: ResourceCleaner) -> None:
        """登记一类数据的清理器——真实项目里每新增一处存储用户数据的地方，
        都应该在这里补一个对应的`ResourceCleaner`，而不是散落在别处
        自行处理、容易被这个统一入口漏掉。"""
        self._cleaners.append(cleaner)

    # @property见ainative_core/memory_backends.py里的详细解释：让下面
    # 这个方法可以"像访问属性一样被调用"（写`service.covered_resource_types`
    # 不用加括号），适合"包装/计算一下但概念上是只读属性"的场景。
    @property
    def covered_resource_types(self) -> list[str]:
        """当前已注册覆盖的数据类型列表——供合规审查时快速核实"是否所有
        分散存储用户数据的地方都有对应的清理器"，而不用逐个翻代码。"""
        # 列表推导式：把已注册的每一个清理器的`name`属性取出来，组成
        # 一个新的字符串列表返回。
        return [c.name for c in self._cleaners]

    def export_my_data(self, user_id: str) -> dict[str, Any]:
        """GDPR Art.20数据可携权——导出该用户在所有已注册数据类型里的记录。"""
        # `{cleaner.name: cleaner.export(user_id) for cleaner in self._cleaners}`：
        # 字典推导式（见fairness.py里的详细解释），遍历所有已注册的
        # 清理器，对每一个都调用它的`export(user_id)`方法拿到这类数据
        # 的导出结果，用清理器的`name`当作key，组成一个"每类数据各自的
        # 导出内容"字典。
        export = {cleaner.name: cleaner.export(user_id) for cleaner in self._cleaners}
        # 完成导出之后，无论如何都要写一条审计记录——见下面
        # `_write_audit_record`的详细解释，这一步是导出/删除两个接口
        # 共享的统一步骤。
        self._write_audit_record("export", user_id)
        return export

    def delete_my_data(self, user_id: str) -> dict[str, int]:
        """GDPR Art.17被遗忘权——删除该用户在所有已注册数据类型里的记录，
        返回每类数据各自删除的记录数，供调用方核实删除确实覆盖了全部
        已知的存储位置。"""
        # 和导出同理，遍历所有清理器，分别调用它们的`delete(user_id)`
        # 方法（返回值是"删除了多少条记录"这个整数），组成一个字典。
        deleted_counts = {cleaner.name: cleaner.delete(user_id) for cleaner in self._cleaners}
        self._write_audit_record("delete", user_id)
        return deleted_counts

    def _write_audit_record(self, action: DSRAction, user_id: str) -> None:
        # 先把这次操作的完整审计信息组装成一个AuditRecord对象——
        # `regulation_article=_GDPR_ARTICLE[action]`表示"根据这次是
        # 导出还是删除，从前面定义的映射字典里取出对应的具体法规条款
        # 文字"；`resource_types=self.covered_resource_types`记录下
        # "这次操作实际覆盖了哪些数据类型"，方便日后核实审计记录时能
        # 看到当时到底注册了哪些清理器。
        entry = AuditRecord(
            action=action, user_id=user_id, regulation_article=_GDPR_ARTICLE[action],
            resource_types=self.covered_resource_types,
        )
        try:
            # 尝试把这条审计记录真正写进去（具体写到哪里、怎么写，由
            # 注入的`self._audit_sink`决定，这里不关心）。
            self._audit_sink.record(entry)
        except Exception as exc:  # noqa: BLE001
            # `except Exception as exc:`：捕获几乎所有类型的异常（见
            # gate.py里对`Exception`这个宽泛捕获的详细解释）。这里故意
            # 捕获得这么宽泛，是因为本模块docstring里第4条设计原则
            # 明确要求"审计留痕写入失败，不应该阻塞用户本该享有的
            # 合规操作（导出/删除数据）"——不管审计系统具体因为什么
            # 原因失败（网络问题、数据库故障、代码bug……），都不能让
            # 这个失败连累到用户核心操作，所以统一捕获，不深究具体异常
            # 类型。下一行代码末尾的noqa注释是给代码检查工具ruff的
            # 指令，告诉它"这一行故意宽泛捕获异常，不要报警"（BLE001
            # 是ruff内建的"过于宽泛的except"规则编号）。
            # 审计留痕失败不阻塞用户本该享有的合规权利——但必须用足够可见
            # 的日志级别（ERROR而非debug）记录，不能让这类失败无声无息发生。
            logger.error(
                "[GDPR] failed to write audit record for %s action on user %s: %s", action, user_id, exc,
            )
