from __future__ import annotations

import pytest

from ainative_eval.judge_aggregation import aggregate_scores


def test_aggregate_scores_returns_none_for_empty_list():
    assert aggregate_scores([]) is None


def test_aggregate_scores_median_and_low_uncertainty():
    result = aggregate_scores([0.8, 0.9, 0.7])
    assert result.score == 0.8
    assert result.score_range == pytest.approx(0.2)
    assert result.high_uncertainty is False
    assert result.sample_size == 3


def test_aggregate_scores_flags_high_uncertainty_on_wide_spread():
    result = aggregate_scores([0.1, 0.9, 0.5])
    assert result.high_uncertainty is True


def test_aggregate_scores_single_value_has_zero_range():
    result = aggregate_scores([0.75])
    assert result.score == 0.75
    assert result.score_range == 0.0
    assert result.high_uncertainty is False


def test_aggregate_scores_respects_custom_threshold():
    result = aggregate_scores([0.5, 0.6], high_uncertainty_threshold=0.05)
    assert result.high_uncertainty is True
