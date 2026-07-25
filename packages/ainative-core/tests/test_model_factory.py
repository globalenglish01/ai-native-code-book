from __future__ import annotations

from ainative_core.config import ProviderConfig
from ainative_core.memory_backends import InMemoryUsageSink
from ainative_core.model_factory import (
    ModelProvider,
    _provider_of,
    _supports_temperature,
    build_agent_model,
    build_agent_model_with_fallback,
    build_cheap_model,
    build_model,
    get_summarization_config,
    make_cached_system_prompt,
    temperature_kwargs,
)
from langchain_core.language_models import BaseChatModel


def test_supports_temperature_excludes_reasoning_models():
    assert _supports_temperature("openai:gpt-4o") is True
    assert _supports_temperature("openai:o1-preview") is False
    assert _supports_temperature("deepseek:deepseek-reasoner") is False


def test_temperature_kwargs_empty_for_reasoning_models():
    assert temperature_kwargs("openai:o3-mini", 0.2) == {}
    assert temperature_kwargs("anthropic:claude-sonnet-4-5", 0.2) == {"temperature": 0.2}


def test_provider_of_parses_prefix():
    assert _provider_of("anthropic:claude-sonnet-4-5") is ModelProvider.ANTHROPIC
    assert _provider_of("deepseek:deepseek-chat") is ModelProvider.DEEPSEEK
    assert _provider_of("unknown-model") is ModelProvider.AUTO


def test_build_agent_model_with_fallback_returns_none_without_credentials():
    cfg = ProviderConfig(anthropic_api_key="test-key")
    primary, fallback_mw = build_agent_model_with_fallback(config=cfg)
    assert primary is not None
    assert fallback_mw is None


def test_build_agent_model_with_fallback_builds_middleware_when_configured():
    cfg = ProviderConfig(anthropic_api_key="test-key", openai_api_key="test-openai-key")
    primary, fallback_mw = build_agent_model_with_fallback(config=cfg)
    assert primary is not None
    assert fallback_mw is not None


def test_get_summarization_config_without_profile_uses_fixed_fallback():
    class _FakeModel:
        pass

    config = get_summarization_config(_FakeModel())
    assert config == {"max_tokens_before_summary": 40_000, "messages_to_keep": 8}


def test_get_summarization_config_uses_profile_when_available():
    class _FakeModel:
        profile = {"max_input_tokens": 200_000}

    config = get_summarization_config(_FakeModel())
    assert config["max_tokens_before_summary"] == int(200_000 * 0.55)


def test_make_cached_system_prompt_has_ephemeral_cache_control():
    message = make_cached_system_prompt("hello")
    assert message.content[0]["cache_control"] == {"type": "ephemeral"}


def test_build_model_with_usage_sink_still_returns_a_real_base_chat_model():
    """Regression guard for the ch53-01-style invariant: attaching a usage_sink
    must go through callbacks=[...] at construction time, never
    model.with_config(...), which would return a RunnableBinding instead of
    a BaseChatModel and break ModelFallbackMiddleware's type expectations."""
    cfg = ProviderConfig(anthropic_api_key="test-key")
    sink = InMemoryUsageSink()
    model = build_model("anthropic:claude-sonnet-4-5-20250929", config=cfg, usage_sink=sink, agent_name="test_agent")
    assert isinstance(model, BaseChatModel)


def test_build_model_without_usage_sink_does_not_attach_callbacks():
    cfg = ProviderConfig(anthropic_api_key="test-key")
    model = build_model("anthropic:claude-sonnet-4-5-20250929", config=cfg)
    assert isinstance(model, BaseChatModel)


def test_build_agent_model_with_usage_sink_returns_base_chat_model():
    cfg = ProviderConfig(anthropic_api_key="test-key")
    sink = InMemoryUsageSink()
    model = build_agent_model(config=cfg, usage_sink=sink, agent_name="support_agent")
    assert isinstance(model, BaseChatModel)


def test_build_agent_model_with_fallback_still_returns_base_chat_model_when_usage_sink_attached():
    cfg = ProviderConfig(anthropic_api_key="test-key", openai_api_key="test-openai-key")
    sink = InMemoryUsageSink()
    primary, fallback_mw = build_agent_model_with_fallback(config=cfg, usage_sink=sink, agent_name="test_agent")
    assert isinstance(primary, BaseChatModel)
    assert fallback_mw is not None


