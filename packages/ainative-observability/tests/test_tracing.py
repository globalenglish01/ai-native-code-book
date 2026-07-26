from __future__ import annotations

import pytest
from ainative_observability.memory_backends import AlwaysFailingSpanExporter, InMemorySpanExporter
from ainative_observability.tracing import Tracer


def test_span_is_exported_with_correlation_id_and_duration():
    exporter = InMemorySpanExporter()
    tracer = Tracer(exporter)

    with tracer.span("handle_request", correlation_id="req-1"):
        pass

    spans = exporter.for_correlation_id("req-1")
    assert len(spans) == 1
    assert spans[0].name == "handle_request"
    assert spans[0].correlation_id == "req-1"
    assert spans[0].duration_ms >= 0


def test_child_span_links_to_explicit_parent():
    exporter = InMemorySpanExporter()
    tracer = Tracer(exporter)

    with tracer.span("parent_op", correlation_id="req-1") as parent:
        with tracer.span("child_op", correlation_id="req-1", parent=parent):
            pass

    spans = {s.name: s for s in exporter.for_correlation_id("req-1")}
    assert spans["child_op"].parent_span_id == spans["parent_op"].span_id
    assert spans["parent_op"].parent_span_id is None


def test_attributes_are_recorded_on_the_span():
    exporter = InMemorySpanExporter()
    tracer = Tracer(exporter)

    with tracer.span("call_llm", correlation_id="req-1", model="claude-sonnet", tokens=42):
        pass

    span = exporter.for_correlation_id("req-1")[0]
    assert span.attributes == {"model": "claude-sonnet", "tokens": 42}


def test_span_status_is_error_and_message_captured_when_exception_raised():
    exporter = InMemorySpanExporter()
    tracer = Tracer(exporter)

    with pytest.raises(ValueError, match="boom"), tracer.span("risky_op", correlation_id="req-1"):
        raise ValueError("boom")

    span = exporter.for_correlation_id("req-1")[0]
    assert span.status == "error"
    assert span.error_message == "boom"


def test_span_still_exports_even_when_body_raises():
    """A failed operation must still produce a span record (with error
    status) — the export must not be skipped just because the with-block
    raised."""
    exporter = InMemorySpanExporter()
    tracer = Tracer(exporter)

    with pytest.raises(RuntimeError), tracer.span("will_fail", correlation_id="req-1"):
        raise RuntimeError("failure")

    assert len(exporter.for_correlation_id("req-1")) == 1


def test_export_failure_is_counted_and_does_not_propagate_to_caller():
    """The whole point of the observable recorder: an exporter that itself
    fails (backend unreachable) must not crash the traced code, but the
    failure must be countable/observable rather than silently swallowed."""
    tracer = Tracer(AlwaysFailingSpanExporter())

    with tracer.span("op", correlation_id="req-1"):
        pass  # must not raise even though the exporter always fails

    assert tracer.export_failure_count == 1


def test_export_failure_callback_receives_the_exception():
    captured: list[Exception] = []
    tracer = Tracer(AlwaysFailingSpanExporter(), on_export_failure=captured.append)

    with tracer.span("op", correlation_id="req-1"):
        pass

    assert len(captured) == 1
    assert isinstance(captured[0], ConnectionError)


def test_multiple_correlation_ids_are_kept_separate():
    exporter = InMemorySpanExporter()
    tracer = Tracer(exporter)

    with tracer.span("op_a", correlation_id="req-1"):
        pass
    with tracer.span("op_b", correlation_id="req-2"):
        pass

    assert len(exporter.for_correlation_id("req-1")) == 1
    assert len(exporter.for_correlation_id("req-2")) == 1
    assert len(exporter.all()) == 2
