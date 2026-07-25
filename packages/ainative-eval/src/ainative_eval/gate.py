"""通用治理门控（FCARS风格）：多维度检查 + 状态机 + 边界复核防噪声。

改造自真实生产项目里验证过的部署前门控设计（原版针对Fairness/Compliance/
Accountability/Reliability/Safety五个固定维度做结构化+LLM评分检查），本版
把"检查什么"完全外置为可注册的`GateCheck`列表（`ainative_core.protocols`），
`Gate`类本身只负责状态机判定逻辑，不内置任何具体检查项。

保留的核心设计：
1. **统一结果schema**：`{dimension, gating, status, detail, evidence}`
   （`ainative_core.protocols.GateResult`），不管检查的是结构化代码扫描
   还是LLM评分，结果形状一致，便于统一渲染报告/统一判定。
2. **`GREEN/YELLOW/RED/UNKNOWN/SKIPPED/NEEDS_REVIEW`六态状态机**：
   `NEEDS_REVIEW`是原版为了应对"LLM评分噪声"引入的关键状态——分数落在
   阈值边界且复核评分不一致时，既不自动放行也不自动拦截，交给人工确认。
3. **边界复核防噪声**（`maybe_recheck_boundary`）：只有分数落在
   `green_min ± boundary_tolerance`容差内才触发一次重新评分（成本只在
   真正需要时付出），两次分差在`recheck_max_diff`内则采信更保守的较低分，
   分差过大则标记`NEEDS_REVIEW`而不是随便采信一次。
4. **拦截判定**（`decide`）：任一`gating=True`的维度为`RED`/`UNKNOWN`/
   `NEEDS_REVIEW`即拦截；`gating=False`（warn-only）维度即便异常也不拦截，
   只作记录。
"""

from __future__ import annotations

from collections.abc import Callable

from ainative_core.protocols import GateCheck, GateDecision, GateResult, GateStatus

GREEN: GateStatus = "GREEN"
YELLOW: GateStatus = "YELLOW"
RED: GateStatus = "RED"
UNKNOWN: GateStatus = "UNKNOWN"
SKIPPED: GateStatus = "SKIPPED"
NEEDS_REVIEW: GateStatus = "NEEDS_REVIEW"


def status_from_score(score: float | None, green_min: float, yellow_min: float) -> GateStatus:
    """按分数区间映射到状态：``None``→UNKNOWN；``>=green_min``→GREEN；
    ``>=yellow_min``→YELLOW；否则RED。"""
    if score is None:
        return UNKNOWN
    if score >= green_min:
        return GREEN
    if score >= yellow_min:
        return YELLOW
    return RED


def maybe_recheck_boundary(
    score: float,
    green_min: float,
    *,
    boundary_tolerance: float,
    recheck_max_diff: float,
    rescore: Callable[[], float | None],
) -> tuple[float, bool, str]:
    """分数落在gating阈值边界内时，复核一次判定稳定性。

    单次评分噪声可能让本该通过的部署卡在阈值边缘被误拦截，或反之。只在真正
    落入边界容差时才调用`rescore()`重新评分（成本只在需要时付出），两次分差
    在`recheck_max_diff`内则采信更保守的较低分作为最终判定依据；分差过大
    则不采信任一次结果，标记为需要人工复核。

    Args:
        score: 首次评分结果。
        green_min: GREEN状态的判定阈值——同时也是边界复核的中心点。
        boundary_tolerance: 分数与`green_min`的差距在此范围内才触发复核。
        recheck_max_diff: 两次评分的差值超过此值时，判定为"复核不一致"。
        rescore: 重新评分的回调，返回`None`表示复核本身失败。

    Returns:
        ``(final_score, needs_review, note)``——``note``为空字符串表示未触发复核。
    """
    if abs(score - green_min) > boundary_tolerance:
        return score, False, ""

    recheck_score = rescore()
    if recheck_score is None:
        return score, False, "(boundary recheck failed to produce a score, using first result)"

    diff = round(abs(score - recheck_score), 4)
    if diff > recheck_max_diff:
        return score, True, (
            f"boundary recheck inconsistent: first={score} recheck={recheck_score} "
            f"(diff {diff} > {recheck_max_diff}), needs manual review"
        )
    conservative = min(score, recheck_score)
    return conservative, False, (
        f"boundary recheck consistent: first={score} recheck={recheck_score} "
        f"(diff {diff} <= {recheck_max_diff}), using conservative value={conservative}"
    )


def decide(dimensions: list[GateResult]) -> GateDecision:
    """任一`gating=True`维度为RED/UNKNOWN/NEEDS_REVIEW即拦截。

    `NEEDS_REVIEW`不自动放行——既不采信首次评分也不采信复核评分，需要人工
    确认后再决定是否部署，因此和RED/UNKNOWN一样计入拦截项，但拦截说明会
    明确区分"需人工复核"而非"确认有问题"。`gating=False`（warn-only）维度
    即便异常也不拦截，只作记录。
    """
    blockers: list[str] = []
    for d in dimensions:
        if not d.gating:
            continue
        if d.status == RED:
            blockers.append(f"{d.dimension} = RED: {d.detail}")
        elif d.status == UNKNOWN:
            blockers.append(f"{d.dimension} = UNKNOWN (could not be certified): {d.detail}")
        elif d.status == NEEDS_REVIEW:
            blockers.append(
                f"{d.dimension} = NEEDS_REVIEW (score at boundary, recheck inconsistent, "
                f"manual review recommended before deploying): {d.detail}"
            )
    return GateDecision(passed=not blockers, dimensions=dimensions, blockers=blockers)


class Gate:
    """按注册的`GateCheck`列表跑一轮检查，返回统一的`GateDecision`。

    用法::

        gate = Gate([
            GateCheck(name="guardrail_wired", gating=True, check_fn=check_guardrail_wired),
            GateCheck(name="pii_redaction", gating=True, check_fn=check_pii_redaction),
        ])
        decision = gate.run()
        if not decision.passed:
            print(decision.blockers)
    """

    def __init__(self, checks: list[GateCheck]) -> None:
        self._checks = checks

    def run(self) -> GateDecision:
        dimensions: list[GateResult] = []
        for check in self._checks:
            try:
                result = check.check_fn()
            except Exception as exc:
                result = GateResult(
                    dimension=check.name,
                    gating=check.gating,
                    status=UNKNOWN,
                    detail=f"check raised an exception: {exc}",
                )
            dimensions.append(result)
        return decide(dimensions)
