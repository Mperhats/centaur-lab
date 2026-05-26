"""Workflow: nightly BFTS hyperparameter reflection (Phase 4c).

Scans the most-recent N completed ``bfts_runs`` and appends one new
``bfts_hyperparams`` row that subsequent ``bfts_root`` runs will pick up
as default search-policy knobs. The v1 heuristic is intentionally coarse
— bump ``debug_prob`` by ``+0.05`` when fewer than half of recent runs
reached a good node, decay by ``-0.02`` when every run succeeded, hold
otherwise — and the other knobs (``max_debug_depth``, ``num_drafts``,
``num_workers``) round-trip the ``_bfts_config`` module defaults so a
smarter rule can replace this body without churning the table schema.

Off by default; flip on via ``BFTS_REFLECTION_ENABLED`` in
``api.extraEnv`` so a stale ``values.yaml`` never silently runs at 03:00
UTC the day after deploy.

See ``docs/superpowers/plans/2026-05-26-bfts-phase4.md`` (Phase 4c.3).
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

if TYPE_CHECKING:
    from api.workflow_engine import WorkflowContext

import asyncpg
from _bfts_config import (
    DEFAULT_DEBUG_PROB,
    DEFAULT_MAX_DEBUG_DEPTH,
    DEFAULT_METRIC_REDUCER,
    DEFAULT_NUM_DRAFTS,
    DEFAULT_NUM_WORKERS,
)
from _bfts_hyperparams import insert_hyperparams, latest_hyperparams

WORKFLOW_NAME = "bfts_reflection_nightly"


async def _load_recent_runs(
    pool: asyncpg.Pool, limit: int
) -> list[dict[str, Any]]:
    """Fetch the most-recent N completed runs as a list of dicts.

    ``ctx.step`` checkpoints its return value via JSON serialization, so the
    raw ``asyncpg.Record`` objects ``pool.fetch`` returns must be coerced to
    dicts before they leave the step. Mirrors the same pattern used in
    ``_bfts_state.list_recent_node_summaries`` / ``list_nodes_for_run``.
    """
    rows = await pool.fetch(
        """
        SELECT best_node_id
        FROM bfts_runs
        WHERE status = 'completed'
        ORDER BY updated_at DESC
        LIMIT $1
        """,
        limit,
    )
    return [dict(r) for r in rows]

# Heuristic clamps. Kept module-level so a tweak shows up in one place
# and so the test pinning the bounds doesn't redefine magic numbers.
_DEBUG_PROB_CEILING = 0.8
_DEBUG_PROB_FLOOR = 0.1
_DEBUG_PROB_BUMP = 0.05
_DEBUG_PROB_DECAY = 0.02


def _env_flag_enabled(name: str) -> bool:
    """Return True iff ``$name`` reads as a truthy boolean.

    Local to this module rather than imported from ``slack_sync_shared``:
    the overlay must not depend on Centaur ETL helpers, and inlining the
    three-string check keeps the schedule's enabled-flag self-contained.
    """
    return str(os.getenv(name, "")).strip().lower() in ("1", "true", "yes")


# NOTE: ``enabled`` only gates the scheduler's cron tick. Manual POSTs
# (e.g. the planned ``bfts-trigger-reflection`` Justfile target) always
# run ``handler`` regardless of ``BFTS_REFLECTION_ENABLED``, so an
# operator can dry-fire reflection without flipping Helm. Compare
# ``slack_sync.py:326``, which re-checks the env flag inside
# ``handler()`` for stricter gating — we deliberately omit that
# defense-in-depth check here because manual triggering is a feature,
# not a misuse.
SCHEDULE = {
    "cron": "0 3 * * *",
    "timezone": "UTC",
    "no_delivery": True,
    "enabled": _env_flag_enabled("BFTS_REFLECTION_ENABLED"),
    "catchup_policy": "skip",
}


@dataclass
class Input:
    """Per-trigger overrides for the nightly reflection.

    ``lookback_runs`` is intentionally per-run rather than env-driven:
    an operator can post a one-off ``run_input={"lookback_runs": 200}``
    for a deep retrospective without redeploying Helm.
    """

    lookback_runs: int = 50


async def handler(inp: Input, ctx: "WorkflowContext") -> dict[str, Any]:
    pool = ctx._pool

    # v1 heuristic only inspects ``best_node_id`` (truthy ⇒ "good run").
    # The plan's wider SELECT was forward-looking; pulling
    # ``idea_json`` / ``config_json`` here costs KB-MB per row and adds
    # nothing the heuristic uses. Expand at the call site when a v2
    # rule actually reads more columns. The wrapping helper converts
    # ``asyncpg.Record`` rows to dicts so ``ctx.step`` can checkpoint
    # the result; a previous direct ``pool.fetch`` here surfaced as
    # ``Object of type Record is not JSON serializable`` and the
    # workflow died 5ms in.
    recent = await ctx.step(
        "load_recent_runs",
        lambda: _load_recent_runs(pool, inp.lookback_runs),
    )

    if not recent:
        ctx.log("bfts_reflection_skipped", reason="no_completed_runs")
        return {"inserted": False}

    prev = await ctx.step("load_latest", lambda: latest_hyperparams(pool))

    debug_prob = prev["debug_prob"] if prev else DEFAULT_DEBUG_PROB
    good_count = sum(1 for r in recent if r["best_node_id"])
    # Half-rule is strict ``<`` so the exact-half case (e.g. 4 runs / 2
    # good) lands in neither branch and ``debug_prob`` holds. Mirrors
    # the plan's listing — a future smarter rule can replace this body.
    if good_count < len(recent) / 2:
        debug_prob = min(_DEBUG_PROB_CEILING, debug_prob + _DEBUG_PROB_BUMP)
    elif good_count == len(recent):
        debug_prob = max(_DEBUG_PROB_FLOOR, debug_prob - _DEBUG_PROB_DECAY)

    metric_reducer = prev["metric_reducer"] if prev else DEFAULT_METRIC_REDUCER

    await ctx.step(
        "insert_row",
        lambda: insert_hyperparams(
            pool,
            debug_prob=debug_prob,
            max_debug_depth=DEFAULT_MAX_DEBUG_DEPTH,
            num_drafts=DEFAULT_NUM_DRAFTS,
            num_workers=DEFAULT_NUM_WORKERS,
            metric_reducer=metric_reducer,
            notes=f"reflection of {len(recent)} runs; good={good_count}",
        ),
    )

    ctx.log(
        "bfts_reflection_inserted",
        debug_prob=debug_prob,
        recent_runs=len(recent),
        good_count=good_count,
    )
    return {"inserted": True, "debug_prob": debug_prob}
