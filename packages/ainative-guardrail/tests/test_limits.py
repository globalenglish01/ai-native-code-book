from __future__ import annotations

from ainative_guardrail.limits import DEFAULT_RECURSION_LIMIT, DEFAULT_TOKEN_BUDGET, AgentLimits


def test_unregistered_agent_falls_back_to_defaults():
    limits = AgentLimits()
    assert limits.recursion_limit("unknown") == DEFAULT_RECURSION_LIMIT
    assert limits.token_budget("unknown") == DEFAULT_TOKEN_BUDGET
    assert limits.max_consecutive_errors("unknown") == 2


def test_registered_agent_returns_custom_values():
    limits = AgentLimits()
    limits.register("checkout_agent", recursion_limit=80, token_budget=300_000, max_consecutive_errors=3)
    assert limits.recursion_limit("checkout_agent") == 80
    assert limits.token_budget("checkout_agent") == 300_000
    assert limits.max_consecutive_errors("checkout_agent") == 3
