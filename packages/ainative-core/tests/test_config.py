from __future__ import annotations

import dataclasses

import pytest
from ainative_core.config import ProviderConfig


def test_from_env_reads_all_expected_variables(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-key")
    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-key")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://api.deepseek.example.com")
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("AGENT_PREFERRED_LANGUAGE", "ja")
    monkeypatch.setenv("AGENT_MODEL_RELIABLE", "anthropic:claude-opus-4")
    monkeypatch.setenv("AGENT_MODEL_CHEAP", "anthropic:claude-haiku-4")

    cfg = ProviderConfig.from_env()

    assert cfg.anthropic_api_key == "anthropic-key"
    assert cfg.openai_api_key == "openai-key"
    assert cfg.deepseek_api_key == "deepseek-key"
    assert cfg.deepseek_base_url == "https://api.deepseek.example.com"
    assert cfg.is_production is True
    assert cfg.preferred_language == "ja"
    assert cfg.default_model_id == "anthropic:claude-opus-4"
    assert cfg.cheap_model_id == "anthropic:claude-haiku-4"


def test_from_env_uses_defaults_when_unset(monkeypatch):
    for var in [
        "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL",
        "ENVIRONMENT", "AGENT_PREFERRED_LANGUAGE", "AGENT_MODEL_RELIABLE", "AGENT_MODEL", "AGENT_MODEL_CHEAP",
    ]:
        monkeypatch.delenv(var, raising=False)

    cfg = ProviderConfig.from_env()

    assert cfg.anthropic_api_key is None
    assert cfg.is_production is False
    assert cfg.default_model_id == "anthropic:claude-sonnet-4-5-20250929"
    assert cfg.cheap_model_id == "anthropic:claude-haiku-4-5"


def test_from_env_falls_back_to_agent_model_before_hardcoded_default(monkeypatch):
    monkeypatch.delenv("AGENT_MODEL_RELIABLE", raising=False)
    monkeypatch.setenv("AGENT_MODEL", "anthropic:claude-custom")

    cfg = ProviderConfig.from_env()
    assert cfg.default_model_id == "anthropic:claude-custom"


def test_provider_config_is_frozen():
    cfg = ProviderConfig()
    with pytest.raises(dataclasses.FrozenInstanceError):
        cfg.anthropic_api_key = "should not be allowed"
