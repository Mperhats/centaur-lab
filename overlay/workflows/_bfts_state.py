"""DAO for bfts_runs / bfts_nodes / bfts_artifacts.

All SQL is fixed; no string interpolation. Parameters are passed as
positional asyncpg arguments. Underscore-prefixed so the workflow loader
skips it.
"""
from __future__ import annotations

import json
from typing import Any

import asyncpg


async def insert_run(
    pool: asyncpg.Pool,
    *,
    run_id: str,
    parent_run_id: str | None,
    idea: dict[str, Any],
    config: dict[str, Any],
    seed: int,
    stage_name: str = "stage_1",
) -> None:
    await pool.execute(
        """
        INSERT INTO bfts_runs (run_id, parent_run_id, idea_json, config_json,
                               stage_name, seed)
        VALUES ($1, $2, $3::jsonb, $4::jsonb, $5, $6)
        ON CONFLICT (run_id) DO NOTHING
        """,
        run_id, parent_run_id, json.dumps(idea), json.dumps(config), stage_name, seed,
    )


async def insert_node(
    pool: asyncpg.Pool,
    *,
    node_id: str,
    run_id: str,
    parent_node_id: str | None,
    step: int,
    stage_name: str,
    plan: str,
    code: str,
    debug_depth: int = 0,
) -> None:
    await pool.execute(
        """
        INSERT INTO bfts_nodes
            (node_id, run_id, parent_node_id, step, stage_name, plan, code, debug_depth)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        ON CONFLICT (node_id) DO NOTHING
        """,
        node_id, run_id, parent_node_id, step, stage_name, plan, code, debug_depth,
    )


async def update_node_metric(
    pool: asyncpg.Pool,
    *,
    node_id: str,
    term_out: list[str],
    exec_time_seconds: float,
    exc_type: str | None,
    exc_info: dict[str, Any] | None,
    exc_stack: list[Any] | None,
    metric: dict[str, Any] | None,
    is_buggy: bool,
    analysis: str | None,
    plan: str | None = None,
    code: str | None = None,
    parse_metrics_code: str | None = None,
    parse_term_out: list[str] | None = None,
    plot_code: str | None = None,
    plot_term_out: list[str] | None = None,
) -> None:
    """Write the post-execution result for a node.

    Two distinct null-handling contracts:

    - ``exc_info``, ``exc_stack``, ``metric``, ``analysis``: passing ``None``
      writes SQL ``NULL`` to the column; pass an empty container
      (``[]`` / ``{}`` / ``""``) to record empty-but-present. Callers MUST
      NOT conflate the two.
    - ``plan``, ``code``, ``parse_metrics_code``, ``plot_code``: passing
      ``None`` leaves the column unchanged (``COALESCE`` semantics) —
      useful when the caller only has the post-execution result and wants
      to preserve the value written by ``insert_node`` or an earlier
      update. Pass an explicit string to overwrite. This applies to all
      TEXT columns (both ``NOT NULL DEFAULT ''`` and nullable) so callers
      never have to special-case the buggy short-circuit, which omits
      parse-/plot-step outputs entirely.
    - ``parse_term_out``, ``plot_term_out``: JSONB columns follow the
      same SQL-``NULL``-vs-empty-list contract as ``metric`` / ``exc_info``
      — ``None`` means "no payload" (the metric-parse / plot sub-step did
      not run), ``[]`` means "ran and produced no stdout".
    """
    await pool.execute(
        """
        UPDATE bfts_nodes SET
            term_out_json = $2::jsonb,
            exec_time_seconds = $3,
            exc_type = $4,
            exc_info_json = $5::jsonb,
            exc_stack_json = $6::jsonb,
            metric_json = $7::jsonb,
            is_buggy = $8,
            analysis = $9,
            plan = COALESCE($10, plan),
            code = COALESCE($11, code),
            parse_metrics_code = COALESCE($12, parse_metrics_code),
            parse_term_out_json = $13::jsonb,
            plot_code = COALESCE($14, plot_code),
            plot_term_out_json = $15::jsonb,
            updated_at = NOW()
        WHERE node_id = $1
        """,
        node_id,
        json.dumps(term_out),
        exec_time_seconds,
        exc_type,
        json.dumps(exc_info) if exc_info is not None else None,
        json.dumps(exc_stack) if exc_stack is not None else None,
        json.dumps(metric) if metric is not None else None,
        is_buggy,
        analysis,
        plan,
        code,
        parse_metrics_code,
        json.dumps(parse_term_out) if parse_term_out is not None else None,
        plot_code,
        json.dumps(plot_term_out) if plot_term_out is not None else None,
    )


async def mark_node_failed(
    pool: asyncpg.Pool,
    *,
    node_id: str,
    exc_type: str,
    exc_info: dict[str, Any] | None,
    analysis: str,
) -> None:
    """Mark a placeholder node as buggy after its expansion workflow failed.

    Used by ``bfts_tree`` when ``wait_for_workflow`` returns a non-completed
    status (e.g. ``failed`` / ``failed_permanent`` / ``cancelled``). Fills
    in the minimum fields the selector needs:

    - ``is_buggy=True`` so ``_buggy_leaf_nodes`` sees it.
    - ``exc_type`` sentinel (typically ``"ChildWorkflowFailed"``) so
      operators can grep for orphaned-child rows.
    - ``exc_info_json`` carrying the child's status + error excerpt for
      postmortem (COALESCE preserves any pre-existing value).
    - ``analysis`` human-readable string for the dot artifact + UI.

    Closes the in-code TODO at ``bfts_tree.py:301-310`` (which would
    otherwise leave failed children as NULL placeholders that stall the
    selector's ``len(drafts) < num_drafts`` accounting).
    """
    await pool.execute(
        """
        UPDATE bfts_nodes
        SET is_buggy = TRUE,
            exc_type = $2,
            exc_info_json = COALESCE($3::jsonb, exc_info_json),
            analysis = $4,
            updated_at = NOW()
        WHERE node_id = $1
        """,
        node_id,
        exc_type,
        json.dumps(exc_info) if exc_info is not None else None,
        analysis,
    )


