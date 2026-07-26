from __future__ import annotations

import pytest
from ainative_tenancy.quota import QuotaExceededError, TenantResourceTracker


def test_unregistered_tenant_falls_back_to_default_quota():
    tracker = TenantResourceTracker()
    tracker.acquire_job_slot("unregistered-tenant")
    assert tracker.active_job_count("unregistered-tenant") == 1


def test_acquire_job_slot_increments_active_count():
    tracker = TenantResourceTracker()
    tracker.register_quota("tenant-a", max_concurrent_jobs=5)
    tracker.acquire_job_slot("tenant-a")
    tracker.acquire_job_slot("tenant-a")
    assert tracker.active_job_count("tenant-a") == 2


def test_acquire_job_slot_raises_when_quota_exceeded():
    tracker = TenantResourceTracker()
    tracker.register_quota("tenant-a", max_concurrent_jobs=1)
    tracker.acquire_job_slot("tenant-a")
    with pytest.raises(QuotaExceededError, match="tenant-a"):
        tracker.acquire_job_slot("tenant-a")


def test_release_job_slot_decrements_active_count():
    tracker = TenantResourceTracker()
    tracker.register_quota("tenant-a", max_concurrent_jobs=1)
    tracker.acquire_job_slot("tenant-a")
    tracker.release_job_slot("tenant-a")
    assert tracker.active_job_count("tenant-a") == 0
    tracker.acquire_job_slot("tenant-a")  # must not raise now


def test_release_job_slot_never_goes_negative():
    tracker = TenantResourceTracker()
    tracker.release_job_slot("never-acquired-tenant")
    assert tracker.active_job_count("never-acquired-tenant") == 0


def test_connection_quota_is_tracked_independently_from_job_quota():
    tracker = TenantResourceTracker()
    tracker.register_quota("tenant-a", max_concurrent_jobs=1, max_pool_connections=1)
    tracker.acquire_job_slot("tenant-a")
    tracker.acquire_connection("tenant-a")
    with pytest.raises(QuotaExceededError):
        tracker.acquire_connection("tenant-a")
    # job quota independently still at its own limit, not affected by connection quota
    assert tracker.active_job_count("tenant-a") == 1


def test_one_tenants_usage_does_not_affect_another_tenants_quota():
    """The core fairness guarantee this module exists for: a heavy tenant
    exhausting its own quota must not affect a different tenant's ability
    to acquire resources."""
    tracker = TenantResourceTracker()
    tracker.register_quota("heavy-tenant", max_concurrent_jobs=1)
    tracker.register_quota("light-tenant", max_concurrent_jobs=1)

    tracker.acquire_job_slot("heavy-tenant")
    with pytest.raises(QuotaExceededError):
        tracker.acquire_job_slot("heavy-tenant")

    tracker.acquire_job_slot("light-tenant")  # must not raise
    assert tracker.active_job_count("light-tenant") == 1


def test_release_connection_decrements_active_connection_count():
    tracker = TenantResourceTracker()
    tracker.register_quota("tenant-a", max_pool_connections=2)
    tracker.acquire_connection("tenant-a")
    tracker.acquire_connection("tenant-a")
    tracker.release_connection("tenant-a")
    assert tracker.active_connection_count("tenant-a") == 1


def test_usage_report_reflects_active_tenants_only():
    tracker = TenantResourceTracker()
    tracker.register_quota("tenant-a")
    tracker.acquire_job_slot("tenant-a")

    report = tracker.usage_report()

    assert report == {"tenant-a": {"active_jobs": 1, "active_connections": 0}}
