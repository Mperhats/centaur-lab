"""Test: _bfts_metric.mean() collapses Sakana nested-dict metrics correctly."""
from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _bfts_metric import (
    WORST_METRIC,
    direction_lower_is_better,
    is_worst,
    mean,
    score,
)


def test_mean_single_metric_single_dataset() -> None:
    m = {
        "metric_names": [
            {
                "metric_name": "val_loss",
                "lower_is_better": True,
                "description": "validation loss",
                "data": [{"dataset_name": "ds", "final_value": 0.4, "best_value": 0.3}],
            }
        ]
    }
    assert mean(m) == 0.4


def test_mean_collapses_across_datasets() -> None:
    m = {
        "metric_names": [
            {
                "metric_name": "val_loss",
                "lower_is_better": True,
                "description": "",
                "data": [
                    {"dataset_name": "ds1", "final_value": 0.2, "best_value": 0.1},
                    {"dataset_name": "ds2", "final_value": 0.4, "best_value": 0.3},
                ],
            }
        ]
    }
    assert math.isclose(mean(m), 0.3)


def test_direction_taken_from_first_metric_only() -> None:
    """Known footgun (research 02 §MetricValue): first metric's
    lower_is_better governs comparison across ALL metrics."""
    m = {
        "metric_names": [
            {"metric_name": "val_loss", "lower_is_better": True, "description": "", "data": []},
            {"metric_name": "val_acc", "lower_is_better": False, "description": "", "data": []},
        ]
    }
    assert direction_lower_is_better(m) is True


def test_worst_metric_compares_worst() -> None:
    real = {"metric_names": [{"metric_name": "x", "lower_is_better": True, "description": "", "data": [{"dataset_name": "d", "final_value": 0.5, "best_value": 0.5}]}]}
    assert is_worst(WORST_METRIC)
    assert not is_worst(real)


def test_score_lower_is_better_returns_mean() -> None:
    m = {
        "metric_names": [
            {
                "metric_name": "val_loss",
                "lower_is_better": True,
                "description": "",
                "data": [{"dataset_name": "ds", "final_value": 0.4, "best_value": 0.3}],
            }
        ]
    }
    assert score(m) == 0.4


def test_score_higher_is_better_flips_sign() -> None:
    m = {
        "metric_names": [
            {
                "metric_name": "val_acc",
                "lower_is_better": False,
                "description": "",
                "data": [{"dataset_name": "ds", "final_value": 0.9, "best_value": 0.95}],
            }
        ]
    }
    assert score(m) == -0.9


def test_score_worst_metric_is_positive_inf() -> None:
    assert score(WORST_METRIC) == float("inf")


# --- Configurable reducer tests (Phase 4g.2) ----------------------------
#
# `score(metric, *, reducer=...)` collapses a per-dataset metric vector
# into a single comparable scalar (or tuple, for lexicographic). The
# default reducer remains "mean" so unmodified call sites preserve the
# Phase 0–3 behavior exactly.

def _two_metric(
    *,
    a_values: list[float],
    b_values: list[float],
    a_lib: bool = True,
    b_lib: bool = True,
    weights: list[float] | None = None,
) -> dict:
    """Two-metric fixture (`a` and `b`), one dataset per metric.

    `a_lib` / `b_lib` set per-metric `lower_is_better`. `weights` (if
    given) attaches a `metric["weights"]` parallel list for
    `weighted_mean`.
    """
    m = {
        "metric_names": [
            {
                "metric_name": "a",
                "lower_is_better": a_lib,
                "description": "",
                "data": [
                    {"dataset_name": f"ds{i}", "final_value": v, "best_value": v}
                    for i, v in enumerate(a_values)
                ],
            },
            {
                "metric_name": "b",
                "lower_is_better": b_lib,
                "description": "",
                "data": [
                    {"dataset_name": f"ds{i}", "final_value": v, "best_value": v}
                    for i, v in enumerate(b_values)
                ],
            },
        ],
    }
    if weights is not None:
        m["weights"] = weights
    return m


def test_score_default_reducer_is_mean() -> None:
    """Default reducer preserves Phase 0–3 behavior exactly."""
    m = _two_metric(a_values=[0.2, 0.4], b_values=[0.6, 0.8])
    # mean of all final_values = (0.2+0.4+0.6+0.8) / 4 = 0.5
    assert score(m) == score(m, reducer="mean")
    assert math.isclose(score(m), 0.5)


def test_score_reducer_min_lower_is_better_returns_min_value() -> None:
    """Under the lower-is-better convention, ``min`` returns the best
    (lowest) dataset value across all metrics + datasets."""
    m = _two_metric(a_values=[0.2, 0.4], b_values=[0.6, 0.8])
    assert score(m, reducer="min") == 0.2


def test_score_reducer_min_higher_is_better_flips_sign() -> None:
    """When higher-is-better, ``min`` returns -max(values) so the best
    (highest) dataset still produces the lowest score."""
    m = _two_metric(
        a_values=[0.2, 0.4], b_values=[0.6, 0.9], a_lib=False, b_lib=False
    )
    assert score(m, reducer="min") == -0.9


