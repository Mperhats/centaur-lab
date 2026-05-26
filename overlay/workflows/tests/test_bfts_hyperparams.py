"""Unit tests for `_bfts_hyperparams` DAO (Phase 4c).

These tests use the shared ``MockPool`` (``tests._mocks``) which records
every ``fetchrow``/``execute`` call so we can assert the exact SQL string
and positional argument list passed to asyncpg. The integration round-trip
against a real database lives separately under ``tests/integration/``.
"""
from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _bfts_hyperparams import insert_hyperparams, latest_hyperparams

from ._mocks import MockPool


@pytest.mark.asyncio
async def test_latest_hyperparams_returns_none_when_empty() -> None:
    pool = MockPool(fetchrow_result=None)

    result = await latest_hyperparams(pool)

    assert result is None
    assert len(pool.fetchrow_calls) == 1
    query, args = pool.fetchrow_calls[0]
    assert args == ()
    assert "bfts_hyperparams" in query
    assert "ORDER BY effective_from DESC" in query
    assert "LIMIT 1" in query


@pytest.mark.asyncio
async def test_latest_hyperparams_returns_row_as_dict() -> None:
    row = {
        "effective_from": datetime(2026, 5, 26, 12, 0, tzinfo=UTC),
        "debug_prob": 0.5,
        "max_debug_depth": 3,
        "num_drafts": 5,
        "num_workers": 3,
        "metric_reducer": "mean",
        "notes": "first config",
        "created_by": "reflection",
    }
    pool = MockPool(fetchrow_result=row)

    result = await latest_hyperparams(pool)

    assert result == row
    assert isinstance(result, dict)


@pytest.mark.asyncio
async def test_latest_hyperparams_selects_all_columns() -> None:
    """The SELECT must surface every column the table defines so the
    reflection workflow has full context for its next-config decision."""
    pool = MockPool(fetchrow_result=None)

    await latest_hyperparams(pool)

    query, _args = pool.fetchrow_calls[0]
    for column in (
        "effective_from",
        "debug_prob",
        "max_debug_depth",
        "num_drafts",
        "num_workers",
        "metric_reducer",
        "notes",
        "created_by",
    ):
        assert column in query, f"latest_hyperparams SELECT missing {column}"


@pytest.mark.asyncio
async def test_insert_hyperparams_writes_one_row_with_all_values() -> None:
    pool = MockPool()

    await insert_hyperparams(
        pool,
        debug_prob=0.3,
        max_debug_depth=4,
        num_drafts=7,
        num_workers=2,
        metric_reducer="median",
        notes="bumped drafts after high buggy rate",
        created_by="operator",
    )

    assert len(pool.execute_calls) == 1
    query, args = pool.execute_calls[0]
    assert args == (0.3, 4, 7, 2, "median", "bumped drafts after high buggy rate", "operator")
    assert "INSERT INTO bfts_hyperparams" in query
    # effective_from must NOT be in the column list — it defaults to NOW().
    assert "effective_from" not in query


@pytest.mark.asyncio
async def test_insert_hyperparams_uses_default_created_by_reflection() -> None:
    pool = MockPool()

    await insert_hyperparams(
        pool,
        debug_prob=0.5,
        max_debug_depth=3,
        num_drafts=5,
        num_workers=3,
        metric_reducer="mean",
        notes=None,
    )

    _query, args = pool.execute_calls[0]
    assert args[-1] == "reflection"


@pytest.mark.asyncio
async def test_insert_hyperparams_accepts_notes_none() -> None:
    pool = MockPool()

    await insert_hyperparams(
        pool,
        debug_prob=0.5,
        max_debug_depth=3,
        num_drafts=5,
        num_workers=3,
        metric_reducer="mean",
        notes=None,
    )

    _query, args = pool.execute_calls[0]
    # notes is the 6th positional arg (index 5).
    assert args[5] is None


@pytest.mark.asyncio
async def test_insert_hyperparams_column_order_matches_values() -> None:
    """Sanity check: the column list in the INSERT matches the positional
    arg order the function passes, so callers can't silently swap fields."""
    pool = MockPool()

    await insert_hyperparams(
        pool,
        debug_prob=0.1,
        max_debug_depth=2,
        num_drafts=4,
        num_workers=1,
        metric_reducer="mean",
        notes="x",
    )

    query, args = pool.execute_calls[0]
    # Column list appears in this order; values must follow the same order.
    expected_order = [
        "debug_prob",
        "max_debug_depth",
        "num_drafts",
        "num_workers",
        "metric_reducer",
        "notes",
        "created_by",
    ]
    last_idx = -1
    for column in expected_order:
        idx = query.find(column)
        assert idx != -1, f"INSERT missing column {column}"
        assert idx > last_idx, f"INSERT column order wrong at {column}"
        last_idx = idx
    assert args == (0.1, 2, 4, 1, "mean", "x", "reflection")