async def mark_buggy_plots(
    pool: asyncpg.Pool,
    *,
    node_id: str,
    is_buggy_plots: bool,
    plot_analyses: list[dict[str, Any]] | None,
    vlm_feedback_summary: str | None,
) -> None:
    await pool.execute(
        """
        UPDATE bfts_nodes SET
            is_buggy_plots = $2,
            plot_analyses_json = $3::jsonb,
            vlm_feedback_summary = $4,
            updated_at = NOW()
        WHERE node_id = $1
        """,
        node_id,
        is_buggy_plots,
        json.dumps(plot_analyses) if plot_analyses is not None else None,
        vlm_feedback_summary,
    )


async def list_recent_node_summaries(
    pool: asyncpg.Pool,
    *,
    run_id: str,
    limit: int,
    exclude_node_id: str | None,
) -> list[dict[str, Any]]:
    """Recent executed nodes for prior-attempts memory injection (F.2).

    Skips placeholder rows (``is_buggy IS NULL``) so the LLM doesn't see
    its own in-flight slots, and skips ``exclude_node_id`` so the
    current expansion never sees itself. Ordered most-recent-first;
    callers reverse if they want chronological order in the prompt.

    ``limit <= 0`` returns an empty list (caller can disable memory
    injection by setting ``prior_attempts_window=0``); we still emit
    the SQL to keep the path uniform, but with LIMIT 0.
    """
    rows = await pool.fetch(
        """
        SELECT node_id, stage_name, plan, is_buggy, analysis
        FROM bfts_nodes
        WHERE run_id = $1
          AND is_buggy IS NOT NULL
          AND ($2::text IS NULL OR node_id <> $2)
        ORDER BY created_at DESC, node_id DESC
        LIMIT $3
        """,
        run_id, exclude_node_id, max(0, int(limit)),
    )
    return [dict(r) for r in rows]


async def list_nodes_for_run(
    pool: asyncpg.Pool, *, run_id: str
) -> list[dict[str, Any]]:
    """Return all nodes for a run, ORDER BY step ASC.

    JSONB columns (``term_out_json``, ``exc_info_json``, ``exc_stack_json``,
    ``metric_json``, ``plot_analyses_json``) are returned as raw JSON
    strings — callers must ``json.loads(...)`` them. Centaur's asyncpg
    pool does not register a JSONB codec; we follow the same convention
    so the schema matches what every other Centaur DAO sees.

    ``child_count`` is computed as a correlated subquery so callers
    (``bfts_tree._to_noderef`` → ``_bfts_select._buggy_leaf_nodes``) can
    derive ``is_leaf`` accurately. The subquery is fine at BFTS scale
    (≤ ~50 nodes per run); a materialized CTE would be over-engineering.
    """
    rows = await pool.fetch(
        """
        SELECT n.node_id, n.run_id, n.parent_node_id, n.step, n.stage_name,
               n.plan, n.code, n.term_out_json, n.exec_time_seconds, n.exc_type,
               n.exc_info_json, n.exc_stack_json, n.metric_json, n.is_buggy,
               n.is_buggy_plots, n.debug_depth, n.analysis, n.vlm_feedback_summary,
               (SELECT COUNT(*) FROM bfts_nodes c
                   WHERE c.parent_node_id = n.node_id AND c.run_id = n.run_id)
                   AS child_count
        FROM bfts_nodes n
        WHERE n.run_id = $1
        ORDER BY n.step ASC
        """,
        run_id,
    )
    return [dict(r) for r in rows]


async def set_best_node(
    pool: asyncpg.Pool, *, run_id: str, best_node_id: str
) -> None:
    await pool.execute(
        "UPDATE bfts_runs SET best_node_id = $2, status = 'completed', updated_at = NOW() WHERE run_id = $1",
        run_id, best_node_id,
    )


async def fetch_best_node_for_run(
    pool: asyncpg.Pool, *, run_id: str
) -> dict[str, Any] | None:
    """Return the best node's row (``node_id``, ``plan``, ``code``) for a run.

    Resolves ``bfts_runs.best_node_id → bfts_nodes`` in one round-trip
    via a correlated subquery so callers don't have to issue two
    queries (and risk a TOCTOU between them). Returns ``None`` when the
    run has no ``best_node_id`` set — the caller decides whether that
    means "incomplete run" (fail-fast in ``gather_citations``) or
    "no good nodes yet" (silent skip in some future analytics workflow).
    """
    row = await pool.fetchrow(
        """
        SELECT n.node_id, n.plan, n.code
        FROM bfts_nodes n
        WHERE n.node_id = (
            SELECT best_node_id FROM bfts_runs WHERE run_id = $1
        )
        """,
        run_id,
    )
    return dict(row) if row is not None else None
