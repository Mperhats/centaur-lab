"""Pure-Python MetricValue helpers (Sakana nested-dict shape).

KNOWN FOOTGUN (preserved intentionally for the ``mean`` / ``min`` /
``weighted_mean`` reducers): the direction of comparison is taken from
the FIRST metric's ``lower_is_better`` and applied to all metrics +
datasets. A node returning ``[val_loss↓, val_acc↑]`` will compare
accuracy as if lower were better. Mirrored 1:1 from
.scientist/ai_scientist/treesearch/utils/metric.py:191-203 to preserve
Sakana behavior at the MVP boundary; research 02 §Tree data model and
§Gotcha #7.

The ``lexicographic`` reducer (Phase 4g.2) is the only collapse that
honors each metric's OWN ``lower_is_better`` flag, because tuple
comparison naturally requires per-component direction.

Underscore-prefixed module name so the API's workflow loader skips it
(research 03 §Tool programming model).
"""
from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Any, Union


WORST_METRIC: Mapping[str, Any] = MappingProxyType({"_worst": True})
"""Sentinel that compares worse than any real metric.

Read-only mapping; mutating it would corrupt downstream `is_worst()` checks.
Assigned on any failure path (buggy exec, metric-parse failure, plot
failure). Equivalent to Sakana's WorstMetricValue
(.scientist/ai_scientist/treesearch/utils/metric.py:327-341)."""


REDUCERS: tuple[str, ...] = ("mean", "min", "weighted_mean", "lexicographic")
DEFAULT_REDUCER: str = "mean"


# Return type covers scalar reducers (float) plus the lexicographic
# reducer which emits a tuple. Both are total-orderable by Python's
# built-in comparison so existing argmin call sites (sorted(), min())
# keep working without an explicit encode/decode step.
ScoreResult = Union[float, tuple[float, ...]]


def is_worst(metric: Mapping[str, Any] | None) -> bool:
    return metric is None or bool(metric.get("_worst"))


def direction_lower_is_better(metric: Mapping[str, Any]) -> bool:
    names = metric.get("metric_names") or []
    if not names:
        return True
    return bool(names[0].get("lower_is_better", True))


def mean(metric: Mapping[str, Any]) -> float:
    """Mean of all final_values across all metrics and datasets.

    Returns +inf for is_worst() so argmax(-mean) never picks it.
    """
    if is_worst(metric):
        return float("inf")
    values = _all_final_values(metric)
    if not values:
        return float("inf")
    return sum(values) / len(values)


def score(
    metric: Mapping[str, Any], *, reducer: str = DEFAULT_REDUCER
) -> ScoreResult:
    """Collapse a per-dataset metric vector into a sortable score.

    Reducers (Phase 4g.2):

    - ``mean`` (default, unchanged from Phase 0–3): average of all
      ``final_value`` entries across every metric and dataset. The
      first metric's ``lower_is_better`` flag governs sign for the
      whole node (preserves Sakana's first-metric direction footgun;
      see module docstring).
    - ``min``: best single dataset value across all metrics + datasets.
      Under the lower-is-better convention this is ``min(values)``;
      under higher-is-better it is ``-max(values)`` so the selector's
      ``min(score)`` still picks the highest measurement.
    - ``weighted_mean``: per-metric weights from ``metric["weights"]``
      (parallel to ``metric["metric_names"]``). Each metric's average
      is computed first, then weighted-averaged across metrics. Missing
      / wrong-length / non-numeric weights silently fall back to a
      uniform per-metric mean — the resolver / operator owns weight
      hygiene, the reducer must not crash a tree run over bad config.
    - ``lexicographic``: returns a ``tuple[float, ...]`` of per-metric
      averages, sorted alphabetically by ``metric_name`` and
      sign-flipped per-component for higher-is-better metrics. Python's
      tuple comparison gives natural lexicographic argmin without
      needing a bespoke encoding.

    The best node is always the one with the LOWEST ``score()``
    (selectors / exporter use ``min``); WORST_METRIC always sorts
    strictly after any real result.
    """
    if reducer not in REDUCERS:
        raise ValueError(
            f"unknown reducer: {reducer!r} (valid: {', '.join(REDUCERS)})"
        )

    if reducer == "lexicographic":
        return _score_lexicographic(metric)

    if is_worst(metric):
        return float("inf")

    if reducer == "mean":
        m = mean(metric)
    elif reducer == "min":
        m = _scalar_min(metric)
    else:  # weighted_mean
        m = _weighted_mean(metric)

    if m == float("inf"):
        return m
    return m if direction_lower_is_better(metric) else -m


