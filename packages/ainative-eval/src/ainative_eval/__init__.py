"""ainative-eval —— FCARS风格治理Gate、独立多评判聚合。"""

from __future__ import annotations

from ainative_eval.fairness import (
    FairnessDimensionScore,
    FairnessResult,
    detect_stereotype_skew,
    evaluate_fairness,
    has_dominant_skew,
)
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
from ainative_eval.gdpr import AuditRecord, DataSubjectRightsService, InMemoryAuditSink, ResourceCleaner
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
    "AuditRecord",
    "DataSubjectRightsService",
    "FairnessDimensionScore",
    "FairnessResult",
    "Gate",
    "InMemoryAuditSink",
    "ResourceCleaner",
    "aggregate_scores",
    "decide",
    "detect_stereotype_skew",
    "evaluate_fairness",
    "has_dominant_skew",
    "maybe_recheck_boundary",
    "status_from_score",
]
