"""Workflow: BFTS tree controller (Stage 1 only).

Loops:
  select_next → for each selection, ctx.step("expand_node", ...) → wait_all
  → write nodes → check terminate.

Terminate when ≥1 good_node exists (Sakana stage-1 completion rule,
agent_manager.py:434-442) OR iters_used >= max_iters.

See docs/superpowers/plans/2026-05-25-bfts-on-centaur.md (Phase 2).
"""
from __future__ import annotations

import json
import os
import random
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

if TYPE_CHECKING:
    from api.workflow_engine import WorkflowContext

from _bfts_expand import ExpandContext, expand_node
from _bfts_metric import score
from _bfts_select import NodeRef, SearchConfig, select_next
from _bfts_state import (
    insert_node,
    insert_run,
    list_nodes_for_run,
    mark_buggy_plots,
    set_best_node,
    update_node_metric,
)

WORKFLOW_NAME = "bfts_tree"


@dataclass
class Input:
    run_id: str                       # this tree's run_id (matches workflow's own run_id)
    parent_run_id: str | None         # bfts_root run that started us
    idea: dict[str, Any] = field(default_factory=dict)
    num_drafts: int = 3
    num_workers: int = 4
    max_debug_depth: int = 3
    debug_prob: float = 0.5
    max_iters: int = 20
    seed: int = 0
    sandbox_id: str = ""              # pre-provisioned by bfts_root
    openai_api_key_secret: str = "OPENAI_API_KEY"   # iron-proxy substitutes


def _parse_metric_json(raw: Any) -> dict[str, Any]:
    """Convert a DAO `metric_json` field (JSON string | dict | None) to a dict.

    list_nodes_for_run returns JSONB columns as raw JSON strings; this normalizes
    them before calling _bfts_metric.score(). Empty / malformed values fall back
    to the WORST metric so scoring stays well-defined.
    """
    if raw is None:
        return {"_worst": True}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        if not raw:
            return {"_worst": True}
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {"_worst": True}
        return parsed if isinstance(parsed, dict) else {"_worst": True}
    return {"_worst": True}


def _should_terminate(nodes: list[dict[str, Any]], iters_used: int, max_iters: int) -> bool:
    has_good = any(n.get("is_buggy") is False and n.get("is_buggy_plots") is not True for n in nodes)
    return has_good or iters_used >= max_iters


def _to_noderef(row: dict[str, Any]) -> NodeRef:
    return NodeRef(
        node_id=row["node_id"],
        parent_id=row.get("parent_node_id"),
        root_id=_root_id(row),
        is_buggy=row.get("is_buggy"),
        is_buggy_plots=row.get("is_buggy_plots"),
        debug_depth=int(row.get("debug_depth") or 0),
        metric_score=score(_parse_metric_json(row.get("metric_json"))),
        stage_name=row.get("stage_name", "draft"),
        is_leaf=True,
    )


def _root_id(row: dict[str, Any]) -> str:
    return row["node_id"] if row.get("parent_node_id") is None else (row.get("parent_node_id") or "ROOT")