def test_score_reducer_weighted_mean_with_weights_picks_best_node() -> None:
    """``weighted_mean`` per-metric weights from ``metric['weights']``."""
    # node X: a=0.2, b=0.8 (per-metric means)  → 0.2*0.9 + 0.8*0.1 = 0.26
    x = _two_metric(a_values=[0.2], b_values=[0.8], weights=[0.9, 0.1])
    # node Y: a=0.5, b=0.5 → 0.5*0.9 + 0.5*0.1 = 0.5
    y = _two_metric(a_values=[0.5], b_values=[0.5], weights=[0.9, 0.1])
    assert score(x, reducer="weighted_mean") < score(y, reducer="weighted_mean")
    assert math.isclose(score(x, reducer="weighted_mean"), 0.26)


def test_score_reducer_weighted_mean_uniform_when_no_weights() -> None:
    """No ``weights`` field falls back to uniform per-metric average."""
    m = _two_metric(a_values=[0.2], b_values=[0.8])
    # per-metric means are [0.2, 0.8]; uniform mean = 0.5
    assert math.isclose(score(m, reducer="weighted_mean"), 0.5)


def test_score_reducer_weighted_mean_mismatched_weights_falls_back_to_uniform() -> None:
    """Wrong-length ``weights`` falls back to uniform (don't crash)."""
    m = _two_metric(a_values=[0.2], b_values=[0.8], weights=[1.0])  # len mismatch
    assert math.isclose(score(m, reducer="weighted_mean"), 0.5)


def test_score_reducer_lexicographic_returns_tuple() -> None:
    """``lexicographic`` returns a tuple, sorted by metric_name, with each
    component sign-flipped per its OWN ``lower_is_better`` flag (so
    Python's tuple-min picks the multi-objective best)."""
    m = _two_metric(a_values=[0.3], b_values=[0.7])
    result = score(m, reducer="lexicographic")
    assert isinstance(result, tuple)
    # alphabetical: a then b; both lower_is_better → unflipped
    assert result == (0.3, 0.7)


def test_score_reducer_lexicographic_picks_first_metric_as_primary() -> None:
    """Lexicographic comparison: smaller first-metric wins regardless of
    second-metric values."""
    # x's primary metric (alphabetical 'a') is better than y's, even
    # though y's secondary metric 'b' is better.
    x = _two_metric(a_values=[0.1], b_values=[0.9])
    y = _two_metric(a_values=[0.3], b_values=[0.1])
    assert score(x, reducer="lexicographic") < score(y, reducer="lexicographic")


def test_score_reducer_lexicographic_respects_per_metric_direction() -> None:
    """Per-metric ``lower_is_better=False`` flips the sign for that
    component only — so a higher value becomes a lower score."""
    # 'a' is higher-is-better: bigger a → smaller component
    x = _two_metric(a_values=[0.9], b_values=[0.1], a_lib=False)
    y = _two_metric(a_values=[0.5], b_values=[0.1], a_lib=False)
    # x has higher a (0.9) so component = -0.9 < -0.5 = y's component
    assert score(x, reducer="lexicographic") < score(y, reducer="lexicographic")


def test_score_reducer_lexicographic_worst_metric_returns_inf_tuple() -> None:
    """is_worst → tuple sentinel that sorts after any real tuple."""
    result = score(WORST_METRIC, reducer="lexicographic")
    assert isinstance(result, tuple)
    assert all(v == float("inf") for v in result)
    # Sorts after any finite tuple (Python tuple comparison).
    assert result > (1e9, 1e9)


def test_score_reducer_unknown_raises() -> None:
    """Unknown reducer name surfaces a clear ValueError."""
    m = _two_metric(a_values=[0.1], b_values=[0.2])
    with pytest.raises(ValueError, match="reducer"):
        score(m, reducer="not_a_reducer")


def test_score_all_reducers_agree_worst_is_max_score() -> None:
    """Every reducer must put WORST_METRIC strictly worse than any real
    metric so the selector / exporter never picks a buggy node."""
    real = _two_metric(a_values=[0.5], b_values=[0.5])
    for reducer in ("mean", "min", "weighted_mean"):
        assert score(WORST_METRIC, reducer=reducer) > score(real, reducer=reducer)
    # Lexicographic uses tuple comparison.
    assert score(WORST_METRIC, reducer="lexicographic") > score(
        real, reducer="lexicographic"
    )


def test_score_mean_empty_values_higher_is_better_returns_inf() -> None:
    """Phase 4g.2 quietly corrected a Phase 0-3 latent bug: an empty-data
    metric with lower_is_better=False used to collapse to -inf and be
    picked as best. Lock the corrected behavior so we don't regress."""
    m = {
        "metric_names": [
            {
                "metric_name": "x",
                "lower_is_better": False,
                "description": "",
                "data": [],
            }
        ]
    }
    assert score(m, reducer="mean") == float("inf")
