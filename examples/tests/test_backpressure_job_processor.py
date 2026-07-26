from __future__ import annotations

from products.backpressure_job_processor import BackpressureAwareJobProcessor


def test_enqueue_below_threshold_does_not_trigger_warning():
    processor = BackpressureAwareJobProcessor(backlog_warn_threshold=10, max_calls_per_window=5, window_seconds=1.0)
    assert processor.enqueue("job-1") is False


def test_enqueue_crossing_threshold_triggers_warning_once():
    processor = BackpressureAwareJobProcessor(backlog_warn_threshold=3, max_calls_per_window=5, window_seconds=1.0)
    processor.enqueue("job-1")
    processor.enqueue("job-2")
    assert processor.enqueue("job-3") is True
    assert processor.enqueue("job-4") is False  # already warned


def test_peak_depth_is_tracked_across_a_burst():
    processor = BackpressureAwareJobProcessor(backlog_warn_threshold=100, max_calls_per_window=5, window_seconds=1.0)
    for i in range(5):
        processor.enqueue(f"job-{i}")
    assert processor.backlog_monitor.peak_depth == 5


def test_process_next_returns_none_when_queue_is_empty():
    processor = BackpressureAwareJobProcessor(backlog_warn_threshold=10, max_calls_per_window=5, window_seconds=1.0)
    assert processor.process_next() is None


def test_process_next_drains_jobs_in_fifo_order():
    processor = BackpressureAwareJobProcessor(backlog_warn_threshold=10, max_calls_per_window=5, window_seconds=1.0)
    processor.enqueue("job-1")
    processor.enqueue("job-2")

    first = processor.process_next()
    second = processor.process_next()

    assert first.job_id == "job-1"
    assert second.job_id == "job-2"


def test_rate_limit_forces_a_wait_once_the_window_is_exhausted():
    processor = BackpressureAwareJobProcessor(backlog_warn_threshold=10, max_calls_per_window=1, window_seconds=0.05)
    processor.enqueue("job-1")
    processor.enqueue("job-2")

    first = processor.process_next()
    second = processor.process_next()

    assert first.waited_seconds == 0.0
    assert second.waited_seconds > 0
