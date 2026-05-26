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
) -> None:
    """Write the post-execution result for a node.

    Nullable params (``exc_info``, ``exc_stack``, ``metric``,
    ``analysis``, ``plan``, ``code``): pass ``None`` to leave the column
    unchanged (= absent update); pass an empty container (``[]`` / ``{}`` /
    ``""``) to record empty-but-present. Callers MUST NOT conflate the two.
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
            -- parse_* / plot_* / plot_code intentionally NOT updated here:
            -- they land in a separate update from Task 3.x once the
            -- metric-parse sub-step and plot exec sub-step are in scope.
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


async def list_nodes_for_run(
    pool: asyncpg.Pool, *, run_id: str
) -> list[dict[str, Any]]:
    """Return all nodes for a run, ORDER BY step ASC.

    JSONB columns (``term_out_json``, ``exc_info_json``, ``exc_stack_json``,
    ``metric_json``, ``plot_analyses_json``) are returned as raw JSON
    strings — callers must ``json.loads(...)`` them. Centaur's asyncpg
    pool does not register a JSONB codec; we follow the same convention
    so the schema matches what every other Centaur DAO sees.
    """
    rows = await pool.fetch(
        """
        SELECT node_id, run_id, parent_node_id, step, stage_name, plan, code,
               term_out_json, exec_time_seconds, exc_type, exc_info_json,
               exc_stack_json, metric_json, is_buggy, is_buggy_plots, debug_depth,
               analysis, vlm_feedback_summary
        FROM bfts_nodes
        WHERE run_id = $1
        ORDER BY step ASC
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
