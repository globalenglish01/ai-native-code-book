from __future__ import annotations

import pytest

from ainative_core.protocols import MemoryEntry
from ainative_memory.store import InMemoryMemoryStore


@pytest.mark.asyncio
async def test_append_and_load_recent_returns_newest_first():
    store = InMemoryMemoryStore()
    for i in range(5):
        await store.append(MemoryEntry(owner_id="user-1", sequence=i, content=f"memory {i}"))

    recent = await store.load_recent("user-1", max_items=3)
    assert [e.sequence for e in recent] == [4, 3, 2]


@pytest.mark.asyncio
async def test_load_recent_respects_before_sequence():
    store = InMemoryMemoryStore()
    for i in range(5):
        await store.append(MemoryEntry(owner_id="user-1", sequence=i, content=f"memory {i}"))

    recent = await store.load_recent("user-1", before_sequence=3, max_items=10)
    assert [e.sequence for e in recent] == [2, 1, 0]


@pytest.mark.asyncio
async def test_load_recent_isolates_different_owners():
    store = InMemoryMemoryStore()
    await store.append(MemoryEntry(owner_id="user-1", sequence=0, content="a"))
    await store.append(MemoryEntry(owner_id="user-2", sequence=0, content="b"))

    user1_entries = await store.load_recent("user-1")
    assert len(user1_entries) == 1
    assert user1_entries[0].content == "a"


@pytest.mark.asyncio
async def test_delete_by_owner_removes_all_entries_and_returns_count():
    store = InMemoryMemoryStore()
    for i in range(3):
        await store.append(MemoryEntry(owner_id="user-1", sequence=i, content=f"memory {i}"))
    await store.append(MemoryEntry(owner_id="user-2", sequence=0, content="untouched"))

    deleted_count = await store.delete_by_owner("user-1")
    assert deleted_count == 3

    remaining = await store.load_recent("user-1")
    assert remaining == []
    other_owner_remaining = await store.load_recent("user-2")
    assert len(other_owner_remaining) == 1


@pytest.mark.asyncio
async def test_delete_by_owner_on_unknown_owner_returns_zero():
    store = InMemoryMemoryStore()
    assert await store.delete_by_owner("never-existed") == 0
