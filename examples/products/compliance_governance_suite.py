"""产品示例：AI合规与治理套件（公平性评测 + GDPR数据主体权利）。

真实产品形态：一个面向多语言用户的AI客服系统，上线前需要证明"不同语言
用户获得同等质量的核心功能体验"（而不是只测试单一语言就默认全球用户
体验一致），聚合多语言测试分数时取最弱语言而不是平均分（避免个别语言
的短板被其他语言的高分掩盖）；同时该系统必须提供真正落地的GDPR数据
主体权利接口——用户请求"导出我的数据"/"删除我的数据"时，系统要正确
覆盖所有分散存储用户数据的地方（对话历史、使用统计……），并且导出和
删除两个接口获得同等的审计留痕保护级别。

组合的包：ainative-eval（公平性聚合 + GDPR数据主体权利骨架）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ainative_eval import (
    DataSubjectRightsService,
    FairnessDimensionScore,
    InMemoryAuditSink,
    evaluate_fairness,
)


@dataclass
class ConversationHistoryCleaner:
    """对话历史数据的清理器——每一类分散存储用户数据的地方都要有一个
    这样的清理器，方便审查时一眼看出覆盖是否完整。"""

    name: str = "conversation_history"
    _data: dict[str, list[str]] = field(default_factory=dict)

    def add(self, user_id: str, message: str) -> None:
        self._data.setdefault(user_id, []).append(message)

    def export(self, user_id: str) -> dict:
        return {"messages": list(self._data.get(user_id, []))}

    def delete(self, user_id: str) -> int:
        return len(self._data.pop(user_id, []))


@dataclass
class UsageStatsCleaner:
    """用量统计数据的清理器——这类容易被忽视的关联数据（不是对话本身，
    但仍然是"这个用户"产生的数据）同样必须被GDPR删除请求覆盖。"""

    name: str = "usage_stats"
    _data: dict[str, dict] = field(default_factory=dict)

    def record(self, user_id: str, tokens_used: int) -> None:
        stats = self._data.setdefault(user_id, {"total_tokens": 0, "call_count": 0})
        stats["total_tokens"] += tokens_used
        stats["call_count"] += 1

    def export(self, user_id: str) -> dict:
        return dict(self._data.get(user_id, {}))

    def delete(self, user_id: str) -> int:
        return 1 if self._data.pop(user_id, None) is not None else 0


class ComplianceGovernanceSuite:
    """把公平性评测和GDPR数据主体权利接口组合在一起的合规治理套件。"""

    def __init__(self) -> None:
        self.conversation_cleaner = ConversationHistoryCleaner()
        self.usage_cleaner = UsageStatsCleaner()
        self.audit_sink = InMemoryAuditSink()
        self.dsr_service = DataSubjectRightsService(self.audit_sink)
        self.dsr_service.register_cleaner(self.conversation_cleaner)
        self.dsr_service.register_cleaner(self.usage_cleaner)

    def evaluate_multilingual_fairness(self, scores_by_language: dict[str, float]):
        dimension_scores = [FairnessDimensionScore(lang, score) for lang, score in scores_by_language.items()]
        return evaluate_fairness(dimension_scores)

    def is_ready_to_launch(self, scores_by_language: dict[str, float], *, min_acceptable_parity: float = 0.8) -> bool:
        """上线判定：不是看平均分好不好看，是看最弱的那个语言是否也达标——
        任何一个语言体验明显落后都不允许上线，即使整体平均分看起来很高。"""
        result = self.evaluate_multilingual_fairness(scores_by_language)
        return result.parity_min >= min_acceptable_parity


async def main() -> None:
    import sys

    if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    suite = ComplianceGovernanceSuite()

    # A launch candidate where one language (English) lags far behind the others.
    scores = {"japanese": 0.95, "chinese": 0.92, "english": 0.50}
    result = suite.evaluate_multilingual_fairness(scores)
    print(f"fairness result: parity_min={result.parity_min}, weakest_dimension={result.weakest_dimension}")
    print(f"ready to launch (min 0.8)? {suite.is_ready_to_launch(scores)}")

    # A candidate where all languages are consistently strong.
    balanced_scores = {"japanese": 0.93, "chinese": 0.91, "english": 0.90}
    balanced_result = suite.evaluate_multilingual_fairness(balanced_scores)
    print(f"\nbalanced fairness result: parity_min={balanced_result.parity_min}")
    print(f"ready to launch (min 0.8)? {suite.is_ready_to_launch(balanced_scores)}")

    # GDPR data subject rights demo.
    suite.conversation_cleaner.add("user-42", "Hi, I need help with my order")
    suite.conversation_cleaner.add("user-42", "Thanks, that resolved it")
    suite.usage_cleaner.record("user-42", tokens_used=350)

    print(f"\ncovered resource types for GDPR requests: {suite.dsr_service.covered_resource_types}")

    export = suite.dsr_service.export_my_data("user-42")
    print(f"export_my_data('user-42') -> {export}")

    deleted = suite.dsr_service.delete_my_data("user-42")
    print(f"delete_my_data('user-42') -> {deleted}")

    audit_records = suite.audit_sink.for_user("user-42")
    print(f"\naudit trail for user-42: {[(r.action, r.regulation_article) for r in audit_records]}")

    # Confirm the data is actually gone, not just reported as deleted.
    post_delete_export = suite.dsr_service.export_my_data("user-42")
    print(f"export after deletion (should be empty): {post_delete_export}")


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
