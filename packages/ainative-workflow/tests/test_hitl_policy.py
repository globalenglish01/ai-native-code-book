from __future__ import annotations

from ainative_workflow.hitl_policy import (
    DEFAULT_TIMEOUT_SECONDS,
    read_timeout_seconds,
    safe_timeout_decision,
    safe_timeout_decisions,
)


def test_read_timeout_seconds_returns_default_when_unset(monkeypatch):
    monkeypatch.delenv("MY_TIMEOUT", raising=False)
    assert read_timeout_seconds("MY_TIMEOUT") == DEFAULT_TIMEOUT_SECONDS


def test_read_timeout_seconds_parses_valid_value(monkeypatch):
    monkeypatch.setenv("MY_TIMEOUT", "3600")
    assert read_timeout_seconds("MY_TIMEOUT") == 3600


def test_read_timeout_seconds_falls_back_on_invalid_value(monkeypatch):
    monkeypatch.setenv("MY_TIMEOUT", "not-a-number")
    assert read_timeout_seconds("MY_TIMEOUT") == DEFAULT_TIMEOUT_SECONDS


def test_read_timeout_seconds_falls_back_on_non_positive_value(monkeypatch):
    monkeypatch.setenv("MY_TIMEOUT", "-5")
    assert read_timeout_seconds("MY_TIMEOUT") == DEFAULT_TIMEOUT_SECONDS


def test_read_timeout_seconds_respects_custom_default(monkeypatch):
    monkeypatch.delenv("MY_TIMEOUT", raising=False)
    assert read_timeout_seconds("MY_TIMEOUT", default=100) == 100


def test_safe_timeout_decision_is_always_reject():
    decision = safe_timeout_decision()
    assert decision["type"] == "reject"


def test_safe_timeout_decision_cannot_be_forced_to_approve():
    # The function signature itself has no parameter to pass a decision type —
    # this test documents that guarantee rather than testing a bypass.
    import inspect
    sig = inspect.signature(safe_timeout_decision)
    assert "type" not in sig.parameters


def test_safe_timeout_decision_uses_custom_message():
    decision = safe_timeout_decision(message="custom reason")
    assert decision["message"] == "custom reason"


def test_safe_timeout_decisions_generates_n_decisions():
    decisions = safe_timeout_decisions(3)
    assert len(decisions) == 3
    assert all(d["type"] == "reject" for d in decisions)


def test_safe_timeout_decisions_with_zero_or_negative_count():
    assert safe_timeout_decisions(0) == []
    assert safe_timeout_decisions(-5) == []
