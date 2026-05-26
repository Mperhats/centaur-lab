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


_DOT_COLOR_BY_STATE: dict[str, str] = {
    "best":         "gold",
    "good":         "green",
    "buggy":        "red",
    "buggy_plots":  "yellow",
    "pending":      "lightgray",
}
"""Legend palette for ``render_tree_dot``. ``best`` overrides
``good``; ``buggy`` overrides ``buggy_plots`` (a node can't be both
"good code with bad plots" AND "buggy code", but if the data is
inconsistent code-buggy wins because that's the harder signal)."""


def _node_color(node: dict[str, Any], best_node_id: str | None) -> str:
    """Resolve a node's dot fillcolor from its is_buggy / is_buggy_plots
    state. ``best_node_id`` (if provided and matching) wins regardless
    of the underlying flags so the operator sees the selector's
    final pick immediately."""
    if best_node_id and node.get("node_id") == best_node_id:
        return _DOT_COLOR_BY_STATE["best"]
    if node.get("is_buggy") is True:
        return _DOT_COLOR_BY_STATE["buggy"]
    if node.get("is_buggy_plots") is True:
        return _DOT_COLOR_BY_STATE["buggy_plots"]
    if node.get("is_buggy") is False:
        return _DOT_COLOR_BY_STATE["good"]
    return _DOT_COLOR_BY_STATE["pending"]


def _metric_score_for_label(node: dict[str, Any]) -> str:
    """Extract a one-line metric label for the dot node. ``metric_json``
    can arrive as a string (asyncpg JSONB return) or a dict; tolerate
    both. Returns an empty string when no usable number is found so the
    label stays compact for pending/buggy nodes."""
    m = node.get("metric_json")
    if isinstance(m, str):
        try:
            m = json.loads(m)
        except json.JSONDecodeError:
            return ""
    if not isinstance(m, dict):
        return ""
    final = m.get("final_value")
    if isinstance(final, (int, float)):
        return f"\\n{final:.4g}"
    # Newer schema nests under metric_names[*].data[*].final_value.
    names = m.get("metric_names")
    if isinstance(names, list) and names:
        first = names[0]
        if isinstance(first, dict):
            data = first.get("data")
            if isinstance(data, list) and data:
                v = data[0].get("final_value") if isinstance(data[0], dict) else None
                if isinstance(v, (int, float)):
                    return f"\\n{v:.4g}"
    return ""


def render_tree_dot(
    nodes: list[dict[str, Any]],
    *,
    run_id: str,
    best_node_id: str | None,
) -> str:
    """Render a ``bfts_run`` as GraphViz dot text.

    No external dependencies. Output is self-contained and can be piped
    to ``dot -Tpng tree.dot -o tree.png`` by operators or pasted into
    any online dot viewer. Color legend embedded as a subgraph so the
    graph is readable standalone.

    Why dot rather than the upstream HTML: porting Sakana's
    ``tree_export.py`` (484 LOC + ``python-igraph`` + a JS template)
    is disproportionate to the operator-debugging win. A
    self-contained dot artifact captures node states, parent-child
    edges, and metric scores; future upgrade to interactive HTML is
    additive (read this artifact + render).
    """
    # Sanitize the run_id for the digraph identifier (dot allows
    # [A-Za-z_][A-Za-z0-9_]* unquoted; ``wfr_<hex>:tree:0`` contains
    # ``:`` which would break parsing).
    safe_run = "".join(c if c.isalnum() or c == "_" else "_" for c in run_id)
    lines = [
        f"digraph BFTS_{safe_run} {{",
        "  rankdir=TB;",
        '  node [style=filled, shape=box, fontname="Helvetica"];',
    ]
    for n in nodes:
        nid = n["node_id"]
        color = _node_color(n, best_node_id)
        score_label = _metric_score_for_label(n)
        label = (
            f"{nid[:8]}\\n[{n.get('stage_name','?')}]{score_label}"
        )
        lines.append(
            f'  "{nid}" [label="{label}", fillcolor="{color}"];'
        )
    for n in nodes:
        parent = n.get("parent_node_id")
        if parent:
            lines.append(f'  "{parent}" -> "{n["node_id"]}";')
    lines.append("  // legend")
    lines.append("  subgraph cluster_legend {")
    lines.append('    label="legend"; style=dashed;')
    for state, color in _DOT_COLOR_BY_STATE.items():
        lines.append(
            f'    "legend_{state}" [label="{state}", fillcolor="{color}"];'
        )
    lines.append("  }")
    lines.append("}")
    return "\n".join(lines) + "\n"


async def write_tree_dot_artifact(
    pool: asyncpg.Pool,
    *,
    run_id: str,
    dot_text: str,
    anchor_node_id: str,
) -> str:
    """Persist ``tree.dot`` under the anchor (best or first) node.

    Uses the same ``bfts_artifacts`` upsert path as
    ``write_best_artifact``; ``relative_path = 'tree.dot'``,
    ``kind = 'tree_viz'``. The ``run_id`` is folded into the
    ``artifact_id`` so re-runs of the same tree (Centaur replay)
    overwrite the previous dot rather than colliding.
    """
    artifact_id = f"{run_id}:tree.dot"
    await pool.execute(
        """
        INSERT INTO bfts_artifacts (artifact_id, node_id, kind, relative_path, bytes)
        VALUES ($1, $2, 'tree_viz', 'tree.dot', $3)
        ON CONFLICT (node_id, relative_path) DO UPDATE SET bytes = EXCLUDED.bytes
        """,
        artifact_id, anchor_node_id, dot_text.encode("utf-8"),
    )
    return artifact_id


async def write_references_artifact(
    pool: asyncpg.Pool, *, node_id: str, bibtex: str
) -> str:
    """Persist the gathered BibTeX (`references.bib`) for the best node.

    Idempotent ON CONFLICT upsert keyed by (node_id, relative_path) so
    re-running ``gather_citations`` for the same best node overwrites
    the previous BibTeX rather than colliding on the unique constraint
    or orphaning a stale row. Empty ``bibtex`` is allowed (and
    intentional in the no-claims / no-papers case): writing an empty
    artifact lets a downstream writeup workflow detect "we tried, no
    citations found" via byte length without needing to distinguish
    "missing artifact" from "explicit empty".
    """
    artifact_id = uuid.uuid4().hex
    await pool.execute(
        """
        INSERT INTO bfts_artifacts (artifact_id, node_id, kind, relative_path, bytes)
        VALUES ($1, $2, 'references', 'references.bib', $3)
        ON CONFLICT (node_id, relative_path) DO UPDATE SET bytes = EXCLUDED.bytes
        """,
        artifact_id, node_id, bibtex.encode("utf-8"),
    )
    return artifact_id
