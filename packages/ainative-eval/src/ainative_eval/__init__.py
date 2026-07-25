"""ainative-eval —— FCARS风格治理Gate、独立多评判聚合。"""

from __future__ import annotations

from ainative_eval.gate import (
    GREEN,
    NEEDS_REVIEW,
    RED,
    SKIPPED,
    UNKNOWN,
    YELLOW,
    Gate,
    decide,
    maybe_recheck_boundary,
    status_from_score,
)
from ainative_eval.judge_aggregation import AggregatedJudgment, aggregate_scores

__version__ = "0.1.0"

__all__ = [
    "GREEN",
    "NEEDS_REVIEW",
    "RED",
    "SKIPPED",
    "UNKNOWN",
    "YELLOW",
    "AggregatedJudgment",
    "Gate",
    "aggregate_scores",
    "decide",
    "maybe_recheck_boundary",
    "status_from_score",
]
