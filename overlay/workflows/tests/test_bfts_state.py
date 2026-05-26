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

from _bfts_state import fetch_best_node_for_run, update_node_metric

from ._mocks import MockPool as FakePool


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
