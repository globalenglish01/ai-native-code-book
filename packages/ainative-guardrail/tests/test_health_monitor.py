from __future__ import annotations

from ainative_guardrail.health_monitor import GuardHealthMonitorMiddleware
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
