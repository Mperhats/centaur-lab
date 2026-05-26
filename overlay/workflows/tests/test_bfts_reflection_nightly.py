"""Tests for the Phase 4c nightly BFTS hyperparameter reflection workflow.

The handler scans the most recent ``bfts_runs`` rows, computes a coarse
"good vs bad" tally over their ``best_node_id`` column, and appends one
new ``bfts_hyperparams`` row that next-day runs will pick up as defaults.
These tests pin the v1 heuristic (bump ``debug_prob`` when the search is
underperforming, decay when it's perfect, hold otherwise) plus the
clamps so future smarter rules can replace the body without silently
shifting behavior.
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ._mocks import MockPool


class _ReflectionCtx:
    """Stand-in WorkflowContext for ``bfts_reflection_nightly.handler``.

    Mirrors the recording-ctx convention used by ``test_bfts_root_handler.py``
    (``_RootCtx``): ``step`` invokes its callable / coroutine just like the
    real engine would so the handler's three ``ctx.step`` sites compose
    naturally, while ``log`` accumulates structured-event payloads for
    assertion.
    """

    def __init__(self, pool: Any) -> None:
        self._pool = pool
        self.step_calls: list[str] = []
        self.logs: list[tuple[str, dict[str, Any]]] = []

    async def step(self, name: str, fn: Any) -> Any:
        self.step_calls.append(name)
        out = fn() if callable(fn) else fn
        if inspect.iscoroutine(out):
            out = await out
        return out

    def log(self, event: str, **kwargs: Any) -> None:
        self.logs.append((event, kwargs))


def _run(*, best_node_id: str | None) -> dict[str, Any]:
    """Build one synthetic ``bfts_runs`` row.

    Only ``best_node_id`` matters for the heuristic; the other columns
    are present so the handler's ``recent`` list shape matches what
    ``pool.fetch`` would return in production.
    """
    return {
        "run_id": "r",
        "idea_json": "{}",
        "config_json": "{}",
        "best_node_id": best_node_id,
        "status": "completed",
        "created_at": None,
        "updated_at": None,
    }


def _prev_row(
    *, debug_prob: float = 0.5, metric_reducer: str = "mean"
) -> dict[str, Any]:
    """Build a ``latest_hyperparams`` result row for the seed case."""
    return {
        "effective_from": None,
        "debug_prob": debug_prob,
        "max_debug_depth": 3,
        "num_drafts": 4,
        "num_workers": 2,
        "metric_reducer": metric_reducer,
        "notes": "seed",
        "created_by": "operator",
    }


def test_workflow_name_and_schedule_shape() -> None:
    import bfts_reflection_nightly as wf

    assert wf.WORKFLOW_NAME == "bfts_reflection_nightly"
    assert wf.SCHEDULE["cron"] == "0 3 * * *"
    assert wf.SCHEDULE["timezone"] == "UTC"
    assert wf.SCHEDULE["no_delivery"] is True
    assert wf.SCHEDULE["catchup_policy"] == "skip"
    # Defaults OFF — the env flag toggles the schedule on, so a stale
    # values.yaml never silently runs a reflection.
    assert wf.SCHEDULE["enabled"] is False


@pytest.mark.asyncio
async def test_handler_skips_when_no_completed_runs() -> None:
    import bfts_reflection_nightly as wf

    pool = MockPool(fetch_result=[])
    ctx = _ReflectionCtx(pool)

    result = await wf.handler(wf.Input(), ctx)

    assert result == {"inserted": False}
    assert pool.execute_calls == []  # no insert
    assert ctx.step_calls == ["load_recent_runs"]
    assert ("bfts_reflection_skipped", {"reason": "no_completed_runs"}) in ctx.logs


@pytest.mark.asyncio
async def test_handler_passes_lookback_runs_to_fetch() -> None:
    """``Input.lookback_runs`` flows into the LIMIT parameter so operators
    can override the scan window via ``run_input`` on a manual trigger."""
    import bfts_reflection_nightly as wf

    pool = MockPool(fetch_result=[])
    ctx = _ReflectionCtx(pool)

    await wf.handler(wf.Input(lookback_runs=17), ctx)

    assert pool.fetch_calls, "handler must call pool.fetch at least once"
    _query, args = pool.fetch_calls[0]
    assert args == (17,)


@pytest.mark.asyncio
async def test_handler_bumps_debug_prob_when_few_good_nodes() -> None:
    """1 / 4 good < half → +0.05 (well within the [0.1, 0.8] band)."""
    import bfts_reflection_nightly as wf

    pool = MockPool(
        fetch_result=[
            _run(best_node_id="n1"),
            _run(best_node_id=None),
            _run(best_node_id=None),
            _run(best_node_id=None),
        ],
        fetchrow_result=_prev_row(debug_prob=0.5),
    )
    ctx = _ReflectionCtx(pool)

    result = await wf.handler(wf.Input(), ctx)

    assert result == {"inserted": True, "debug_prob": pytest.approx(0.55)}
    assert len(pool.execute_calls) == 1
    _query, args = pool.execute_calls[0]
    assert args[0] == pytest.approx(0.55)


@pytest.mark.asyncio
async def test_handler_decays_debug_prob_when_all_good() -> None:
    """4 / 4 good → -0.02; debug_prob lowers."""
    import bfts_reflection_nightly as wf

    pool = MockPool(
        fetch_result=[_run(best_node_id=f"n{i}") for i in range(4)],
        fetchrow_result=_prev_row(debug_prob=0.5),
    )
    ctx = _ReflectionCtx(pool)

    result = await wf.handler(wf.Input(), ctx)

    assert result["inserted"] is True
    assert result["debug_prob"] == pytest.approx(0.48)
    _query, args = pool.execute_calls[0]
    assert args[0] == pytest.approx(0.48)


@pytest.mark.asyncio
async def test_handler_holds_debug_prob_when_half_good() -> None:
    """Exact-half (2 / 4) hits neither branch → no change."""
    import bfts_reflection_nightly as wf

    pool = MockPool(
        fetch_result=[
            _run(best_node_id="n1"),
            _run(best_node_id="n2"),
            _run(best_node_id=None),
            _run(best_node_id=None),
        ],
        fetchrow_result=_prev_row(debug_prob=0.5),
    )
    ctx = _ReflectionCtx(pool)

    result = await wf.handler(wf.Input(), ctx)

    assert result["debug_prob"] == pytest.approx(0.5)


@pytest.mark.asyncio
async def test_handler_caps_debug_prob_at_0_8() -> None:
    """Bump must clamp at 0.8 even when prev is already close to the cap."""
    import bfts_reflection_nightly as wf

    pool = MockPool(
        fetch_result=[_run(best_node_id=None) for _ in range(4)],
        fetchrow_result=_prev_row(debug_prob=0.78),
    )
    ctx = _ReflectionCtx(pool)

    result = await wf.handler(wf.Input(), ctx)

    # 0.78 + 0.05 = 0.83 → clamped to 0.8.
    assert result["debug_prob"] == pytest.approx(0.8)


@pytest.mark.asyncio
async def test_handler_floors_debug_prob_at_0_1() -> None:
    """Decay must clamp at 0.1 even when prev is already close to the floor."""
    import bfts_reflection_nightly as wf

    pool = MockPool(
        fetch_result=[_run(best_node_id=f"n{i}") for i in range(4)],
        fetchrow_result=_prev_row(debug_prob=0.11),
    )
    ctx = _ReflectionCtx(pool)

    result = await wf.handler(wf.Input(), ctx)

    # 0.11 - 0.02 = 0.09 → clamped to 0.1.
    assert result["debug_prob"] == pytest.approx(0.1)


@pytest.mark.asyncio
async def test_handler_uses_default_when_prev_is_none() -> None:
    """First-ever reflection has no prior row; baseline debug_prob is 0.5."""
    import bfts_reflection_nightly as wf

    pool = MockPool(
        fetch_result=[
            _run(best_node_id="n1"),
            _run(best_node_id="n2"),
        ],
        fetchrow_result=None,
    )
    ctx = _ReflectionCtx(pool)

    result = await wf.handler(wf.Input(), ctx)

    # 2 / 2 all good → 0.5 - 0.02 = 0.48 (starts from the 0.5 default).
    assert result["debug_prob"] == pytest.approx(0.48)


@pytest.mark.asyncio
async def test_handler_carries_metric_reducer_from_prev() -> None:
    """Reducer is not auto-tuned yet; it must round-trip from prev."""
    import bfts_reflection_nightly as wf

    pool = MockPool(
        fetch_result=[_run(best_node_id=None) for _ in range(4)],
        fetchrow_result=_prev_row(debug_prob=0.5, metric_reducer="lexicographic"),
    )
    ctx = _ReflectionCtx(pool)

    await wf.handler(wf.Input(), ctx)

    _query, args = pool.execute_calls[0]
    # args order matches _bfts_hyperparams.insert_hyperparams:
    # (debug_prob, max_debug_depth, num_drafts, num_workers,
    #  metric_reducer, notes, created_by)
    assert args[4] == "lexicographic"


@pytest.mark.asyncio
async def test_handler_falls_back_to_mean_when_prev_is_none() -> None:
    """First reflection has no reducer to carry over → default to 'mean'."""
    import bfts_reflection_nightly as wf

    pool = MockPool(
        fetch_result=[_run(best_node_id=None) for _ in range(4)],
        fetchrow_result=None,
    )
    ctx = _ReflectionCtx(pool)

    await wf.handler(wf.Input(), ctx)

    _query, args = pool.execute_calls[0]
    assert args[4] == "mean"


@pytest.mark.asyncio
async def test_handler_includes_notes_with_run_count_and_good_count() -> None:
    """The ``notes`` column records the heuristic's inputs (recent / good
    counts) so operators reading bfts_hyperparams know which run window
    produced each row."""
    import bfts_reflection_nightly as wf

    pool = MockPool(
        fetch_result=[
            _run(best_node_id="n1"),
            _run(best_node_id=None),
            _run(best_node_id=None),
        ],
        fetchrow_result=_prev_row(),
    )
    ctx = _ReflectionCtx(pool)

    await wf.handler(wf.Input(), ctx)

    _query, args = pool.execute_calls[0]
    notes = args[5]
    assert "reflection of 3 runs" in notes
    assert "good=1" in notes


@pytest.mark.asyncio
async def test_handler_inserts_module_default_constants() -> None:
    """v1 heuristic only tweaks debug_prob; max_debug_depth / num_drafts
    / num_workers always come from ``_bfts_config`` defaults so a future
    smarter rule (or operator-edited DEFAULT_*) is the single knob."""
    import bfts_reflection_nightly as wf
    from _bfts_config import (
        DEFAULT_MAX_DEBUG_DEPTH,
        DEFAULT_NUM_DRAFTS,
        DEFAULT_NUM_WORKERS,
    )

    pool = MockPool(
        fetch_result=[_run(best_node_id=None) for _ in range(4)],
        fetchrow_result=_prev_row(),
    )
    ctx = _ReflectionCtx(pool)

    await wf.handler(wf.Input(), ctx)

    _query, args = pool.execute_calls[0]
    assert args[1] == DEFAULT_MAX_DEBUG_DEPTH
    assert args[2] == DEFAULT_NUM_DRAFTS
    assert args[3] == DEFAULT_NUM_WORKERS


@pytest.mark.asyncio
async def test_handler_step_names_match_replay_contract() -> None:
    """Step names must be stable across deploys so workflow replay can
    map cached step rows back to handler call sites. Pin them here."""
    import bfts_reflection_nightly as wf

    pool = MockPool(
        fetch_result=[
            _run(best_node_id="n1"),
            _run(best_node_id=None),
        ],
        fetchrow_result=_prev_row(),
    )
    ctx = _ReflectionCtx(pool)

    await wf.handler(wf.Input(), ctx)

    assert ctx.step_calls == ["load_recent_runs", "load_latest", "insert_row"]


@pytest.mark.asyncio
async def test_handler_emits_inserted_log_on_success() -> None:
    """The structured event must include the values an operator would
    grep dashboards for: the new debug_prob and both counts."""
    import bfts_reflection_nightly as wf

    pool = MockPool(
        fetch_result=[
            _run(best_node_id="n1"),
            _run(best_node_id=None),
            _run(best_node_id=None),
            _run(best_node_id=None),
        ],
        fetchrow_result=_prev_row(debug_prob=0.5),
    )
    ctx = _ReflectionCtx(pool)

    await wf.handler(wf.Input(), ctx)

    inserted = [kw for ev, kw in ctx.logs if ev == "bfts_reflection_inserted"]
    assert len(inserted) == 1
    assert inserted[0]["debug_prob"] == pytest.approx(0.55)
    assert inserted[0]["recent_runs"] == 4
    assert inserted[0]["good_count"] == 1
