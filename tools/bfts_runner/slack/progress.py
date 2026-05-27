"""Live BFTS tree-search snapshots for Slack stream updates."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import asyncpg

_TERMINAL_WORKFLOW_STATUSES = frozenset({"completed", "failed", "cancelled"})


@dataclass(frozen=True)
class TreeSearchSnapshot:
    workflow_status: str
    node_count: int
    max_step: int
    buggy_count: int
    good_count: int


async def fetch_workflow_status(pool: asyncpg.Pool, *, run_id: str) -> dict[str, Any]:
    """Return the workflow_runs row for ``run_id`` (status + output when terminal)."""
    row = await pool.fetchrow(
        "SELECT run_id, workflow_name, status, output_json, error_json "
        "FROM workflow_runs WHERE run_id = $1",
        run_id,
    )
    if row is None:
        return {"run_id": run_id, "status": "missing"}
    return dict(row)


async def fetch_tree_search_snapshot(
    pool: asyncpg.Pool, *, tree_run_id: str
) -> TreeSearchSnapshot:
    """Aggregate ``bfts_nodes`` progress for one ``bfts_tree`` run."""
    wf = await fetch_workflow_status(pool, run_id=tree_run_id)
    stats = await pool.fetchrow(
        """
        SELECT
            COUNT(*)::int AS node_count,
            COALESCE(MAX(step), -1)::int AS max_step,
            COUNT(*) FILTER (WHERE is_buggy IS TRUE)::int AS buggy_count,
            COUNT(*) FILTER (
                WHERE is_buggy IS FALSE AND is_buggy_plots IS NOT TRUE
            )::int AS good_count
        FROM bfts_nodes
        WHERE run_id = $1
        """,
        tree_run_id,
    )
    return TreeSearchSnapshot(
        workflow_status=str(wf.get("status") or "unknown"),
        node_count=int(stats["node_count"] if stats else 0),
        max_step=int(stats["max_step"] if stats else -1),
        buggy_count=int(stats["buggy_count"] if stats else 0),
        good_count=int(stats["good_count"] if stats else 0),
    )


def format_tree_search_snapshot(
    *,
    tree_index: int,
    tree_run_id: str,
    snapshot: TreeSearchSnapshot,
) -> str:
    """One Slack markdown block for an in-flight tree search poll."""
    step_label = snapshot.max_step + 1 if snapshot.max_step >= 0 else 0
    lines = [
        f"**Tree {tree_index}** `{tree_run_id}` — {snapshot.workflow_status}",
        (
            f"nodes: {snapshot.node_count} · step: {step_label} · "
            f"good: {snapshot.good_count} · buggy: {snapshot.buggy_count}"
        ),
    ]
    if snapshot.workflow_status in _TERMINAL_WORKFLOW_STATUSES:
        if snapshot.good_count:
            lines.append("search found at least one good node")
        elif snapshot.buggy_count and not snapshot.good_count:
            lines.append("no good nodes yet — expand failures may indicate proxy timeouts")
    return "\n".join(lines)
