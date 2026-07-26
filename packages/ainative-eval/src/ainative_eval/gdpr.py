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

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

logger = logging.getLogger(__name__)

DSRAction = Literal["export", "delete"]

_GDPR_ARTICLE: dict[DSRAction, str] = {"export": "Art.20 (Right to Data Portability)", "delete": "Art.17 (Right to Erasure)"}


@runtime_checkable
class ResourceCleaner(Protocol):
    """"某一类数据该怎么导出/删除"这件事的抽象接口——每一类分散存储的
    用户数据（对话历史、检查点、缓存……）各自实现一个，而不是在一个
    大函数里把所有存储系统的清理逻辑堆在一起（那样容易遗漏、也难以
    在代码审查时一眼看出覆盖是否完整）。
    """

    name: str
    """这类数据的简短标识（如"conversation_history"/"checkpoints"），
    出现在审计记录和覆盖率报告里。"""

    def export(self, user_id: str) -> dict[str, Any]:
        """导出这个用户在这类数据里的全部记录。"""
        ...

    def delete(self, user_id: str) -> int:
        """删除这个用户在这类数据里的全部记录，返回删除的记录数。"""
        ...


@dataclass
class AuditRecord:
    """一次数据主体权利操作的审计留痕——导出和删除共用同一份schema，
    避免"两个接口各自维护一套审计记录格式，后来发现不一致"的真实教训。"""

    action: DSRAction
    user_id: str
    regulation_article: str
    resource_types: list[str]
    timestamp: float = field(default_factory=time.time)


class AuditSink(Protocol):
    """审计记录该被持久化到哪里——由调用方决定，本模块只负责生成记录。"""

    def record(self, entry: AuditRecord) -> None: ...


class InMemoryAuditSink:
    """`AuditSink`的内存版默认实现——供demo/测试使用。"""

    def __init__(self) -> None:
        self._records: list[AuditRecord] = []

    def record(self, entry: AuditRecord) -> None:
        self._records.append(entry)

    def all(self) -> list[AuditRecord]:
        return list(self._records)

    def for_user(self, user_id: str) -> list[AuditRecord]:
        return [r for r in self._records if r.user_id == user_id]


class DataSubjectRightsService:
    """数据主体权利接口的通用骨架——导出/删除共用同一套"遍历已注册
    ResourceCleaner + 写审计留痕"流程，保证两个接口获得同等合规保护级别。
    """

    def __init__(self, audit_sink: AuditSink) -> None:
        self._audit_sink = audit_sink
        self._cleaners: list[ResourceCleaner] = []

    def register_cleaner(self, cleaner: ResourceCleaner) -> None:
        """登记一类数据的清理器——真实项目里每新增一处存储用户数据的地方，
        都应该在这里补一个对应的`ResourceCleaner`，而不是散落在别处
        自行处理、容易被这个统一入口漏掉。"""
        self._cleaners.append(cleaner)

    @property
    def covered_resource_types(self) -> list[str]:
        """当前已注册覆盖的数据类型列表——供合规审查时快速核实"是否所有
        分散存储用户数据的地方都有对应的清理器"，而不用逐个翻代码。"""
        return [c.name for c in self._cleaners]

    def export_my_data(self, user_id: str) -> dict[str, Any]:
        """GDPR Art.20数据可携权——导出该用户在所有已注册数据类型里的记录。"""
        export = {cleaner.name: cleaner.export(user_id) for cleaner in self._cleaners}
        self._write_audit_record("export", user_id)
        return export

    def delete_my_data(self, user_id: str) -> dict[str, int]:
        """GDPR Art.17被遗忘权——删除该用户在所有已注册数据类型里的记录，
        返回每类数据各自删除的记录数，供调用方核实删除确实覆盖了全部
        已知的存储位置。"""
        deleted_counts = {cleaner.name: cleaner.delete(user_id) for cleaner in self._cleaners}
        self._write_audit_record("delete", user_id)
        return deleted_counts

    def _write_audit_record(self, action: DSRAction, user_id: str) -> None:
        entry = AuditRecord(
            action=action, user_id=user_id, regulation_article=_GDPR_ARTICLE[action],
            resource_types=self.covered_resource_types,
        )
        try:
            self._audit_sink.record(entry)
        except Exception as exc:  # noqa: BLE001
            # 审计留痕失败不阻塞用户本该享有的合规权利——但必须用足够可见
            # 的日志级别（ERROR而非debug）记录，不能让这类失败无声无息发生。
            logger.error(
                "[GDPR] failed to write audit record for %s action on user %s: %s", action, user_id, exc,
            )
