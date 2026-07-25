from __future__ import annotations

import asyncio

import pytest
from ainative_memory.checkpoint import CheckpointSaverFactory


@pytest.mark.asyncio
async def test_get_builds_saver_once_and_caches_it():
    call_count = 0

    async def build_saver():
        nonlocal call_count
        call_count += 1
        return f"saver-{call_count}"

    factory = CheckpointSaverFactory(build_saver)
    first = await factory.get()
    second = await factory.get()
    assert first == "saver-1"
    assert second == "saver-1"
    assert call_count == 1


@pytest.mark.asyncio
async def test_permanent_failure_locks_and_returns_none():
    call_count = 0

    async def build_saver():
        nonlocal call_count
        call_count += 1
        raise ImportError("psycopg not installed")

    factory = CheckpointSaverFactory(build_saver)
    result1 = await factory.get()
    result2 = await factory.get()
    assert result1 is None
    assert result2 is None
    # Permanently failed after the first attempt — no further attempts made.
    assert call_count == 1


@pytest.mark.asyncio
async def test_transient_failure_retries_after_interval_and_can_self_heal():
    attempts = 0

    async def build_saver():
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise ConnectionError("db unreachable")
        return "saver-ok"

    factory = CheckpointSaverFactory(build_saver, retry_interval_seconds=0.01)

    result = await factory.get()
    assert result is None
    assert attempts == 1

    # Immediately retrying before the throttle interval elapses should not re-attempt.
    result = await factory.get()
    assert result is None
    assert attempts == 1

    await asyncio.sleep(0.02)
    result = await factory.get()
    assert result is None
    assert attempts == 2

    await asyncio.sleep(0.02)
    result = await factory.get()
    assert result == "saver-ok"
    assert attempts == 3


@pytest.mark.asyncio
async def test_reset_clears_permanent_failure_and_allows_retry():
    async def always_fails():
        raise ImportError("missing dependency")

    factory = CheckpointSaverFactory(always_fails)
    await factory.get()
    assert (await factory.get()) is None

    factory.reset()

    call_count = 0

    async def now_works():
        nonlocal call_count
        call_count += 1
        return "recovered"

    factory._build_saver = now_works
    result = await factory.get()
    assert result == "recovered"


@pytest.mark.asyncio
async def test_concurrent_get_calls_after_permanent_failure_all_return_none():
    """Regression/branch coverage for the double-checked-lock permanent-failure
    check inside the lock (not just the fast-path check before acquiring it):
    multiple concurrent get() calls racing for the lock after the first one
    already set _permanently_failed must all return None via the in-lock
    check, not attempt to rebuild."""
    call_count = 0

    async def always_fails():
        nonlocal call_count
        call_count += 1
        await asyncio.sleep(0.01)
        raise ImportError("psycopg not installed")

    factory = CheckpointSaverFactory(always_fails)
    results = await asyncio.gather(*(factory.get() for _ in range(10)))
    assert all(r is None for r in results)
    assert call_count == 1


@pytest.mark.asyncio
async def test_concurrent_get_calls_only_build_once():
    call_count = 0

    async def slow_build():
        nonlocal call_count
        call_count += 1
        await asyncio.sleep(0.01)
        return "saver"

    factory = CheckpointSaverFactory(slow_build)
    results = await asyncio.gather(*(factory.get() for _ in range(10)))
    assert all(r == "saver" for r in results)
    assert call_count == 1
