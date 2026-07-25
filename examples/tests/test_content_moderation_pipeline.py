from __future__ import annotations

from products.content_moderation_pipeline import ContentModerationPipeline, ModerationServiceConfig


def test_normal_content_is_approved_unchanged():
    pipeline = ContentModerationPipeline()
    result = pipeline.moderate("Great product, would buy again!")
    assert result.approved is True
    assert result.safety_triggered is False
    assert result.stored_content == result.original


def test_content_with_pii_is_redacted_but_still_approved():
    pipeline = ContentModerationPipeline()
    result = pipeline.moderate("Contact me at 13812345678 if interested.")
    assert "13812345678" not in result.stored_content
    assert "138****5678" in result.stored_content


def test_content_with_leaked_secret_is_flagged_for_review():
    pipeline = ContentModerationPipeline()
    result = pipeline.moderate('Nice! api_key: "sk-abcdefghijklmnopqrstuvwxyz123456"')
    assert result.safety_triggered is True
    assert result.approved is False
    assert "sk-abcdefghijklmnopqrstuvwxyz123456" not in result.stored_content


def test_deployment_gate_fails_when_service_config_uses_default_secrets():
    pipeline = ContentModerationPipeline()
    decision = pipeline.deployment_gate(ModerationServiceConfig()).run()
    assert decision.passed is False
    assert len(decision.blockers) == 1
    assert "SecretDrift" in decision.blockers[0]


def test_deployment_gate_passes_when_service_config_is_properly_configured():
    pipeline = ContentModerationPipeline()
    config = ModerationServiceConfig(moderation_api_key="real-key-abc123", webhook_secret="real-secret-xyz789")
    decision = pipeline.deployment_gate(config).run()
    assert decision.passed is True
