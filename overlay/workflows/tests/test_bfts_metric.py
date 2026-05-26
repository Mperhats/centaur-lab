"""Test: _bfts_metric.mean() collapses Sakana nested-dict metrics correctly."""
from __future__ import annotations

import math
import sys
from pathlib import Path

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