def _all_final_values(metric: Mapping[str, Any]) -> list[float]:
    values: list[float] = []
    for entry in metric.get("metric_names") or []:
        for ds in entry.get("data") or []:
            v = ds.get("final_value")
            if isinstance(v, (int, float)):
                values.append(float(v))
    return values


def _per_metric_means(
    metric: Mapping[str, Any],
) -> list[tuple[dict[str, Any], float]]:
    """Return ``(entry, mean_value)`` for every metric with ≥1 numeric value.

    Carries the raw entry alongside its collapsed mean so callers
    (``lexicographic``) can look up the per-metric ``lower_is_better``
    flag without re-iterating.
    """
    out: list[tuple[dict[str, Any], float]] = []
    for entry in metric.get("metric_names") or []:
        vals = [
            float(ds.get("final_value"))
            for ds in entry.get("data") or []
            if isinstance(ds.get("final_value"), (int, float))
        ]
        if vals:
            out.append((entry, sum(vals) / len(vals)))
    return out


def _scalar_min(metric: Mapping[str, Any]) -> float:
    values = _all_final_values(metric)
    if not values:
        return float("inf")
    # Direction is applied by the outer ``score`` wrapper (sign flip
    # for higher-is-better). Under that convention, picking the
    # globally-best dataset means min() when lower-is-better and
    # max() when higher-is-better — so we always return the value
    # the wrapper will negate-or-not.
    if direction_lower_is_better(metric):
        return min(values)
    return max(values)


def _weighted_mean(metric: Mapping[str, Any]) -> float:
    pairs = _per_metric_means(metric)
    if not pairs:
        return float("inf")

    raw_weights = metric.get("weights")
    weights: list[float] | None = None
    entries = metric.get("metric_names") or []
    if (
        isinstance(raw_weights, list)
        and len(raw_weights) == len(entries)
        and all(isinstance(w, (int, float)) for w in raw_weights)
    ):
        # Realign weights to the entries that actually contributed a
        # mean (entries with no numeric data are dropped by
        # _per_metric_means but their slot in `weights` would
        # otherwise misalign the dot product).
        index_of: dict[int, float] = {
            idx: float(w) for idx, w in enumerate(raw_weights)
        }
        weights = []
        for idx, entry in enumerate(entries):
            if any(e is entry for e, _ in pairs):
                weights.append(index_of[idx])

    means = [m for _, m in pairs]
    if weights is None or len(weights) != len(means):
        return sum(means) / len(means)

    total = sum(weights)
    if total == 0:
        return sum(means) / len(means)
    return sum(v * w for v, w in zip(means, weights)) / total


def _score_lexicographic(metric: Mapping[str, Any]) -> tuple[float, ...]:
    """Per-metric collapsed tuple sorted by ``metric_name``.

    Each component is sign-flipped if its own ``lower_is_better`` is
    false. ``WORST_METRIC`` (or any metric with zero usable values)
    collapses to a single ``+inf`` tuple — strictly greater than any
    real tuple under Python's element-wise comparison, so the selector
    / exporter never picks it.
    """
    if is_worst(metric):
        return (float("inf"),)
    pairs = _per_metric_means(metric)
    if not pairs:
        return (float("inf"),)

    ordered = sorted(pairs, key=lambda ep: ep[0].get("metric_name", ""))
    comps: list[float] = []
    for entry, avg in ordered:
        lib = bool(entry.get("lower_is_better", True))
        comps.append(avg if lib else -avg)
    return tuple(comps)
