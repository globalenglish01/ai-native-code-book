from __future__ import annotations

from ainative_memory.history_budget import estimate_history_tokens, trim_history_to_budget


def test_trim_history_keeps_most_recent_messages_within_budget():
    history = [{"role": "user", "content": "x" * 40} for _ in range(10)]  # 10 tokens each
    trimmed = trim_history_to_budget(history, max_tokens=35)
    assert len(trimmed) == 3


def test_trim_history_always_keeps_at_least_the_most_recent_message():
    history = [{"role": "user", "content": "x" * 4000}]  # 1000 tokens, way over budget
    trimmed = trim_history_to_budget(history, max_tokens=10)
    assert len(trimmed) == 1


def test_trim_history_preserves_chronological_order():
    history = [{"role": "user", "content": f"msg{i}" * 4} for i in range(5)]
    trimmed = trim_history_to_budget(history, max_tokens=1000)
    contents = [m["content"] for m in trimmed]
    assert contents == [m["content"] for m in history]


def test_trim_history_empty_input_returns_empty():
    assert trim_history_to_budget([], max_tokens=100) == []


def test_estimate_history_tokens_sums_all_messages():
    history = [{"content": "x" * 40}, {"content": "y" * 40}]
    assert estimate_history_tokens(history) == 20
