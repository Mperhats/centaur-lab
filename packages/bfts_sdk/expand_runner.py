"""Shared node-expansion runner for ``bfts_expand_one`` and inline ``bfts_tree``.

Phase 5a runs the same pipeline inside the tree workflow (namespaced
``ctx.step`` checkpoints) instead of fanning out child workflow runs.
"""
from __future__ import annotations

from typing import Any

from packages.bfts_sdk.config import DEFAULT_PRIOR_ATTEMPTS_WINDOW
from packages.bfts_sdk.expand import ExpandContext, expand_node
from packages.bfts_sdk.state import (
    list_recent_node_summaries,
    mark_buggy_plots,
    update_node_metric,
)


async def run_expand_for_node(
    ctx: Any,
    pool: Any,
    *,
    run_id: str,
    node_id: str,
    sandbox_id: str,
    working_dir: str,
    parent_node: dict[str, Any] | None,
    idea: dict[str, Any],
    llm_api_key: str,
    draft_model: str,
    feedback_model: str,
    vlm_model: str,
    prior_attempts_window: int | None,
    seed_override: int | None = None,
    inline: bool = False,
) -> dict[str, Any]:
    """Load memory, run ``expand_node``, persist ``bfts_nodes`` updates.

    When ``inline=True`` (Phase 5a), outer step names and
    ``ExpandContext.step_prefix`` are namespaced by ``node_id[:8]`` so
    parallel siblings inside one ``bfts_tree`` run do not collide in
    ``workflow_checkpoints``.
    """
    node_id8 = node_id[:8]
    if inline:
        load_step = f"load_prior_{node_id8}"
        update_step = f"update_node_{node_id8}"
        plots_step = f"mark_buggy_plots_{node_id8}"
        expand_step_prefix = f"expand_{node_id8}_"
    else:
        load_step = "load_prior_attempts"
        update_step = "update_node"
        plots_step = "mark_buggy_plots"
        expand_step_prefix = ""

    window = (
        prior_attempts_window
        if prior_attempts_window is not None
        else DEFAULT_PRIOR_ATTEMPTS_WINDOW
    )
    prior_attempts = await ctx.step(
        load_step,
        lambda: list_recent_node_summaries(
            pool,
            run_id=run_id,
            limit=window,
            exclude_node_id=node_id,
        ),
    )

    expand_ctx = ExpandContext(
        sandbox_id=sandbox_id,
        parent_node=parent_node,
        idea=idea,
        llm_api_key=llm_api_key,
        node_id=node_id,
        working_dir=working_dir,
        draft_model=draft_model,
        feedback_model=feedback_model,
        vlm_model=vlm_model,
        prior_attempts=prior_attempts or [],
        seed_override=seed_override,
        step_prefix=expand_step_prefix,
    )

    result = await expand_node(ctx=ctx, expand_ctx=expand_ctx)

    await ctx.step(
        update_step,
        lambda: update_node_metric(
            pool,
            node_id=node_id,
            term_out=result["term_out"],
            exec_time_seconds=result["exec_time_seconds"],
            exc_type=result["exc_type"],
            exc_info=result["exc_info"],
            exc_stack=result["exc_stack"],
            metric=result["metric"],
            is_buggy=result["is_buggy"],
            analysis=result["analysis"],
            plan=result["plan"],
            code=result["code"],
            parse_metrics_code=result.get("parse_metrics_code"),
            parse_term_out=result.get("parse_term_out"),
            plot_code=result.get("plot_code"),
            plot_term_out=result.get("plot_term_out"),
        ),
    )
    if "is_buggy_plots" in result:
        await ctx.step(
            plots_step,
            lambda: mark_buggy_plots(
                pool,
                node_id=node_id,
                is_buggy_plots=bool(result["is_buggy_plots"]),
                plot_analyses=result.get("plot_analyses"),
                vlm_feedback_summary=result.get("vlm_feedback_summary"),
            ),
        )

    return {
        "node_id": node_id,
        "is_buggy": bool(result["is_buggy"]),
        "stage_name": result.get("stage_name"),
    }