async def handler(inp: Input, ctx: "WorkflowContext") -> dict[str, Any]:
    rng = random.Random(inp.seed)
    pool = ctx._pool

    await ctx.step(
        "insert_run",
        lambda: insert_run(
            pool,
            run_id=inp.run_id,
            parent_run_id=inp.parent_run_id,
            idea=inp.idea,
            config={
                "num_drafts": inp.num_drafts,
                "num_workers": inp.num_workers,
                "max_debug_depth": inp.max_debug_depth,
                "debug_prob": inp.debug_prob,
                "max_iters": inp.max_iters,
                "seed": inp.seed,
            },
            seed=inp.seed,
        ),
    )

    cfg = SearchConfig(
        num_drafts=inp.num_drafts,
        num_workers=inp.num_workers,
        max_debug_depth=inp.max_debug_depth,
        debug_prob=inp.debug_prob,
    )

    openai_api_key = os.getenv(inp.openai_api_key_secret) or ""

    iters_used = 0
    while iters_used < inp.max_iters:
        nodes = await ctx.step("list_nodes", lambda: list_nodes_for_run(pool, run_id=inp.run_id))
        if _should_terminate(nodes, iters_used, inp.max_iters):
            break

        noderefs = [_to_noderef(n) for n in nodes]
        selections = select_next(nodes=noderefs, cfg=cfg, rng=rng)

        # Insert one bfts_nodes row per selection up-front (so node_id is
        # stable across expansion sub-steps even after restart).
        prepared: list[tuple[str, NodeRef | None]] = []
        for sel in selections:
            parent_id = sel.node_id if sel is not None else None
            parent_row = next((n for n in nodes if n["node_id"] == parent_id), None) if parent_id else None
            stage = "draft" if sel is None else ("debug" if parent_row and parent_row.get("is_buggy") else "improve")
            debug_depth = 0
            if sel is not None and parent_row and parent_row.get("is_buggy"):
                debug_depth = int(parent_row.get("debug_depth") or 0) + 1

            async def _insert(parent_id=parent_id, st=stage, dd=debug_depth, used=iters_used):
                nid = uuid.uuid4().hex
                await insert_node(
                    pool,
                    node_id=nid,
                    run_id=inp.run_id,
                    parent_node_id=parent_id,
                    step=used,
                    stage_name=st,
                    plan="",
                    code="",
                    debug_depth=dd,
                )
                return nid

            node_id = await ctx.step("insert_node", _insert)
            prepared.append((node_id, sel))

        # Expand each selected node sequentially within this controller step.
        # (Intra-step fan-out via child workflows is a Phase 3+ optimization;
        # for MVP a sequential loop keeps the workflow self-contained and
        # is bounded by num_workers anyway.)
        for node_id, sel in prepared:
            parent_row = (
                next((n for n in nodes if n["node_id"] == sel.node_id), None)
                if sel is not None else None
            )
            expand_ctx = ExpandContext(
                sandbox_id=inp.sandbox_id,
                parent_node=parent_row,
                idea=inp.idea,
                openai_api_key=openai_api_key,
                node_id=node_id,
            )
            result = await expand_node(ctx=ctx, expand_ctx=expand_ctx)
            await ctx.step(
                "update_node",
                lambda nid=node_id, r=result: update_node_metric(
                    pool,
                    node_id=nid,
                    term_out=r["term_out"],
                    exec_time_seconds=r["exec_time_seconds"],
                    exc_type=r["exc_type"],
                    exc_info=r["exc_info"],
                    exc_stack=r["exc_stack"],
                    metric=r["metric"],
                    is_buggy=r["is_buggy"],
                    analysis=r["analysis"],
                    plan=r["plan"],
                    code=r["code"],
                ),
            )
            if "is_buggy_plots" in result:
                await ctx.step(
                    "mark_buggy_plots",
                    lambda nid=node_id, r=result: mark_buggy_plots(
                        pool,
                        node_id=nid,
                        is_buggy_plots=bool(r["is_buggy_plots"]),
                        plot_analyses=r.get("plot_analyses"),
                        vlm_feedback_summary=r.get("vlm_feedback_summary"),
                    ),
                )
        iters_used += 1

    final_nodes = await ctx.step(
        "list_nodes_final", lambda: list_nodes_for_run(pool, run_id=inp.run_id)
    )
    from _bfts_export import select_best, write_best_artifact   # local import keeps top tidy

    best = select_best(final_nodes)
    if best is not None:
        await ctx.step(
            "write_best_artifact",
            lambda: write_best_artifact(pool, node_id=best["node_id"], code=best["code"]),
        )
        await ctx.step(
            "set_best",
            lambda: set_best_node(pool, run_id=inp.run_id, best_node_id=best["node_id"]),
        )

    return {
        "run_id": inp.run_id,
        "iters_used": iters_used,
        "node_count": len(final_nodes),
        "best_node_id": best["node_id"] if best else None,
    }
