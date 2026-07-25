from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest
from ainative_core.protocols import SecretRule
from ainative_security.secret_drift import (
    DEFAULT_INTERVAL_SECONDS,
    _drift_check_interval_seconds,
    detect_secret_drift,
    run_secret_drift_check_once,
    secret_drift_check_loop,
)


@dataclass
class _FakeConfig:
    jwt_secret: str
    cors_origins: list[str]


def _rules() -> list[SecretRule]:
    return [
        SecretRule(
            name="jwt_secret_is_default",
            is_default=lambda cfg: cfg.jwt_secret == "changeme",
            message="JWT secret is using the insecure default value",
        ),
        SecretRule(
            name="cors_has_localhost",
            is_default=lambda cfg: any("localhost" in o for o in cfg.cors_origins),
            message="CORS_ORIGINS contains localhost",
            severity="warn",
        ),
    ]


def test_detect_secret_drift_returns_empty_when_all_rules_pass():
    config = _FakeConfig(jwt_secret="real-secret-abc123", cors_origins=["https://example.com"])
    assert detect_secret_drift(config, _rules()) == []


def test_detect_secret_drift_reports_hit_rules():
    config = _FakeConfig(jwt_secret="changeme", cors_origins=["http://localhost:3000"])
    issues = detect_secret_drift(config, _rules())
    assert len(issues) == 2


def test_detect_secret_drift_never_raises_on_broken_rule():
    def _broken(cfg):
        raise RuntimeError("boom")

    broken_rule = SecretRule(name="broken", is_default=_broken, message="unreachable")
    config = _FakeConfig(jwt_secret="fine", cors_origins=[])
    issues = detect_secret_drift(config, [broken_rule])
    assert issues == []


@pytest.mark.asyncio
async def test_run_secret_drift_check_once_skips_when_not_monitored():
    config = _FakeConfig(jwt_secret="changeme", cors_origins=[])
    issues = await run_secret_drift_check_once(config, _rules(), is_monitored_environment=False)
    assert issues == []


@pytest.mark.asyncio
async def test_run_secret_drift_check_once_reports_when_monitored():
    config = _FakeConfig(jwt_secret="changeme", cors_origins=[])
    issues = await run_secret_drift_check_once(config, _rules(), is_monitored_environment=True)
    assert len(issues) == 1


def test_drift_check_interval_seconds_returns_default_when_unset(monkeypatch):
    monkeypatch.delenv("MY_INTERVAL", raising=False)
    assert _drift_check_interval_seconds("MY_INTERVAL") == DEFAULT_INTERVAL_SECONDS


def test_drift_check_interval_seconds_parses_valid_positive_value(monkeypatch):
    monkeypatch.setenv("MY_INTERVAL", "1800")
    assert _drift_check_interval_seconds("MY_INTERVAL") == 1800


def test_drift_check_interval_seconds_falls_back_on_non_numeric_value(monkeypatch):
    monkeypatch.setenv("MY_INTERVAL", "not-a-number")
    assert _drift_check_interval_seconds("MY_INTERVAL") == DEFAULT_INTERVAL_SECONDS


def test_drift_check_interval_seconds_falls_back_on_non_positive_value(monkeypatch):
    monkeypatch.setenv("MY_INTERVAL", "0")
    assert _drift_check_interval_seconds("MY_INTERVAL") == DEFAULT_INTERVAL_SECONDS
    monkeypatch.setenv("MY_INTERVAL", "-5")
    assert _drift_check_interval_seconds("MY_INTERVAL") == DEFAULT_INTERVAL_SECONDS


@pytest.mark.asyncio
async def test_secret_drift_check_loop_runs_until_cancelled():
    """This is a "runs forever until cancelled" background loop — the test's
    job is to prove it iterates without crashing and that cancellation stops
    it cleanly, not to assert an exact call count (which would be timing-flaky)."""
    config = _FakeConfig(jwt_secret="changeme", cors_origins=[])

    task = asyncio.create_task(secret_drift_check_loop(config, _rules(), interval_seconds=0.001))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_secret_drift_check_loop_survives_a_failing_check(monkeypatch):
    """The loop must not crash even if run_secret_drift_check_once raises —
    it should log and keep looping (verified here by letting it run past the
    first failing iteration without the task dying)."""
    config = _FakeConfig(jwt_secret="changeme", cors_origins=[])

    call_count = 0

    async def broken_check(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        raise RuntimeError("boom")

    monkeypatch.setattr("ainative_security.secret_drift.run_secret_drift_check_once", broken_check)

    task = asyncio.create_task(secret_drift_check_loop(config, _rules(), interval_seconds=0.001))
    await asyncio.sleep(0.02)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert call_count >= 1