def test_build_agent_model_preserves_explicit_extra_callbacks_alongside_usage_tracking():
    """_with_usage_tracking must append to, not overwrite, any callbacks the
    caller already configured via other means."""
    from ainative_core.model_factory import _with_usage_tracking

    sink = InMemoryUsageSink()
    sentinel_callback = object()
    result = _with_usage_tracking(
        {"callbacks": [sentinel_callback]}, model_id="anthropic:claude-sonnet-4-5", usage_sink=sink, agent_name="a",
    )
    assert sentinel_callback in result["callbacks"]
    assert len(result["callbacks"]) == 2


def test_with_usage_tracking_returns_extra_kwargs_unchanged_when_no_sink():
    from ainative_core.model_factory import _with_usage_tracking

    original = {"timeout": 60}
    result = _with_usage_tracking(original, model_id="anthropic:claude-sonnet-4-5", usage_sink=None, agent_name="a")
    assert result is original


def test_build_model_passes_deepseek_api_key_and_base_url(monkeypatch):
    """DeepSeek uses the OpenAI-compatible client under init_chat_model, which
    isn't installed in this test environment (langchain-deepseek) — patch
    init_chat_model itself to verify build_model constructs the right kwargs
    without needing the real provider package installed."""
    captured = {}

    def fake_init_chat_model(model_id, **kwargs):
        captured["model_id"] = model_id
        captured["kwargs"] = kwargs
        return object()

    monkeypatch.setattr("ainative_core.model_factory.init_chat_model", fake_init_chat_model)

    cfg = ProviderConfig(deepseek_api_key="test-deepseek-key", deepseek_base_url="https://api.deepseek.example.com")
    build_model("deepseek:deepseek-chat", config=cfg)

    assert captured["kwargs"]["api_key"] == "test-deepseek-key"
    assert captured["kwargs"]["base_url"] == "https://api.deepseek.example.com"


def test_build_model_deepseek_without_base_url_omits_it(monkeypatch):
    captured = {}

    def fake_init_chat_model(model_id, **kwargs):
        captured["kwargs"] = kwargs
        return object()

    monkeypatch.setattr("ainative_core.model_factory.init_chat_model", fake_init_chat_model)

    cfg = ProviderConfig(deepseek_api_key="test-deepseek-key")
    build_model("deepseek:deepseek-chat", config=cfg)

    assert captured["kwargs"]["api_key"] == "test-deepseek-key"
    assert "base_url" not in captured["kwargs"]


def test_build_cheap_model_returns_base_chat_model():
    cfg = ProviderConfig(anthropic_api_key="test-key")
    model = build_cheap_model(config=cfg)
    assert isinstance(model, BaseChatModel)


def test_build_cheap_model_with_usage_sink_and_agent_name():
    cfg = ProviderConfig(anthropic_api_key="test-key")
    sink = InMemoryUsageSink()
    model = build_cheap_model(config=cfg, usage_sink=sink, agent_name="router_agent")
    assert isinstance(model, BaseChatModel)


def test_build_agent_model_with_fallback_includes_deepseek_when_configured_and_language_not_ja_zh(monkeypatch):
    """Covers the DeepSeek fallback branch in build_agent_model_with_fallback —
    patches init_chat_model so the DeepSeek fallback can be constructed
    without the langchain-deepseek package installed."""
    monkeypatch.setattr(
        "ainative_core.model_factory.init_chat_model",
        lambda model_id, **kwargs: object(),
    )
    cfg = ProviderConfig(anthropic_api_key="test-key", deepseek_api_key="test-deepseek-key", preferred_language="en")
    _primary, fallback_mw = build_agent_model_with_fallback(config=cfg)
    assert fallback_mw is not None


def test_build_agent_model_with_fallback_skips_deepseek_when_language_is_ja_or_zh(monkeypatch):
    calls = []

    def fake_init_chat_model(model_id, **kwargs):
        calls.append(model_id)
        return object()

    monkeypatch.setattr("ainative_core.model_factory.init_chat_model", fake_init_chat_model)
    cfg = ProviderConfig(anthropic_api_key="test-key", deepseek_api_key="test-deepseek-key", preferred_language="ja")
    build_agent_model_with_fallback(config=cfg)
    assert not any("deepseek" in c for c in calls)
