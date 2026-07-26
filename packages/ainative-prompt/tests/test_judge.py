from __future__ import annotations

import pytest
from ainative_prompt.judge import _parse_judge_output, judge_response


class _FakeAIMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeModel:
    """按顺序返回预设响应的假模型，模拟`BaseChatModel.ainvoke`接口。"""

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self._calls = 0

    async def ainvoke(self, messages):
        response = self._responses[self._calls % len(self._responses)]
        self._calls += 1
        if isinstance(response, Exception):
            raise response
        return _FakeAIMessage(response)


def test_parse_judge_output_strips_markdown_code_fence():
    raw = '```json\n{"score": 0.75, "reasoning": "solid"}\n```'
    result = _parse_judge_output(raw)
    assert result == {"score": 0.75, "reasoning": "solid"}


def test_parse_judge_output_strips_bare_code_fence_without_json_label():
    raw = '```\n{"score": 0.5, "reasoning": "ok"}\n```'
    result = _parse_judge_output(raw)
    assert result == {"score": 0.5, "reasoning": "ok"}


def test_parse_judge_output_rejects_score_out_of_range():
    assert _parse_judge_output('{"score": 1.5, "reasoning": "too high"}') is None
    assert _parse_judge_output('{"score": -0.1, "reasoning": "too low"}') is None


def test_parse_judge_output_returns_none_for_malformed_json():
    assert _parse_judge_output("not json at all") is None


def test_parse_judge_output_rejects_boolean_score():
    """Python's bool is an int subclass, so float(True) == 1.0 silently
    passes the 0-1 range check — a judge model hallucinating a malformed
    {"score": true} response must be rejected as invalid, not treated as
    a maximally-confident real score of 1.0."""
    assert _parse_judge_output('{"score": true, "reasoning": "ok"}') is None
    assert _parse_judge_output('{"score": false, "reasoning": "bad"}') is None


def test_parse_judge_output_strips_code_fence_with_non_json_language_tag():
    """A judge model mislabeling the fence language (e.g. ```python instead
    of ```json around otherwise-valid JSON) is a real, observed LLM habit
    — the fence-stripping logic must not depend on the tag literally being
    "json" to still extract valid JSON."""
    raw = '```python\n{"score": 0.8, "reasoning": "good"}\n```'
    assert _parse_judge_output(raw) == {"score": 0.8, "reasoning": "good"}


@pytest.mark.asyncio
async def test_judge_response_aggregates_median_score():
    model = _FakeModel([
        '{"score": 0.8, "reasoning": "good"}',
        '{"score": 0.9, "reasoning": "great"}',
        '{"score": 0.7, "reasoning": "ok"}',
    ])
    result = await judge_response(model, "prompt", "response", "criteria", judge_count=3)
    assert result["ok"] is True
    assert result["score"] == 0.8
    assert result["score_range"] == pytest.approx(0.2)
    assert result["high_uncertainty"] is False


@pytest.mark.asyncio
async def test_judge_response_flags_high_uncertainty_on_wide_spread():
    model = _FakeModel([
        '{"score": 0.1, "reasoning": "bad"}',
        '{"score": 0.9, "reasoning": "great"}',
        '{"score": 0.5, "reasoning": "mixed"}',
    ])
    result = await judge_response(model, "prompt", "response", "criteria", judge_count=3)
    assert result["high_uncertainty"] is True


@pytest.mark.asyncio
async def test_judge_response_returns_not_ok_when_all_calls_unparseable():
    model = _FakeModel(["not json at all"])
    result = await judge_response(model, "prompt", "response", "criteria", judge_count=2)
    assert result["ok"] is False


@pytest.mark.asyncio
async def test_judge_response_tolerates_individual_call_exceptions():
    model = _FakeModel([RuntimeError("boom"), '{"score": 0.5, "reasoning": "ok"}'])
    result = await judge_response(model, "prompt", "response", "criteria", judge_count=2)
    assert result["ok"] is True
    assert result["score"] == 0.5


@pytest.mark.asyncio
async def test_judge_response_applies_sanitize_input_before_calling_model():
    seen = {}

    def sanitize(text: str) -> str:
        seen["input"] = text
        return "SANITIZED"

    model = _FakeModel(['{"score": 1.0, "reasoning": "clean"}'])
    await judge_response(
        model, "prompt", "malicious <script>", "criteria", judge_count=1, sanitize_input=sanitize,
    )
    assert seen["input"] == "malicious <script>"


@pytest.mark.asyncio
async def test_judge_count_is_clamped_between_1_and_5():
    model = _FakeModel(['{"score": 0.5, "reasoning": "ok"}'])
    result = await judge_response(model, "p", "r", "c", judge_count=99)
    assert len(result["judge_calls"]) == 5
