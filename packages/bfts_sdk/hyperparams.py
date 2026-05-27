"""DAO for bfts_hyperparams (Phase 4c nightly reflection).

Append-only history of BFTS search-config knobs picked by the reflection
workflow. All SQL is fixed; no string interpolation. Parameters are passed
as positional asyncpg arguments. Underscore-prefixed so the workflow loader
skips it.
"""
from __future__ import annotations

from typing import Any

import asyncpg


async def latest_hyperparams(pool: asyncpg.Pool) -> dict[str, Any] | None:
    """Return the most-recent bfts_hyperparams row, or ``None`` when empty.

    The table is append-only; ``effective_from`` is both the timestamp the
    row took effect and the primary key, so ``ORDER BY effective_from DESC
    LIMIT 1`` is the canonical "current config" lookup.
    """
    row = await pool.fetchrow(
        """
        SELECT effective_from, debug_prob, max_debug_depth, num_drafts, num_workers,
               metric_reducer, notes, created_by
        FROM bfts_hyperparams
        ORDER BY effective_from DESC
        LIMIT 1
        """,
    )
    return dict(row) if row is not None else None


async def insert_hyperparams(
    pool: asyncpg.Pool,
    *,
    debug_prob: float,
    max_debug_depth: int,
    num_drafts: int,
    num_workers: int,
    metric_reducer: str,
    notes: str | None,
    created_by: str = "reflection",
) -> None:
    """Append one bfts_hyperparams row.

    ``effective_from`` is omitted from the column list so the table's
    ``DEFAULT NOW()`` populates it server-side. Validation (clamping
    ``debug_prob`` into ``[0, 1]``, requiring positive ints) is the
    caller's responsibility; the table's ``NOT NULL`` constraints are the
    final guard.
    """
    await pool.execute(
        """
        INSERT INTO bfts_hyperparams (
            debug_prob, max_debug_depth, num_drafts, num_workers,
            metric_reducer, notes, created_by
        ) VALUES ($1, $2, $3, $4, $5, $6, $7)
        """,
        debug_prob, max_debug_depth, num_drafts, num_workers,
        metric_reducer, notes, created_by,
    )
