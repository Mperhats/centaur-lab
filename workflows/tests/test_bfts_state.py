"""Unit tests for `_bfts_state.update_node_metric` SQL parameter wiring.

These tests use the shared ``FakePool`` (``tests._fakes``) which records every
``execute(query, *args)`` call so we can assert the exact positional argument
list passed to asyncpg. The integration round-trip against a real database
lives in ``tests/integration/test_bfts_state.py``.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _bfts_state import (
    fetch_best_node_for_run,
    insert_node,
    list_recent_node_summaries,
    list_seed_children,
    mark_node_failed,
    mark_run_completed,
    mark_run_failed,
    set_best_node,
    update_node_aggregate_metric,
    update_node_metric,
)

from workflows.tests._mocks import MockPool as FakePool


@pytest.mark.asyncio
async def test_update_node_metric_writes_parse_and_plot_fields() -> None:
    """parse_metrics_code / parse_term_out / plot_code / plot_term_out all
    reach the SQL parameter list when the caller passes non-None values."""
    pool = FakePool()

    await update_node_metric(
        pool,
        node_id="node-1",
        term_out=["hi\n"],
        exec_time_seconds=0.1,
        exc_type=None,
        exc_info=None,
        exc_stack=None,
        metric={"loss": 0.5},
        is_buggy=False,
        analysis="ok",
        plan="plan-x",
        code="print(1)",
        parse_metrics_code="x",
        parse_term_out=["a", "b"],
        plot_code="y",
        plot_term_out=["c"],
    )

    assert len(pool.execute_calls) == 1
    query, args = pool.execute_calls[0]

    # The four new fields must appear somewhere in the positional args.
    assert "x" in args, "parse_metrics_code not passed to SQL"
    assert "y" in args, "plot_code not passed to SQL"
    assert json.dumps(["a", "b"]) in args, "parse_term_out not JSON-encoded into SQL args"
    assert json.dumps(["c"]) in args, "plot_term_out not JSON-encoded into SQL args"

    # SQL must reference the four columns so the values land in the right place.
    assert "parse_metrics_code" in query
    assert "parse_term_out_json" in query
    assert "plot_code" in query
    assert "plot_term_out_json" in query


@pytest.mark.asyncio
async def test_update_node_metric_parse_text_uses_coalesce_when_none() -> None:
    """Passing parse_metrics_code=None must NOT overwrite the column
    (mirrors the existing plan/code COALESCE semantics for TEXT columns)."""
    pool = FakePool()

    await update_node_metric(
        pool,
        node_id="node-2",
        term_out=[],
        exec_time_seconds=0.0,
        exc_type=None,
        exc_info=None,
        exc_stack=None,
        metric=None,
        is_buggy=True,
        analysis=None,
    )

    query, args = pool.execute_calls[0]
    # COALESCE($N, parse_metrics_code) is the contract for the TEXT NOT NULL column.
    assert "COALESCE" in query.upper()
    assert "parse_metrics_code" in query
    assert "plot_code" in query
    # Defaults — caller omitted parse_metrics_code / plot_code — must arrive as None.
    assert None in args


@pytest.mark.asyncio
async def test_update_node_metric_jsonb_fields_pass_none_as_sql_null() -> None:
    """parse_term_out / plot_term_out None → SQL NULL (no JSON encoding).

    Matches the metric / exc_info JSONB pattern: None means "no payload",
    distinct from an empty list "[]" which means "executed but produced
    no output".
    """
    pool = FakePool()

    await update_node_metric(
        pool,
        node_id="node-3",
        term_out=["hi\n"],
        exec_time_seconds=0.0,
        exc_type=None,
        exc_info=None,
        exc_stack=None,
        metric=None,
        is_buggy=True,
        analysis=None,
        parse_term_out=None,
        plot_term_out=None,
    )

    _query, args = pool.execute_calls[0]
    # The JSON-encoded empty list MUST NOT appear; None must be passed straight through.
    assert "[]" not in args


@pytest.mark.asyncio
async def test_update_node_metric_jsonb_fields_distinguish_empty_from_none() -> None:
    """Empty list parse_term_out=[] / plot_term_out=[] → JSON "[]"; not SQL NULL."""
    pool = FakePool()

    await update_node_metric(
        pool,
        node_id="node-4",
        term_out=["hi\n"],
        exec_time_seconds=0.0,
        exc_type=None,
        exc_info=None,
        exc_stack=None,
        metric=None,
        is_buggy=False,
        analysis=None,
        parse_term_out=[],
        plot_term_out=[],
    )

    _query, args = pool.execute_calls[0]
    # "[]" appears twice (once per JSONB column).
    assert args.count("[]") == 2


@pytest.mark.asyncio
async def test_update_node_metric_backward_compatible_signature() -> None:
    """All four new params are optional; existing callers that omit them
    still succeed and produce one SQL execute."""
    pool = FakePool()

    await update_node_metric(
        pool,
        node_id="node-5",
        term_out=["hi\n"],
        exec_time_seconds=0.05,
        exc_type=None,
        exc_info=None,
        exc_stack=None,
        metric={"loss": 0.1},
        is_buggy=False,
        analysis="ok",
    )

    assert len(pool.execute_calls) == 1


# --- Phase 4d.3: fetch_best_node_for_run --------------------------------
#
# Single-row lookup that ``gather_citations`` calls before fanning out
# its claim-extraction LLM call. Resolves the join (best_node_id IN
# bfts_runs → node row IN bfts_nodes) in one round-trip via a
# correlated subquery; returns ``None`` when the run has no
# ``best_node_id`` set (incomplete tree, every expansion was buggy).


@pytest.mark.asyncio
async def test_fetch_best_node_for_run_returns_row_when_set() -> None:
    """Happy path: the run has a best_node_id; the DAO returns a dict
    with at least ``node_id``, ``plan``, ``code``."""
    pool = FakePool(
        fetchrow_result={
            "node_id": "best-1",
            "plan": "the plan",
            "code": "print('x')",
        }
    )

    out = await fetch_best_node_for_run(pool, run_id="run-1")

    assert out == {"node_id": "best-1", "plan": "the plan", "code": "print('x')"}
    assert len(pool.fetchrow_calls) == 1
    query, args = pool.fetchrow_calls[0]
    assert "bfts_runs" in query
    assert "bfts_nodes" in query
    assert args == ("run-1",)


@pytest.mark.asyncio
async def test_fetch_best_node_for_run_returns_none_when_unset() -> None:
    """Incomplete run: ``best_node_id`` is NULL → fetchrow → None →
    DAO returns None. Caller fails fast on None instead of swallowing
    the empty result."""
    pool = FakePool(fetchrow_result=None)

    out = await fetch_best_node_for_run(pool, run_id="run-incomplete")

    assert out is None


# ---------------------------------------------------------------------------
# F.1: mark_node_failed marks a placeholder buggy after child workflow fails.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mark_node_failed_emits_update_with_synthetic_fields() -> None:
    """One UPDATE that sets ``is_buggy=TRUE``, fills ``exc_type`` /
    ``exc_info_json`` (json-encoded dict) / ``analysis``, and bumps
    ``updated_at``. The COALESCE on ``exc_info_json`` preserves any
    pre-existing value the executor might have written."""
    pool = FakePool()

    await mark_node_failed(
        pool,
        node_id="n1",
        exc_type="ChildWorkflowFailed",
        exc_info={"child_status": "failed", "error": "executor pod evicted"},
        analysis="bfts_expand_one terminated with status=failed",
    )

    assert len(pool.execute_calls) == 1
    query, args = pool.execute_calls[0]
    assert "UPDATE bfts_nodes" in query
    assert "is_buggy = TRUE" in query
    assert "COALESCE($3::jsonb, exc_info_json)" in query
    # Positional args: (node_id, exc_type, exc_info_json (json string), analysis).
    assert args[0] == "n1"
    assert args[1] == "ChildWorkflowFailed"
    assert json.loads(args[2]) == {
        "child_status": "failed",
        "error": "executor pod evicted",
    }
    assert args[3] == "bfts_expand_one terminated with status=failed"


@pytest.mark.asyncio
async def test_mark_node_failed_handles_none_exc_info() -> None:
    """``exc_info=None`` → SQL NULL (no JSON ``"null"`` literal). The
    COALESCE keeps any pre-existing exc_info_json the executor wrote."""
    pool = FakePool()

    await mark_node_failed(
        pool,
        node_id="n2",
        exc_type="ChildWorkflowFailed",
        exc_info=None,
        analysis="no detail available",
    )

    _query, args = pool.execute_calls[0]
    assert args[2] is None


# ---------------------------------------------------------------------------
# F.2: list_recent_node_summaries (DAO contract).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_recent_node_summaries_filters_and_limits() -> None:
    """SQL excludes placeholder rows (``is_buggy IS NOT NULL``), excludes
    the current node, orders most-recent-first, and forwards ``limit`` as
    the SQL ``LIMIT $3`` parameter."""
    expected_rows = [
        {"node_id": "n2", "stage_name": "draft", "plan": "p2",
         "is_buggy": False, "analysis": "ran clean"},
        {"node_id": "n1", "stage_name": "draft", "plan": "p1",
         "is_buggy": True, "analysis": "syntax error"},
    ]
    pool = FakePool(fetch_result=expected_rows)

    out = await list_recent_node_summaries(
        pool, run_id="r1", limit=2, exclude_node_id="n3",
    )

    assert out == expected_rows
    assert len(pool.fetch_calls) == 1
    query, args = pool.fetch_calls[0]
    assert "is_buggy IS NOT NULL" in query
    assert "node_id <> $2" in query
    assert "ORDER BY created_at DESC" in query
    assert args == ("r1", "n3", 2)


@pytest.mark.asyncio
async def test_list_recent_node_summaries_clamps_negative_limit() -> None:
    """``limit <= 0`` clamps to 0 so callers can disable memory injection
    by setting ``prior_attempts_window=0`` without separate branching."""
    pool = FakePool(fetch_result=[])

    await list_recent_node_summaries(
        pool, run_id="r1", limit=-5, exclude_node_id=None,
    )

    _query, args = pool.fetch_calls[0]
    assert args == ("r1", None, 0)


# ---------------------------------------------------------------------------
# F.4: insert_node forwards seed columns; list_seed_children DAO;
# update_node_aggregate_metric merges into metric_json.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_insert_node_forwards_seed_columns_by_default_false() -> None:
    """Phase 0-4 callers that omit ``is_seed_node`` / ``seed`` still produce a
    valid INSERT — the schema default ``FALSE`` / NULL applies."""
    pool = FakePool()

    await insert_node(
        pool,
        node_id="n1",
        run_id="r1",
        parent_node_id=None,
        step=0,
        stage_name="draft",
        plan="",
        code="",
    )

    _query, args = pool.execute_calls[0]
    # The DAO passes positional args in declared order:
    # (node_id, run_id, parent_node_id, step, stage_name,
    #  plan, code, debug_depth, is_seed_node, seed).
    assert args == ("n1", "r1", None, 0, "draft", "", "", 0, False, None)


@pytest.mark.asyncio
async def test_insert_node_passes_through_seed_args() -> None:
    """F.4 callers set ``is_seed_node=True`` + ``seed=K``; the values reach
    the SQL parameter list."""
    pool = FakePool()

    await insert_node(
        pool,
        node_id="seed-0",
        run_id="r1",
        parent_node_id="parent",
        step=99000,
        stage_name="seed",
        plan="seed re-eval 0",
        code="print(1)",
        is_seed_node=True,
        seed=0,
    )

    _query, args = pool.execute_calls[0]
    assert args[-2:] == (True, 0)


@pytest.mark.asyncio
async def test_list_seed_children_filters_by_parent_and_seed_flag() -> None:
    """SQL filters ``parent_node_id`` AND ``is_seed_node = TRUE`` and orders
    by ``seed`` so callers can iterate deterministically by seed index."""
    rows = [
        {"node_id": "s0", "seed": 0, "metric_json": None, "is_buggy": False},
        {"node_id": "s1", "seed": 1, "metric_json": None, "is_buggy": False},
    ]
    pool = FakePool(fetch_result=rows)

    out = await list_seed_children(pool, parent_node_id="best-1")

    assert out == rows
    query, args = pool.fetch_calls[0]
    assert "is_seed_node = TRUE" in query
    assert "ORDER BY seed" in query
    assert args == ("best-1",)


@pytest.mark.asyncio
async def test_update_node_aggregate_metric_merges_via_jsonb_concat() -> None:
    """The UPDATE uses ``COALESCE(metric_json, '{}'::jsonb) || $2::jsonb``
    so an aggregate sub-dict is shallow-merged into the existing metric.
    JSON-encoded dict reaches the parameter list."""
    pool = FakePool()

    await update_node_aggregate_metric(
        pool,
        node_id="best-1",
        aggregate={"aggregate_mean": 0.32, "aggregate_std": 0.04, "aggregate_n": 3.0},
    )

    query, args = pool.execute_calls[0]
    assert "metric_json = COALESCE(metric_json, '{}'::jsonb) || $2::jsonb" in query
    assert args[0] == "best-1"
    parsed = json.loads(args[1])
    assert parsed["aggregate_mean"] == pytest.approx(0.32)
    assert parsed["aggregate_n"] == pytest.approx(3.0)


@pytest.mark.asyncio
async def test_set_best_node_writes_only_best_node_id_and_updated_at() -> None:
    """``set_best_node`` no longer flips ``status``. We assert the UPDATE
    touches ``best_node_id`` + ``updated_at`` and explicitly DOES NOT
    include ``status = '...'`` so the new ``mark_run_completed`` writer
    is the single source of truth for the running -> completed
    transition (covers the all-buggy-tree case)."""
    pool = FakePool()

    await set_best_node(pool, run_id="run-1", best_node_id="node-7")

    query, args = pool.execute_calls[0]
    assert "best_node_id = $2" in query
    assert "updated_at = NOW()" in query
    assert "status" not in query, (
        "set_best_node must not write status anymore; "
        "mark_run_completed owns that transition"
    )
    assert args == ("run-1", "node-7")


@pytest.mark.asyncio
async def test_mark_run_completed_updates_status_with_run_id_only() -> None:
    """Idempotent ``status -> 'completed'`` transition keyed on
    ``run_id``. Single positional arg, no payload, since the value is
    hard-coded into the UPDATE statement."""
    pool = FakePool()

    await mark_run_completed(pool, run_id="run-42")

    assert len(pool.execute_calls) == 1
    query, args = pool.execute_calls[0]
    assert "UPDATE bfts_runs" in query
    assert "status = 'completed'" in query
    assert "WHERE run_id = $1" in query
    assert args == ("run-42",)


@pytest.mark.asyncio
async def test_mark_run_failed_updates_status_with_run_id_only() -> None:
    """Reconciliation-only writer for ``status -> 'failed'``. Same
    contract shape as ``mark_run_completed`` so the orphan-sweep tool
    can call either based on the engine's terminal status."""
    pool = FakePool()

    await mark_run_failed(pool, run_id="run-99")

    query, args = pool.execute_calls[0]
    assert "UPDATE bfts_runs" in query
    assert "status = 'failed'" in query
    assert "WHERE run_id = $1" in query
    assert args == ("run-99",)
