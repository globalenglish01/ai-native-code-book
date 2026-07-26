from __future__ import annotations

from ainative_eval.gdpr import DataSubjectRightsService, InMemoryAuditSink


class _FakeCleaner:
    def __init__(self, name: str, seed_data: dict[str, list[str]] | None = None) -> None:
        self.name = name
        self._data: dict[str, list[str]] = seed_data or {}

    def export(self, user_id: str) -> dict[str, object]:
        return {"items": list(self._data.get(user_id, []))}

    def delete(self, user_id: str) -> int:
        items = self._data.pop(user_id, [])
        return len(items)


class _FailingAuditSink:
    def record(self, entry) -> None:
        raise ConnectionError("audit backend unreachable")


def test_export_my_data_aggregates_across_all_registered_cleaners():
    service = DataSubjectRightsService(InMemoryAuditSink())
    service.register_cleaner(_FakeCleaner("conversation_history", {"user-1": ["msg-a"]}))
    service.register_cleaner(_FakeCleaner("checkpoints", {"user-1": ["cp-1", "cp-2"]}))

    export = service.export_my_data("user-1")

    assert export["conversation_history"] == {"items": ["msg-a"]}
    assert export["checkpoints"] == {"items": ["cp-1", "cp-2"]}


def test_delete_my_data_returns_per_resource_deletion_counts():
    service = DataSubjectRightsService(InMemoryAuditSink())
    service.register_cleaner(_FakeCleaner("conversation_history", {"user-1": ["msg-a", "msg-b"]}))
    service.register_cleaner(_FakeCleaner("checkpoints", {"user-1": ["cp-1"]}))

    result = service.delete_my_data("user-1")

    assert result == {"conversation_history": 2, "checkpoints": 1}


def test_covered_resource_types_reflects_registered_cleaners():
    service = DataSubjectRightsService(InMemoryAuditSink())
    service.register_cleaner(_FakeCleaner("conversation_history"))
    service.register_cleaner(_FakeCleaner("checkpoints"))

    assert service.covered_resource_types == ["conversation_history", "checkpoints"]


def test_export_writes_audit_record_with_correct_gdpr_article():
    audit_sink = InMemoryAuditSink()
    service = DataSubjectRightsService(audit_sink)
    service.register_cleaner(_FakeCleaner("conversation_history"))

    service.export_my_data("user-1")

    records = audit_sink.for_user("user-1")
    assert len(records) == 1
    assert records[0].action == "export"
    assert "Art.20" in records[0].regulation_article


def test_delete_writes_audit_record_with_correct_gdpr_article():
    audit_sink = InMemoryAuditSink()
    service = DataSubjectRightsService(audit_sink)
    service.register_cleaner(_FakeCleaner("conversation_history"))

    service.delete_my_data("user-1")

    records = audit_sink.for_user("user-1")
    assert len(records) == 1
    assert records[0].action == "delete"
    assert "Art.17" in records[0].regulation_article


def test_export_and_delete_produce_the_same_audit_schema():
    """The consistency guarantee this module exists for: both interfaces
    must get equally strong audit trails, not just the one that was
    implemented first."""
    audit_sink = InMemoryAuditSink()
    service = DataSubjectRightsService(audit_sink)
    service.register_cleaner(_FakeCleaner("conversation_history"))

    service.export_my_data("user-1")
    service.delete_my_data("user-1")

    records = audit_sink.for_user("user-1")
    assert len(records) == 2
    assert {r.action for r in records} == {"export", "delete"}
    assert all(r.resource_types == ["conversation_history"] for r in records)


def test_audit_write_failure_does_not_block_export():
    """The explicit tradeoff this module encodes: audit failures must not
    prevent the user from exercising their data subject rights."""
    service = DataSubjectRightsService(_FailingAuditSink())
    service.register_cleaner(_FakeCleaner("conversation_history", {"user-1": ["msg-a"]}))

    export = service.export_my_data("user-1")  # must not raise

    assert export["conversation_history"] == {"items": ["msg-a"]}


def test_audit_write_failure_does_not_block_delete():
    service = DataSubjectRightsService(_FailingAuditSink())
    service.register_cleaner(_FakeCleaner("conversation_history", {"user-1": ["msg-a"]}))

    result = service.delete_my_data("user-1")  # must not raise

    assert result == {"conversation_history": 1}
