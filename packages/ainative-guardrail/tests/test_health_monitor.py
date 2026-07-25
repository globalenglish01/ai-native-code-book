from __future__ import annotations

import pytest
from ainative_guardrail.health_monitor import GuardHealthMonitorMiddleware, _default_warn_ratio
from langchain_core.messages import AIMessage


class _FakeRequest:
    def __init__(self, messages):
        self.messages = messages


def test_no_warning_when_dimensions_are_low():
    mw = GuardHealthMonitorMiddleware(recursion_limit=100, token_budget=100_000, warn_ratio=0.7)
    dims = mw._evaluate(_FakeRequest([AIMessage(content="short")]))
    assert dims is None


def test_warns_once_when_two_dimensions_exceed_ratio():
    mw = GuardHealthMonitorMiddleware(recursion_limit=10, token_budget=100, warn_ratio=0.7)

    triggered = None
    messages = []
    for _ in range(9):
        # New message instance each turn — mirrors a real, growing conversation
        # history (TokenCounter dedupes by message identity, so reusing the
        # same object would only count its estimate once).
        messages.append(AIMessage(content="x" * 40))
        result = mw._evaluate(_FakeRequest(list(messages)))
        if result is not None:
            triggered = result

    assert triggered is not None
    assert mw._warned is True

    # Should not warn a second time (de-duplicated).
    messages.append(AIMessage(content="x" * 40))
    second = mw._evaluate(_FakeRequest(list(messages)))
    assert second is None


def test_wrap_model_call_passes_through_and_evaluates():
    mw = GuardHealthMonitorMiddleware(recursion_limit=100, token_budget=100_000)
    response = mw.wrap_model_call(_FakeRequest([AIMessage(content="short")]), lambda r: "handled")
    assert response == "handled"
    assert mw._steps == 1


@pytest.mark.asyncio
async def test_awrap_model_call_passes_through_and_evaluates():
    mw = GuardHealthMonitorMiddleware(recursion_limit=100, token_budget=100_000)

    async def handler(r):
        return "handled"

    response = await mw.awrap_model_call(_FakeRequest([AIMessage(content="short")]), handler)
    assert response == "handled"
    assert mw._steps == 1


def test_default_warn_ratio_reads_env_var(monkeypatch):
    monkeypatch.setenv("AINATIVE_GUARD_HEALTH_WARN_RATIO", "0.5")
    assert _default_warn_ratio() == 0.5


def test_default_warn_ratio_falls_back_when_unset(monkeypatch):
    monkeypatch.delenv("AINATIVE_GUARD_HEALTH_WARN_RATIO", raising=False)
    assert _default_warn_ratio() == 0.7


def test_default_warn_ratio_falls_back_on_invalid_value(monkeypatch):
    monkeypatch.setenv("AINATIVE_GUARD_HEALTH_WARN_RATIO", "not-a-number")
    assert _default_warn_ratio() == 0.7


def test_default_warn_ratio_falls_back_on_out_of_range_value(monkeypatch):
    monkeypatch.setenv("AINATIVE_GUARD_HEALTH_WARN_RATIO", "1.5")
    assert _default_warn_ratio() == 0.7
    monkeypatch.setenv("AINATIVE_GUARD_HEALTH_WARN_RATIO", "0")
    assert _default_warn_ratio() == 0.7


def test_constructor_uses_default_warn_ratio_when_none_passed(monkeypatch):
    monkeypatch.delenv("AINATIVE_GUARD_HEALTH_WARN_RATIO", raising=False)
    mw = GuardHealthMonitorMiddleware(recursion_limit=10, token_budget=100)
    assert mw._warn_ratio == 0.7


def test_recursion_limit_and_token_budget_are_clamped_to_at_least_one():
    mw = GuardHealthMonitorMiddleware(recursion_limit=0, token_budget=-5)
    assert mw._recursion_limit == 1
    assert mw._token_budget == 1
