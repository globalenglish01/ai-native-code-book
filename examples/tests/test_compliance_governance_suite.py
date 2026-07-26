from __future__ import annotations

from products.compliance_governance_suite import ComplianceGovernanceSuite


def test_fairness_result_uses_weakest_language_not_average():
    suite = ComplianceGovernanceSuite()
    scores = {"japanese": 0.95, "chinese": 0.92, "english": 0.50}

    result = suite.evaluate_multilingual_fairness(scores)

    assert result.parity_min == 0.50
    assert result.weakest_dimension == "english"


def test_is_ready_to_launch_false_when_a_language_lags():
    suite = ComplianceGovernanceSuite()
    scores = {"japanese": 0.95, "chinese": 0.92, "english": 0.50}

    assert suite.is_ready_to_launch(scores) is False


def test_is_ready_to_launch_true_when_all_languages_are_strong():
    suite = ComplianceGovernanceSuite()
    scores = {"japanese": 0.93, "chinese": 0.91, "english": 0.90}

    assert suite.is_ready_to_launch(scores) is True


def test_export_my_data_aggregates_conversation_history_and_usage_stats():
    suite = ComplianceGovernanceSuite()
    suite.conversation_cleaner.add("user-1", "hello")
    suite.usage_cleaner.record("user-1", tokens_used=100)

    export = suite.dsr_service.export_my_data("user-1")

    assert export["conversation_history"] == {"messages": ["hello"]}
    assert export["usage_stats"] == {"total_tokens": 100, "call_count": 1}


def test_delete_my_data_removes_data_from_all_registered_sources():
    suite = ComplianceGovernanceSuite()
    suite.conversation_cleaner.add("user-1", "hello")
    suite.usage_cleaner.record("user-1", tokens_used=100)

    suite.dsr_service.delete_my_data("user-1")
    export_after_delete = suite.dsr_service.export_my_data("user-1")

    assert export_after_delete["conversation_history"] == {"messages": []}
    assert export_after_delete["usage_stats"] == {}


def test_export_and_delete_both_produce_audit_records():
    suite = ComplianceGovernanceSuite()
    suite.conversation_cleaner.add("user-1", "hello")

    suite.dsr_service.export_my_data("user-1")
    suite.dsr_service.delete_my_data("user-1")

    records = suite.audit_sink.for_user("user-1")
    assert {r.action for r in records} == {"export", "delete"}


def test_covered_resource_types_includes_both_cleaners():
    suite = ComplianceGovernanceSuite()
    assert suite.dsr_service.covered_resource_types == ["conversation_history", "usage_stats"]
