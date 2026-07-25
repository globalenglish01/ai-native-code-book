from __future__ import annotations

from ainative_workflow.hitl import count_pending_decisions, extract_interrupt


class _FakeInterrupt:
    def __init__(self, value):
        self.value = value


def test_extract_interrupt_returns_none_when_absent():
    assert extract_interrupt({}) is None
    assert extract_interrupt({"__interrupt__": []}) is None


def test_extract_interrupt_returns_value_attribute_when_present():
    result = {"__interrupt__": [_FakeInterrupt({"action_requests": [{"id": 1}]})]}
    payload = extract_interrupt(result)
    assert payload == {"action_requests": [{"id": 1}]}


def test_extract_interrupt_handles_plain_dict_without_value_attribute():
    result = {"__interrupt__": [{"action_requests": []}]}
    payload = extract_interrupt(result)
    assert payload == {"action_requests": []}


def test_extract_interrupt_only_returns_first_of_multiple(caplog):
    result = {"__interrupt__": [_FakeInterrupt({"id": "first"}), _FakeInterrupt({"id": "second"})]}
    payload = extract_interrupt(result)
    assert payload == {"id": "first"}


def test_extract_interrupt_respects_custom_key():
    result = {"custom_key": [_FakeInterrupt({"x": 1})]}
    assert extract_interrupt(result, interrupt_key="custom_key") == {"x": 1}


def test_count_pending_decisions_with_none_payload():
    assert count_pending_decisions(None) == 0


def test_count_pending_decisions_counts_action_requests():
    payload = {"action_requests": [{"id": 1}, {"id": 2}, {"id": 3}]}
    assert count_pending_decisions(payload) == 3


def test_count_pending_decisions_respects_custom_key():
    payload = {"my_requests": [{"id": 1}]}
    assert count_pending_decisions(payload, requests_key="my_requests") == 1
