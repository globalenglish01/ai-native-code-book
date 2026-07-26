from __future__ import annotations

import pytest
from ainative_eval.fairness import (
    FairnessDimensionScore,
    detect_stereotype_skew,
    evaluate_fairness,
    has_dominant_skew,
)


def test_evaluate_fairness_returns_minimum_not_average():
    """The core design requirement: one weak dimension must not be masked
    by other strong dimensions via averaging."""
    scores = [
        FairnessDimensionScore("japanese", 0.95),
        FairnessDimensionScore("chinese", 0.92),
        FairnessDimensionScore("english", 0.50),
    ]
    result = evaluate_fairness(scores)
    assert result.parity_min == 0.50
    assert result.weakest_dimension == "english"


def test_evaluate_fairness_dimension_scores_includes_all_dimensions():
    scores = [FairnessDimensionScore("a", 0.9), FairnessDimensionScore("b", 0.8)]
    result = evaluate_fairness(scores)
    assert result.dimension_scores == {"a": 0.9, "b": 0.8}


def test_evaluate_fairness_raises_on_empty_scores():
    with pytest.raises(ValueError, match="at least one"):
        evaluate_fairness([])


def test_evaluate_fairness_single_dimension_is_its_own_minimum():
    result = evaluate_fairness([FairnessDimensionScore("only", 0.75)])
    assert result.parity_min == 0.75
    assert result.weakest_dimension == "only"


def test_detect_stereotype_skew_returns_distribution_ratios():
    samples = [{"gender": "female"}] * 8 + [{"gender": "male"}] * 2
    distribution = detect_stereotype_skew(samples, attribute_key="gender")
    assert distribution == {"female": 0.8, "male": 0.2}


def test_detect_stereotype_skew_returns_empty_for_no_samples():
    assert detect_stereotype_skew([], attribute_key="gender") == {}


def test_detect_stereotype_skew_missing_attribute_key_is_bucketed_as_empty_string():
    samples = [{"other_field": "x"}, {"gender": "female"}]
    distribution = detect_stereotype_skew(samples, attribute_key="gender")
    assert distribution[""] == 0.5
    assert distribution["female"] == 0.5


def test_has_dominant_skew_true_when_one_value_exceeds_threshold():
    assert has_dominant_skew({"female": 0.8, "male": 0.2}, max_dominant_share=0.7) is True


def test_has_dominant_skew_false_for_balanced_distribution():
    assert has_dominant_skew({"female": 0.5, "male": 0.5}, max_dominant_share=0.7) is False


def test_has_dominant_skew_false_for_empty_distribution():
    assert has_dominant_skew({}, max_dominant_share=0.7) is False
