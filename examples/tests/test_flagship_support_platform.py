from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from products.flagship_support_platform import (
    SupportPlatform,
    fake_llm_respond,
    scaffold_support_project,
)


def test_scaffold_support_project_writes_expected_files():
    with tempfile.TemporaryDirectory() as tmp:
        target_dir = Path(tmp) / "acme-support-platform"
        written = scaffold_support_project(target_dir)

        names = {p.name for p in written}
        assert names == {"pyproject.toml", "README.md", "main.py", ".env.example"}
        assert all(p.exists() for p in written)


@pytest.mark.asyncio
async def test_normal_turn_is_not_escalated():
    platform = SupportPlatform()
    turn = await platform.handle_turn("user-1", "Hi, I'd like a refund.", fake_llm_respond)

    assert turn.escalated_to_specialist is False
    assert "refund" in turn.bot_reply.lower()


@pytest.mark.asyncio
async def test_billing_dispute_is_escalated_to_specialist():
    platform = SupportPlatform()
    turn = await platform.handle_turn(
        "user-1", "I want to file a billing dispute chargeback", fake_llm_respond,
    )

    assert turn.escalated_to_specialist is True
    assert "specialist" in turn.bot_reply.lower()
    assert len(platform.specialist.audit_log.all()) == 1


@pytest.mark.asyncio
async def test_pii_in_escalated_case_is_redacted_before_reaching_specialist():
    """The specialist agent must only ever see redacted case data — the raw
    user_message (with PII) must never cross the A2A delegation boundary."""
    platform = SupportPlatform()
    turn = await platform.handle_turn(
        "user-1", "My phone is 13812345678 and I want to file a billing dispute chargeback",
        fake_llm_respond,
    )

    assert "13812345678" not in turn.bot_reply
    specialist_input = platform.specialist.audit_log.all()[0].input_summary["case_summary"]
    assert "13812345678" not in specialist_input


@pytest.mark.asyncio
async def test_leaked_secret_in_response_is_still_caught_after_escalation():
    platform = SupportPlatform()

    def leaking_respond(_history):
        return 'Sure! api_key: "sk-leaked1234567890123456789"'

    turn = await platform.handle_turn("user-1", "Just saying hi, no dispute here.", leaking_respond)

    assert turn.safety_triggered is True
    assert "sk-leaked1234567890123456789" not in turn.bot_reply


def test_deployment_gate_passes_with_specialist_wired():
    platform = SupportPlatform()
    decision = platform.deployment_gate().run()
    assert decision.passed is True
