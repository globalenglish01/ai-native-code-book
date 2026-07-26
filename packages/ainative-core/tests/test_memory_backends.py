from __future__ import annotations

from ainative_core.memory_backends import InMemoryUsageSink


def test_record_and_events_returns_all_recorded_events():
    sink = InMemoryUsageSink()
    sink.record({"agent_name": "a", "input_tokens": 10, "output_tokens": 5})
    sink.record({"agent_name": "b", "input_tokens": 20, "output_tokens": 0})
    assert len(sink.events) == 2


def test_events_returns_a_copy_not_the_internal_list():
    sink = InMemoryUsageSink()
    sink.record({"agent_name": "a"})
    events = sink.events
    events.append({"agent_name": "should_not_appear_in_sink"})
    assert len(sink.events) == 1


def test_mutating_the_event_dict_after_record_does_not_corrupt_the_stored_copy():
    """record() must deep-copy the event, not store the caller's dict by
    reference — a caller reusing a template event dict across successive
    record() calls must not be able to silently rewrite history already
    believed persisted."""
    sink = InMemoryUsageSink()
    event = {"agent_name": "a1", "input_tokens": 100, "output_tokens": 50}
    sink.record(event)

    event["input_tokens"] = 999999
    event["agent_name"] = "a2"

    assert sink.events[0] == {"agent_name": "a1", "input_tokens": 100, "output_tokens": 50}


def test_mutating_an_event_returned_by_the_events_property_does_not_corrupt_internal_state():
    sink = InMemoryUsageSink()
    sink.record({"agent_name": "a1", "input_tokens": 100})

    got = sink.events
    got[0]["agent_name"] = "MUTATED"

    assert sink.events[0]["agent_name"] == "a1"


def test_total_tokens_sums_input_and_output_across_all_events():
    sink = InMemoryUsageSink()
    sink.record({"input_tokens": 10, "output_tokens": 5})
    sink.record({"input_tokens": 20, "output_tokens": 3})
    assert sink.total_tokens() == 38


def test_total_tokens_handles_missing_fields_gracefully():
    sink = InMemoryUsageSink()
    sink.record({"agent_name": "a"})  # no token fields at all
    assert sink.total_tokens() == 0


def test_total_for_agent_filters_by_agent_name():
    sink = InMemoryUsageSink()
    sink.record({"agent_name": "a", "input_tokens": 10, "output_tokens": 0})
    sink.record({"agent_name": "b", "input_tokens": 100, "output_tokens": 0})
    sink.record({"agent_name": "a", "input_tokens": 5, "output_tokens": 0})
    assert sink.total_for_agent("a") == 15
    assert sink.total_for_agent("b") == 100
    assert sink.total_for_agent("nonexistent") == 0
