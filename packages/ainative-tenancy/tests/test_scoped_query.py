from __future__ import annotations

import pytest
from ainative_tenancy.scoped_query import (
    MissingTenantScopeError,
    ScopedQuery,
    assert_result_belongs_to_tenant,
)


def test_scoped_query_requires_non_empty_tenant_id():
    with pytest.raises(MissingTenantScopeError):
        ScopedQuery(tenant_id="")


def test_scoped_query_rejects_none_tenant_id():
    with pytest.raises(MissingTenantScopeError):
        ScopedQuery(tenant_id=None)  # type: ignore[arg-type]


def test_build_filter_includes_tenant_id():
    query = ScopedQuery(tenant_id="tenant-a")
    assert query.build_filter() == {"tenant_id": "tenant-a"}


def test_build_filter_merges_extra_filters():
    query = ScopedQuery(tenant_id="tenant-a", extra_filters={"doc_type": "invoice"})
    assert query.build_filter() == {"doc_type": "invoice", "tenant_id": "tenant-a"}


def test_build_filter_tenant_id_cannot_be_overridden_by_extra_filters():
    """The real-world bug class this guards against: a scope="all" style
    caller might pass tenant_id inside extra_filters trying to override it —
    the actual scoped tenant_id must always win."""
    query = ScopedQuery(tenant_id="tenant-a", extra_filters={"tenant_id": "tenant-b"})
    assert query.build_filter()["tenant_id"] == "tenant-a"


def test_build_filter_supports_custom_tenant_field_name():
    query = ScopedQuery(tenant_id="tenant-a")
    assert query.build_filter(tenant_field="org_id") == {"org_id": "tenant-a"}


def test_assert_result_belongs_to_tenant_passes_for_matching_result():
    query = ScopedQuery(tenant_id="tenant-a")
    assert_result_belongs_to_tenant("tenant-a", query)  # must not raise


def test_assert_result_belongs_to_tenant_catches_cross_tenant_leak():
    query = ScopedQuery(tenant_id="tenant-a")
    with pytest.raises(MissingTenantScopeError, match="tenant-b"):
        assert_result_belongs_to_tenant("tenant-b", query)
