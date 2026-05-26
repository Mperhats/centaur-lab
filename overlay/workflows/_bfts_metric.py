"""Pure-Python MetricValue helpers (Sakana nested-dict shape).

KNOWN FOOTGUN (deferred fix tracked in Phase 4): the direction of
comparison is taken from the FIRST metric's ``lower_is_better`` and
applied to all metrics + datasets. A node returning
``[val_loss↓, val_acc↑]`` will compare accuracy as if lower were better.
Mirrored 1:1 from .scientist/ai_scientist/treesearch/utils/metric.py
:191-203 to preserve Sakana behavior at the MVP boundary; research 02
§Tree data model and §Gotcha #7.

Underscore-prefixed module name so the API's workflow loader skips it
(research 03 §Tool programming model).
"""
from __future__ import annotations

from typing import Any


WORST_METRIC: dict[str, Any] = {"_worst": True}
"""Sentinel that compares worse than any real metric.

Assigned on any failure path (buggy exec, metric-parse failure, plot
failure). Equivalent to Sakana's WorstMetricValue
(.scientist/ai_scientist/treesearch/utils/metric.py:327-341)."""


def is_worst(metric: dict[str, Any] | None) -> bool:
    return metric is None or bool(metric.get("_worst"))


def direction_lower_is_better(metric: dict[str, Any]) -> bool:
    names = metric.get("metric_names") or []
    if not names:
        return True
    return bool(names[0].get("lower_is_better", True))


def mean(metric: dict[str, Any]) -> float:
    """Mean of all final_values across all metrics and datasets.

    Returns +inf for is_worst() so argmax(-mean) never picks it.
    """
    if is_worst(metric):
        return float("inf")
    values: list[float] = []
    for entry in metric.get("metric_names") or []:
        for ds in entry.get("data") or []:
            v = ds.get("final_value")
            if isinstance(v, (int, float)):
                values.append(float(v))
    if not values:
        return float("inf")
    return sum(values) / len(values)


def score(metric: dict[str, Any]) -> float:
    """Sortable score: lower is better => return mean; higher is better => return -mean.

    The best node is the one with the LOWEST score(); use argmin.
    """
    if is_worst(metric):
        return float("inf")
    m = mean(metric)
    return m if direction_lower_is_better(metric) else -m
