from __future__ import annotations

import time

from ainative_guardrail.backpressure import QueueBacklogMonitor, RateLimitedConsumer


def test_record_depth_below_threshold_does_not_warn():
    monitor = QueueBacklogMonitor(warn_threshold=10)
    assert monitor.record_depth(3) is False


def test_record_depth_at_or_above_threshold_warns_once():
    monitor = QueueBacklogMonitor(warn_threshold=5)
    assert monitor.record_depth(5) is True
    assert monitor.record_depth(6) is False  # already warned, no repeat while still over


def test_record_depth_warns_again_after_dropping_below_and_crossing_again():
    monitor = QueueBacklogMonitor(warn_threshold=5)
    monitor.record_depth(5)
    monitor.record_depth(2)  # drop below threshold resets the debounce
    assert monitor.record_depth(5) is True


def test_peak_depth_tracks_the_maximum_seen():
    monitor = QueueBacklogMonitor(warn_threshold=100)
    monitor.record_depth(3)
    monitor.record_depth(10)
    monitor.record_depth(7)
    assert monitor.peak_depth == 10


def test_rate_limited_consumer_allows_calls_up_to_max_within_window():
    consumer = RateLimitedConsumer(max_calls=3, window_seconds=1.0)
    for _ in range(3):
        assert consumer.time_until_next_slot() == 0.0
        consumer.record_call()


def test_rate_limited_consumer_blocks_once_max_calls_reached():
    consumer = RateLimitedConsumer(max_calls=2, window_seconds=10.0)
    consumer.record_call()
    consumer.record_call()

    wait = consumer.time_until_next_slot()

    assert wait > 0


def test_rate_limited_consumer_allows_calls_again_after_window_expires():
    consumer = RateLimitedConsumer(max_calls=1, window_seconds=0.05)
    consumer.record_call()
    assert consumer.time_until_next_slot() > 0

    time.sleep(0.06)

    assert consumer.time_until_next_slot() == 0.0


def test_current_call_count_reflects_only_calls_within_the_window():
    consumer = RateLimitedConsumer(max_calls=5, window_seconds=0.05)
    consumer.record_call()
    consumer.record_call()
    assert consumer.current_call_count == 2

    time.sleep(0.06)

    assert consumer.current_call_count == 0
