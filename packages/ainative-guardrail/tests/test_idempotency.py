from __future__ import annotations

import pytest
from ainative_guardrail.idempotency import (
    DuplicateOperationError,
    IdempotencyStatus,
    InMemoryIdempotencyStore,
    idempotent_operation,
)


def test_first_occupy_succeeds():
    store = InMemoryIdempotencyStore()
    assert store.occupy("key-1", ttl_seconds=3600) is None
    record = store.get("key-1")
    assert record.status is IdempotencyStatus.IN_PROGRESS


def test_second_occupy_while_in_progress_returns_existing_record():
    store = InMemoryIdempotencyStore()
    store.occupy("key-1", ttl_seconds=3600)
    existing = store.occupy("key-1", ttl_seconds=3600)
    assert existing is not None
    assert existing.status is IdempotencyStatus.IN_PROGRESS


def test_complete_updates_status_and_stores_result():
    store = InMemoryIdempotencyStore()
    store.occupy("key-1", ttl_seconds=3600)
    store.complete("key-1", {"id": "abc"}, ttl_seconds=3600)

    record = store.get("key-1")
    assert record.status is IdempotencyStatus.COMPLETED
    assert record.result == {"id": "abc"}


def test_release_removes_the_key_entirely():
    store = InMemoryIdempotencyStore()
    store.occupy("key-1", ttl_seconds=3600)
    store.release("key-1")
    assert store.get("key-1") is None


def test_expired_key_is_treated_as_absent_and_can_be_reoccupied():
    store = InMemoryIdempotencyStore()
    store.occupy("key-1", ttl_seconds=-1)  # already expired
    assert store.get("key-1") is None
    assert store.occupy("key-1", ttl_seconds=3600) is None  # succeeds, not blocked by stale record


def test_idempotent_operation_raises_duplicate_error_for_second_caller():
    store = InMemoryIdempotencyStore()
    with idempotent_operation(store, "op-1"):
        pass  # first caller occupies and finishes the with-block (but hasn't called complete())

    # key is still IN_PROGRESS because complete() was never called — simulates
    # a genuinely concurrent second caller arriving while the first is still "in flight"
    with pytest.raises(DuplicateOperationError) as exc_info, idempotent_operation(store, "op-1"):
        pass
    assert exc_info.value.record.status is IdempotencyStatus.IN_PROGRESS


def test_completed_operation_is_reported_via_duplicate_error_with_cached_result():
    """The core reuse guarantee: a retry after successful completion must be
    told about the cached result, not treated identically to an in-progress
    duplicate."""
    store = InMemoryIdempotencyStore()
    with idempotent_operation(store, "op-1"):
        pass
    store.complete("op-1", {"charge_id": "ch_123"}, ttl_seconds=3600)

    with pytest.raises(DuplicateOperationError) as exc_info, idempotent_operation(store, "op-1"):
        pass

    assert exc_info.value.record.status is IdempotencyStatus.COMPLETED
    assert exc_info.value.record.result == {"charge_id": "ch_123"}


def test_key_is_released_when_operation_body_raises():
    """The real-world bug this guards against: a mid-operation failure must
    not leave the idempotency key stuck IN_PROGRESS for the full TTL window
    — the user should be able to retry immediately once the underlying
    issue is fixed."""
    store = InMemoryIdempotencyStore()

    with pytest.raises(ConnectionError), idempotent_operation(store, "op-1"):
        raise ConnectionError("downstream unavailable")

    assert store.get("op-1") is None  # released, not stuck IN_PROGRESS

    # retry succeeds without hitting DuplicateOperationError
    with idempotent_operation(store, "op-1"):
        pass


def test_duplicate_operation_error_message_includes_key_and_status():
    store = InMemoryIdempotencyStore()
    store.occupy("charge:order-42", ttl_seconds=3600)
    existing = store.occupy("charge:order-42", ttl_seconds=3600)
    error = DuplicateOperationError("charge:order-42", existing)
    assert "charge:order-42" in str(error)
    assert "in_progress" in str(error)
