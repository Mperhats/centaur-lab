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

from _bfts_metric import score


def select_best(nodes: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Pick best of good nodes by lowest score(). Returns None if none good."""
    good = [n for n in nodes if n.get("is_buggy") is False and n.get("is_buggy_plots") is not True]
    if not good:
        return None

    def _score_for(n: dict[str, Any]) -> float:
        m = n.get("metric_json")
        if isinstance(m, str):
            try:
                m = json.loads(m)
            except json.JSONDecodeError:
                m = None
        if not isinstance(m, dict):
            m = {"_worst": True}
        return score(m)

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
