from __future__ import annotations

from uuid import uuid4

from ainative_core.memory_backends import InMemoryUsageSink
from ainative_core.usage_tracking import UsageTrackingCallbackHandler, _extract_model_name, _extract_usage
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult


def _make_result(*, usage_metadata=None, llm_output=None) -> LLMResult:
    message = AIMessage(content="reply", usage_metadata=usage_metadata)
    return LLMResult(generations=[[ChatGeneration(message=message)]], llm_output=llm_output or {})


def test_extract_usage_reads_input_and_output_tokens():
    result = _make_result(usage_metadata={"input_tokens": 42, "output_tokens": 7, "total_tokens": 49})
    input_tokens, output_tokens = _extract_usage(result)
    assert (input_tokens, output_tokens) == (42, 7)


def test_extract_usage_defaults_to_zero_when_missing():
    result = _make_result(usage_metadata=None)
    assert _extract_usage(result) == (0, 0)


def test_extract_usage_sums_across_multiple_generations():
    msg1 = AIMessage(content="a", usage_metadata={"input_tokens": 10, "output_tokens": 1, "total_tokens": 11})
    msg2 = AIMessage(content="b", usage_metadata={"input_tokens": 20, "output_tokens": 2, "total_tokens": 22})
    result = LLMResult(generations=[[ChatGeneration(message=msg1)], [ChatGeneration(message=msg2)]])
    assert _extract_usage(result) == (30, 3)


def test_extract_model_name_reads_model_name_field():
    result = _make_result(llm_output={"model_name": "claude-sonnet-4-5"})
    assert _extract_model_name(result) == "claude-sonnet-4-5"


def test_extract_model_name_falls_back_to_model_field():
    result = _make_result(llm_output={"model": "gpt-4o"})
    assert _extract_model_name(result) == "gpt-4o"


def test_extract_model_name_returns_none_when_absent():
    result = _make_result(llm_output={})
    assert _extract_model_name(result) is None


def test_callback_handler_records_a_usage_event_on_llm_end():
    sink = InMemoryUsageSink()
    handler = UsageTrackingCallbackHandler(
        sink, agent_name="support_agent", provider="anthropic", model_id="anthropic:claude-sonnet-4-5",
    )
    result = _make_result(usage_metadata={"input_tokens": 100, "output_tokens": 20, "total_tokens": 120})

    handler.on_llm_end(result, run_id=uuid4())

    assert len(sink.events) == 1
    event = sink.events[0]
    assert event["agent_name"] == "support_agent"
    assert event["provider"] == "anthropic"
    assert event["input_tokens"] == 100
    assert event["output_tokens"] == 20
    assert "timestamp" in event


def test_callback_handler_falls_back_to_configured_model_id_when_response_has_none():
    sink = InMemoryUsageSink()
    handler = UsageTrackingCallbackHandler(
        sink, agent_name="a", provider="anthropic", model_id="anthropic:claude-sonnet-4-5",
    )
    result = _make_result(llm_output={})

    handler.on_llm_end(result, run_id=uuid4())

    assert sink.events[0]["model"] == "anthropic:claude-sonnet-4-5"


def test_callback_handler_prefers_model_name_from_response_over_configured_id():
    sink = InMemoryUsageSink()
    handler = UsageTrackingCallbackHandler(
        sink, agent_name="a", provider="anthropic", model_id="anthropic:claude-sonnet-4-5",
    )
    result = _make_result(llm_output={"model_name": "claude-sonnet-4-5-20250929"})

    handler.on_llm_end(result, run_id=uuid4())

    assert sink.events[0]["model"] == "claude-sonnet-4-5-20250929"
