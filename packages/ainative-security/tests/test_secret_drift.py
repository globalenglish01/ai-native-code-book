from __future__ import annotations

from dataclasses import dataclass

import pytest

from ainative_core.protocols import SecretRule
from ainative_security.secret_drift import detect_secret_drift, run_secret_drift_check_once


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
