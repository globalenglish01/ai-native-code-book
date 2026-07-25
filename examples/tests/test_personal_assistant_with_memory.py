from __future__ import annotations

import pytest
from products.personal_assistant_with_memory import PersonalAssistant


@pytest.mark.asyncio
async def test_session_with_no_memories_returns_bare_system_prompt():
    assistant = PersonalAssistant()
    context = await assistant.start_session("user-1", thread_id="session-1")
    assert context == "You are a helpful personal assistant."


@pytest.mark.asyncio
async def test_remembered_facts_are_recalled_in_a_later_session():
    assistant = PersonalAssistant()
    await assistant.remember("user-1", "User likes dark mode.")
    await assistant.remember("user-1", "User's name is Alex.")

    context = await assistant.start_session("user-1", thread_id="session-2")

    assert "User likes dark mode." in context
    assert "User's name is Alex." in context


@pytest.mark.asyncio
async def test_memories_are_scoped_per_user():
    assistant = PersonalAssistant()
    await assistant.remember("user-1", "User-1 secret fact.")
    await assistant.remember("user-2", "User-2 secret fact.")

    context = await assistant.start_session("user-2", thread_id="session-x")

    assert "User-2 secret fact." in context
    assert "User-1 secret fact." not in context


@pytest.mark.asyncio
async def test_only_max_memories_most_recent_are_recalled():
    assistant = PersonalAssistant(max_memories=2)
    for i in range(5):
        await assistant.remember("user-1", f"fact number {i}")

    context = await assistant.start_session("user-1", thread_id="session-1")

    assert "fact number 4" in context
    assert "fact number 3" in context
    assert "fact number 0" not in context


def test_long_history_is_trimmed_to_token_budget():
    assistant = PersonalAssistant()
    long_history = [{"role": "user", "content": "message " * 50} for _ in range(30)]

    trimmed = assistant.build_bounded_history(long_history, max_tokens=500)

    assert len(trimmed) < len(long_history)


@pytest.mark.asyncio
async def test_forget_user_deletes_all_memories_and_removes_memory_block():
    assistant = PersonalAssistant()
    await assistant.remember("user-1", "fact one")
    await assistant.remember("user-1", "fact two")

    deleted_count = await assistant.forget_user("user-1")
    assert deleted_count == 2

    context = await assistant.start_session("user-1", thread_id="session-after-forget")
    assert "fact one" not in context
    assert "fact two" not in context
    assert context == "You are a helpful personal assistant."


@pytest.mark.asyncio
async def test_forget_user_does_not_affect_other_users():
    assistant = PersonalAssistant()
    await assistant.remember("user-1", "user-1 fact")
    await assistant.remember("user-2", "user-2 fact")

    await assistant.forget_user("user-1")

    context = await assistant.start_session("user-2", thread_id="session-1")
    assert "user-2 fact" in context
