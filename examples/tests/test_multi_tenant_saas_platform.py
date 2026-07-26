from __future__ import annotations

import pytest
from ainative_tenancy import MissingTenantScopeError, QuotaExceededError, ScopedQuery
from products.multi_tenant_saas_platform import MultiTenantSaasPlatform


def test_search_only_returns_documents_belonging_to_the_scoped_tenant():
    platform = MultiTenantSaasPlatform()
    platform.onboard_tenant("acme-corp")
    platform.onboard_tenant("globex-inc")
    platform.ingest_document("acme-corp", "doc-1", "acme secret")
    platform.ingest_document("globex-inc", "doc-2", "globex secret")

    acme_results = platform.search("acme-corp", request_id="req-1")

    assert [d.doc_id for d in acme_results] == ["doc-1"]


def test_search_never_leaks_another_tenants_documents():
    platform = MultiTenantSaasPlatform()
    platform.onboard_tenant("acme-corp")
    platform.onboard_tenant("globex-inc")
    platform.ingest_document("acme-corp", "doc-1", "acme secret")
    platform.ingest_document("globex-inc", "doc-2", "globex secret")

    globex_results = platform.search("globex-inc", request_id="req-2")

    assert all(d.tenant_id == "globex-inc" for d in globex_results)
    assert "doc-1" not in [d.doc_id for d in globex_results]


def test_unscoped_query_is_rejected():
    with pytest.raises(MissingTenantScopeError):
        ScopedQuery(tenant_id="")


def test_one_tenants_quota_exhaustion_does_not_affect_another_tenant():
    platform = MultiTenantSaasPlatform()
    platform.onboard_tenant("acme-corp", max_concurrent_jobs=1)
    platform.onboard_tenant("globex-inc", max_concurrent_jobs=1)
    platform.ingest_document("globex-inc", "doc-2", "globex secret")

    platform.resource_tracker.acquire_job_slot("acme-corp")
    with pytest.raises(QuotaExceededError):
        platform.resource_tracker.acquire_job_slot("acme-corp")

    # globex-inc must still be able to search despite acme-corp being throttled.
    results = platform.search("globex-inc", request_id="req-3")
    assert len(results) == 1


def test_search_records_a_trace_span_with_correlation_id():
    platform = MultiTenantSaasPlatform()
    platform.onboard_tenant("acme-corp")
    platform.ingest_document("acme-corp", "doc-1", "content")

    platform.search("acme-corp", request_id="req-xyz")

    spans = platform.span_exporter.for_correlation_id("req-xyz")
    assert len(spans) == 1
    assert spans[0].name == "search_documents"
    assert spans[0].attributes["tenant_id"] == "acme-corp"


def test_job_slot_is_released_after_search_completes():
    platform = MultiTenantSaasPlatform()
    platform.onboard_tenant("acme-corp", max_concurrent_jobs=1)
    platform.ingest_document("acme-corp", "doc-1", "content")

    platform.search("acme-corp", request_id="req-1")

    assert platform.resource_tracker.active_job_count("acme-corp") == 0
