"""Best-node selection + artifact export.

Deterministic argmin(score) — NO LLM judge (Spec correction #6 in plan;
research 02 §Gotcha #6 — Sakana's LLM-as-arbiter is non-deterministic
and falls back to a different selection algorithm on error).
"""
from __future__ import annotations

import json
import uuid
from typing import Any

import asyncpg

from _bfts_metric import DEFAULT_REDUCER, ScoreResult, score


def select_best(
    nodes: list[dict[str, Any]], *, reducer: str = DEFAULT_REDUCER
) -> dict[str, Any] | None:
    """Pick best of good nodes by lowest ``score()``. Returns None if none good.

    ``reducer`` is keyword-only and defaults to ``"mean"`` so existing
    unit-test callers preserve their behavior. Phase 4g.2: the
    ``lexicographic`` reducer returns a tuple, which ``min(..., key=...)``
    still compares correctly under Python's element-wise tuple ordering.
    """
    good = [n for n in nodes if n.get("is_buggy") is False and n.get("is_buggy_plots") is not True]
    if not good:
        return None

    def _score_for(n: dict[str, Any]) -> ScoreResult:
        m = n.get("metric_json")
        if isinstance(m, str):
            try:
                m = json.loads(m)
            except json.JSONDecodeError:
                m = None
        if not isinstance(m, dict):
            m = {"_worst": True}
        return score(m, reducer=reducer)

    return min(good, key=_score_for)


async def write_best_artifact(
    pool: asyncpg.Pool, *, node_id: str, code: str
) -> str:
    """Persist the best node's code to bfts_artifacts. Returns artifact_id."""
    artifact_id = uuid.uuid4().hex
    await pool.execute(
        """
        INSERT INTO bfts_artifacts (artifact_id, node_id, kind, relative_path, bytes)
        VALUES ($1, $2, 'code', 'best_solution.py', $3)
        ON CONFLICT (node_id, relative_path) DO UPDATE SET bytes = EXCLUDED.bytes
        """,
        artifact_id, node_id, code.encode("utf-8"),
    )
    return artifact_id


async def write_best_node_id_artifact(
    pool: asyncpg.Pool, *, node_id: str
) -> str:
    """Persist a pointer artifact (`best_node_id.txt`) naming the best node.

    Mirrors `write_best_artifact`: idempotent ON CONFLICT upsert keyed by
    (node_id, relative_path). Lets downstream tooling locate the winning
    node from artifact storage alone without re-querying `bfts_runs`.
    """
    artifact_id = uuid.uuid4().hex
    await pool.execute(
        """
        INSERT INTO bfts_artifacts (artifact_id, node_id, kind, relative_path, bytes)
        VALUES ($1, $2, 'metadata', 'best_node_id.txt', $3)
        ON CONFLICT (node_id, relative_path) DO UPDATE SET bytes = EXCLUDED.bytes
        """,
        artifact_id, node_id, node_id.encode("utf-8"),
    )
    return artifact_id
