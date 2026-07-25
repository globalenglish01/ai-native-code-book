from __future__ import annotations

import pytest
from ainative_core.protocols import PromptVariant
from ainative_prompt.store import InMemoryPromptStore, ab_select_deterministic, load_prompt


@pytest.mark.asyncio
async def test_load_prompt_falls_back_to_default_when_no_variants():
    store = InMemoryPromptStore()
    result = await load_prompt(store, "my_agent", default="hello default")
    assert result == "hello default"


@pytest.mark.asyncio
async def test_load_prompt_returns_single_variant_directly():
    store = InMemoryPromptStore()
    await store.save_variant(
        "my_agent", "system_prompt",
        PromptVariant(variant="default", content="You are helpful.", traffic_pct=100, version=1),
    )
    result = await load_prompt(store, "my_agent")
    assert result == "You are helpful."


@pytest.mark.asyncio
async def test_load_prompt_sticky_routing_same_thread_id_same_variant():
    store = InMemoryPromptStore()
    await store.save_variant(
        "my_agent", "system_prompt",
        PromptVariant(variant="a", content="Variant A", traffic_pct=50, version=1),
    )
    await store.save_variant(
        "my_agent", "system_prompt",
        PromptVariant(variant="b", content="Variant B", traffic_pct=50, version=1),
    )

    first = await load_prompt(store, "my_agent", thread_id="thread-123")
    second = await load_prompt(store, "my_agent", thread_id="thread-123")
    assert first == second


@pytest.mark.asyncio
async def test_load_prompt_sticky_decision_ignored_if_variant_deactivated():
    store = InMemoryPromptStore()
    await store.save_variant(
        "my_agent", "system_prompt",
        PromptVariant(variant="a", content="Variant A", traffic_pct=100, version=1),
    )
    await store.record_decision("my_agent", "system_prompt", "thread-1", "b")

    # "b" was never actually saved as an active variant, so it must not be trusted.
    result = await load_prompt(store, "my_agent", thread_id="thread-1")
    assert result == "Variant A"


def test_ab_select_deterministic_same_thread_id_always_same_variant():
    variants = [
        PromptVariant(variant="a", content="A", traffic_pct=30, version=1),
        PromptVariant(variant="b", content="B", traffic_pct=70, version=1),
    ]
    first = ab_select_deterministic(variants, "thread-abc")
    second = ab_select_deterministic(variants, "thread-abc")
    assert first.variant == second.variant


def test_ab_select_deterministic_zero_traffic_falls_back_to_first():
    variants = [PromptVariant(variant="a", content="A", traffic_pct=0, version=1)]
    result = ab_select_deterministic(variants, "thread-1")
    assert result.variant == "a"


def test_ab_select_deterministic_anonymous_calls_always_same_variant():
    """记录已知设计特性：thread_id为None时，所有匿名调用路由到同一固定变体。"""
    variants = [
        PromptVariant(variant="a", content="A", traffic_pct=50, version=1),
        PromptVariant(variant="b", content="B", traffic_pct=50, version=1),
    ]
    first = ab_select_deterministic(variants, None)
    second = ab_select_deterministic(variants, None)
    assert first.variant == second.variant
