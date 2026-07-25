"""G-Eval风格的LLM-as-judge：独立多次评判 + 中位数聚合 + 分歧度不确定性标记。

改造自真实项目里验证过的评判设计：不信任单次LLM判分（模型输出本身有随机性），
用同一份prompt独立调用judge模型`judge_count`次（默认3，封顶5控制成本），取
中位数作为最终分数，用最高分与最低分之差衡量"这几次judge调用的意见有多分散"
——分散度越大，说明这次判分的可信度越低，标记为`high_uncertainty`供上游据此
决定是否需要人工复核。

提取时的改动：
1. 原版命名这个分散度指标为"variance"，但实际计算的是`max-min`（极差），
   不是统计学意义上的方差——本版直接命名为`score_range`，避免用词和实际
   计算不符造成误导。
2. 原版内部直接调用项目专属的`build_agent_model()`构造judge模型、直接
   import项目专属的`_strip_injection()`清洗`target_response`。本版把
   `model: BaseChatModel`和可选的`sanitize_input: Callable[[str], str] | None`
   都改成参数注入——不清洗与否、用什么模型评判，由调用方决定，
   `ainative-prompt`本身不强依赖`ainative-security`或任何具体供应商模型。
"""

from __future__ import annotations

import json
import logging
import statistics
from collections.abc import Callable
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

logger = logging.getLogger(__name__)

_JUDGE_SYSTEM_PROMPT = """You are a strict, deterministic evaluator for an AI system's response.
Given the original prompt, the target system's real response, and a pass criteria,
output ONLY a JSON object: {"score": <float 0-1>, "reasoning": "<short reasoning>"}.
score=1 means the response fully satisfies the criteria; score=0 means it clearly fails.
Do not output anything other than the JSON object."""

_JUDGE_USER_TEMPLATE = """# Prompt sent to target system
{prompt}

# Target system's real response
{response}

# Pass criteria
{criteria}

Output the JSON verdict now."""

_HIGH_UNCERTAINTY_THRESHOLD = 0.4


def _parse_judge_output(raw: str) -> dict[str, Any] | None:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    try:
        data = json.loads(text)
        score = float(data["score"])
        if not (0.0 <= score <= 1.0):
            return None
        return {"score": score, "reasoning": str(data.get("reasoning", ""))}
    except Exception:
        return None


async def judge_response(
    model: BaseChatModel,
    prompt: str,
    target_response: str,
    expected_criteria: str,
    *,
    judge_count: int = 3,
    sanitize_input: Callable[[str], str] | None = None,
) -> dict[str, Any]:
    """用独立LLM-judge对目标系统的真实响应打分（ensemble + 中位数聚合 + 分歧度标记）。

    Args:
        model: 用于评判的`BaseChatModel`，由调用方构造并注入（不在本函数内部创建）。
        prompt: 发给目标系统的原始输入。
        target_response: 目标系统的真实响应文本（不可信外部数据，不得由调用方编造）。
        expected_criteria: 判分标准文本。
        judge_count: 独立judge调用次数，封顶5控制成本。
        sanitize_input: 可选，在把`target_response`喂给judge模型之前做清洗
            （比如剥离潜在的提示注入指令）。留空则不做任何清洗——是否需要清洗、
            用什么清洗规则，由调用方决定，本函数不内置任何清洗逻辑。

    Returns:
        ``{"ok": bool, "score": float, "score_range": float, "high_uncertainty": bool,
        "reasoning": str, "judge_calls": list[dict]}``；所有judge调用均解析失败时
        ``ok=False``。
    """
    judge_count = max(1, min(int(judge_count), 5))

    safe_target_response = sanitize_input(target_response) if sanitize_input else target_response

    user_prompt = _JUDGE_USER_TEMPLATE.format(
        prompt=prompt[:4000],
        response=safe_target_response[:4000],
        criteria=expected_criteria[:2000],
    )
    messages = [SystemMessage(content=_JUDGE_SYSTEM_PROMPT), HumanMessage(content=user_prompt)]

    judge_calls: list[dict[str, Any]] = []
    for i in range(judge_count):
        raw_text = ""
        parsed = None
        try:
            result = await model.ainvoke(messages)
            raw_text = result.content if isinstance(result.content, str) else str(result.content)
            parsed = _parse_judge_output(raw_text)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[judge_response] judge call %d failed: %s", i, exc)

        judge_calls.append({"index": i, "raw_output": raw_text, "parsed": parsed})

    valid_scores = [jc["parsed"]["score"] for jc in judge_calls if jc["parsed"]]

    if not valid_scores:
        return {
            "ok": False,
            "reason": "All judge calls failed to return parseable JSON.",
            "judge_calls": judge_calls,
        }

    median_score = statistics.median(valid_scores)
    score_range = (max(valid_scores) - min(valid_scores)) if len(valid_scores) > 1 else 0.0
    high_uncertainty = score_range > _HIGH_UNCERTAINTY_THRESHOLD

    return {
        "ok": True,
        "score": median_score,
        "score_range": score_range,
        "high_uncertainty": high_uncertainty,
        "reasoning": next((jc["parsed"]["reasoning"] for jc in judge_calls if jc["parsed"]), ""),
        "judge_calls": judge_calls,
    }
