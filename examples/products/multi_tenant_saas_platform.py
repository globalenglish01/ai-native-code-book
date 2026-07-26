"""产品示例：多租户SaaS平台（数据隔离 + 资源配额 + 可观测性）。

真实产品形态：多个客户组织（租户）共享同一套后端基础设施——检索/查询
必须从数据源头就携带租户限定条件，而不是先不加区分地查询、再靠事后
过滤（checklist E类真实事故：`scope="all"`完全不做租户过滤，任何持有
合法凭证的调用方都能读到其他租户的全部数据）；共享的任务队列/连接池
这类计算资源也要按租户配额隔离，防止一个任务量大的租户不成比例挤占
资源、拖慢其他租户；同时每次请求都要有结构化日志+追踪span、且日志里
绝不能意外泄露租户的敏感配置。

组合的包：ainative-tenancy（租户隔离+资源配额）+ ainative-observability
（结构化日志+追踪span）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from ainative_observability import InMemorySpanExporter, SensitiveDataFilter, Tracer
from ainative_tenancy import (
    MissingTenantScopeError,
    QuotaExceededError,
    ScopedQuery,
    TenantResourceTracker,
    assert_result_belongs_to_tenant,
    tenant_scope,
)

logger = logging.getLogger("multi_tenant_saas_platform")


@dataclass
class Document:
    doc_id: str
    tenant_id: str
    content: str


class InMemoryDocumentStore:
    """一个刻意从零设计的极简文档存储——只为演示"查询必须真的携带租户
    过滤条件、而不是查完再筛"这个核心设计原则，不代表真实数据库实现。"""

    def __init__(self) -> None:
        self._docs: list[Document] = []

    def add(self, doc: Document) -> None:
        self._docs.append(doc)

    def query(self, scoped_query: ScopedQuery) -> list[Document]:
        """真正在查询这一步就应用租户过滤——`build_filter()`产出的条件
        被用来筛选，而不是先返回全部文档、调用方自己事后再过滤。"""
        filters = scoped_query.build_filter()
        results = [d for d in self._docs if d.tenant_id == filters["tenant_id"]]
        for doc in results:
            assert_result_belongs_to_tenant(doc.tenant_id, scoped_query)
        return results


class MultiTenantSaasPlatform:
    """多租户SaaS平台骨架：租户隔离查询 + 资源配额 + 追踪与结构化日志。"""

    def __init__(self) -> None:
        self.document_store = InMemoryDocumentStore()
        self.resource_tracker = TenantResourceTracker()
        self.span_exporter = InMemorySpanExporter()
        self.tracer = Tracer(self.span_exporter, on_export_failure=lambda exc: logger.error("span export failed: %s", exc))

        logger.setLevel(logging.INFO)
        logger.handlers.clear()
        self._log_handler = logging.StreamHandler()
        self._log_handler.addFilter(SensitiveDataFilter())
        logger.addHandler(self._log_handler)

    def onboard_tenant(self, tenant_id: str, *, max_concurrent_jobs: int = 5) -> None:
        self.resource_tracker.register_quota(tenant_id, max_concurrent_jobs=max_concurrent_jobs)

    def ingest_document(self, tenant_id: str, doc_id: str, content: str) -> None:
        with tenant_scope(tenant_id):
            self.document_store.add(Document(doc_id=doc_id, tenant_id=tenant_id, content=content))

    def search(self, tenant_id: str, request_id: str) -> list[Document]:
        """一次检索请求：占用一个任务名额（配额保护）+ 追踪span（关联
        request_id）+ 从数据源头就携带租户过滤条件的查询。"""
        with self.tracer.span("search_documents", correlation_id=request_id, tenant_id=tenant_id):
            self.resource_tracker.acquire_job_slot(tenant_id)
            try:
                with tenant_scope(tenant_id):
                    query = ScopedQuery(tenant_id=tenant_id)
                    results = self.document_store.query(query)
                logger.info(
                    "search completed", extra={"tenant_id": tenant_id, "request_id": request_id, "result_count": len(results)},
                )
                return results
            finally:
                self.resource_tracker.release_job_slot(tenant_id)


async def main() -> None:
    import sys

    if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    platform = MultiTenantSaasPlatform()
    platform.onboard_tenant("acme-corp", max_concurrent_jobs=2)
    platform.onboard_tenant("globex-inc", max_concurrent_jobs=2)

    platform.ingest_document("acme-corp", "doc-1", "Acme's confidential Q3 roadmap")
    platform.ingest_document("globex-inc", "doc-2", "Globex's confidential merger plan")

    # A search scoped to acme-corp must never see globex-inc's documents.
    acme_results = platform.search("acme-corp", request_id="req-001")
    print(f"acme-corp search -> {len(acme_results)} document(s): {[d.doc_id for d in acme_results]}")
    globex_results = platform.search("globex-inc", request_id="req-002")
    print(f"globex-inc search -> {len(globex_results)} document(s): {[d.doc_id for d in globex_results]}")

    # Attempting to build a query with no tenant at all is rejected outright —
    # there is no "unscoped" query path in this design.
    try:
        ScopedQuery(tenant_id="")
    except MissingTenantScopeError as exc:
        print(f"\nunscoped query correctly rejected: {exc}")

    # acme-corp's quota (2 concurrent jobs) is exhausted by a burst of requests —
    # globex-inc's own quota must be completely unaffected.
    print("\nsimulating a burst of acme-corp searches exhausting its quota:")
    platform.resource_tracker.acquire_job_slot("acme-corp")
    platform.resource_tracker.acquire_job_slot("acme-corp")
    try:
        platform.resource_tracker.acquire_job_slot("acme-corp")
    except QuotaExceededError as exc:
        print(f"  acme-corp correctly throttled: {exc}")
    globex_still_works = platform.search("globex-inc", request_id="req-003")
    print(f"  globex-inc unaffected, search still works: {len(globex_still_works)} document(s)")
    platform.resource_tracker.release_job_slot("acme-corp")
    platform.resource_tracker.release_job_slot("acme-corp")

    spans = platform.span_exporter.for_correlation_id("req-001")
    print(f"\ntrace spans recorded for req-001: {[(s.name, s.attributes.get('tenant_id')) for s in spans]}")


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
