from __future__ import annotations

from ainative_core.protocols import GateCheck, GateResult
from ainative_eval.gate import (
    GREEN,
    NEEDS_REVIEW,
    RED,
    UNKNOWN,
    YELLOW,
    Gate,
    decide,
    maybe_recheck_boundary,
    status_from_score,
)


def test_status_from_score_boundaries():
    assert status_from_score(0.9, green_min=0.8, yellow_min=0.6) == GREEN
    assert status_from_score(0.7, green_min=0.8, yellow_min=0.6) == YELLOW
    assert status_from_score(0.5, green_min=0.8, yellow_min=0.6) == RED
    assert status_from_score(None, green_min=0.8, yellow_min=0.6) == UNKNOWN


def test_decide_passes_when_no_gating_dimension_is_red():
    dims = [
        GateResult(dimension="Safety", gating=True, status=GREEN, detail="ok"),
        GateResult(dimension="Experimental", gating=False, status=RED, detail="warn-only, ignored"),
    ]
    decision = decide(dims)
    assert decision.passed is True
    assert decision.blockers == []


def test_decide_blocks_on_gating_red():
    dims = [GateResult(dimension="Safety", gating=True, status=RED, detail="bad")]
    decision = decide(dims)
    assert decision.passed is False
    assert "Safety" in decision.blockers[0]


def test_decide_blocks_on_gating_unknown():
    dims = [GateResult(dimension="Fairness", gating=True, status=UNKNOWN, detail="llm unavailable")]
    decision = decide(dims)
    assert decision.passed is False


def test_decide_blocks_on_needs_review():
    dims = [GateResult(dimension="Fairness", gating=True, status=NEEDS_REVIEW, detail="boundary mismatch")]
    decision = decide(dims)
    assert decision.passed is False
    assert "NEEDS_REVIEW" in decision.blockers[0]


def test_maybe_recheck_boundary_skips_when_far_from_threshold():
    score, needs_review, note = maybe_recheck_boundary(
        0.95, green_min=0.8, boundary_tolerance=0.05, recheck_max_diff=0.1,
        rescore=lambda: (_ for _ in ()).throw(AssertionError("should not be called")),
    )
    assert score == 0.95
    assert needs_review is False
    assert note == ""


def test_maybe_recheck_boundary_uses_conservative_value_when_consistent():
    score, needs_review, note = maybe_recheck_boundary(
        0.82, green_min=0.8, boundary_tolerance=0.05, recheck_max_diff=0.1,
        rescore=lambda: 0.79,
    )
    assert score == 0.79
    assert needs_review is False
    assert "consistent" in note


def test_maybe_recheck_boundary_flags_needs_review_when_inconsistent():
    score, needs_review, note = maybe_recheck_boundary(
        0.82, green_min=0.8, boundary_tolerance=0.05, recheck_max_diff=0.1,
        rescore=lambda: 0.5,
    )
    assert needs_review is True
    assert "inconsistent" in note


def test_gate_run_aggregates_checks_into_decision():
    def check_ok() -> GateResult:
        return GateResult(dimension="A", gating=True, status=GREEN, detail="fine")

    def check_bad() -> GateResult:
        return GateResult(dimension="B", gating=True, status=RED, detail="broken")

    gate = Gate([
        GateCheck(name="a", gating=True, check_fn=check_ok),
        GateCheck(name="b", gating=True, check_fn=check_bad),
    ])
    decision = gate.run()
    assert decision.passed is False
    assert len(decision.dimensions) == 2


def test_gate_run_treats_exception_as_unknown():
    def broken_check() -> GateResult:
        raise RuntimeError("boom")

    gate = Gate([GateCheck(name="broken", gating=True, check_fn=broken_check)])
    decision = gate.run()
    assert decision.passed is False
    assert decision.dimensions[0].status == UNKNOWN
