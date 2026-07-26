from __future__ import annotations

import pytest
from ainative_tenancy.context import (
    NoActiveTenantError,
    TenantAuthorizationError,
    assert_tenant_authorized,
    get_current_tenant,
    tenant_scope,
    try_get_current_tenant,
)


def test_get_current_tenant_returns_identity_inside_scope():
    with tenant_scope("tenant-a"):
        assert get_current_tenant().tenant_id == "tenant-a"


def test_get_current_tenant_raises_outside_any_scope():
    with pytest.raises(NoActiveTenantError):
        get_current_tenant()


def test_try_get_current_tenant_returns_none_outside_scope():
    assert try_get_current_tenant() is None


def test_scope_is_restored_after_exiting_with_block():
    with tenant_scope("outer"):
        with tenant_scope("inner"):
            assert get_current_tenant().tenant_id == "inner"
        assert get_current_tenant().tenant_id == "outer"
    assert try_get_current_tenant() is None


def test_assert_tenant_authorized_passes_for_matching_tenant():
    with tenant_scope("tenant-a"):
        assert_tenant_authorized("tenant-a")  # must not raise


def test_assert_tenant_authorized_raises_for_mismatched_tenant():
    with tenant_scope("tenant-a"), pytest.raises(TenantAuthorizationError, match="tenant-b"):
        assert_tenant_authorized("tenant-b")


def test_assert_tenant_authorized_raises_when_no_scope_active():
    with pytest.raises(NoActiveTenantError):
        assert_tenant_authorized("tenant-a")


def test_scope_restores_correctly_even_if_body_raises():
    with tenant_scope("outer"):
        with pytest.raises(ValueError, match="boom"), tenant_scope("inner"):
            raise ValueError("boom")
        assert get_current_tenant().tenant_id == "outer"


@pytest.mark.asyncio
async def test_background_task_created_inside_scope_keeps_tenant_after_parent_scope_exits():
    """Documents the contextvars copy-on-create contract: a task spawned
    inside tenant_scope() retains a SNAPSHOT of the tenant identity taken
    at creation time, and keeps seeing it for its entire lifetime even
    after the parent scope has already exited and reset its contextvar.
    This is standard asyncio/contextvars behavior, not a bug — but any
    background task whose lifetime can outlive its parent request must
    re-enter tenant_scope() itself rather than relying on this snapshot."""
    import asyncio

    captured: list[str] = []

    async def background_task() -> None:
        await asyncio.sleep(0.01)
        captured.append(get_current_tenant().tenant_id)

    with tenant_scope("tenant-a"):
        task = asyncio.create_task(background_task())
        await asyncio.sleep(0)  # let the task start while still inside the scope

    await task  # parent scope has already exited by now

    assert captured == ["tenant-a"]
