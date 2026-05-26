"""Workflow: BFTS tree controller (Stage 1 only).

Loops:
  select_next → insert placeholder rows → fan out
  ``bfts_expand_one`` children (one per selection, ``eager_start=True``)
  → wait for every child → re-query DB → check terminate.

Terminate when ≥1 good_node exists (Sakana stage-1 completion rule,
agent_manager.py:434-442) OR iters_used >= max_iters.

See docs/superpowers/plans/2026-05-25-bfts-on-centaur.md (Phase 2) and
docs/superpowers/plans/2026-05-26-bfts-phase4.md (Phase 4h: fan-out).
"""
from __future__ import annotations

import json
import random
import sys
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

if TYPE_CHECKING:
    from api.workflow_engine import WorkflowContext

from _bfts_config import (
    DEFAULT_METRIC_REDUCER,
    resolve_llm_settings,
    resolve_search_settings,
)
from _bfts_metric import score
from _bfts_select import NodeRef, SearchConfig, select_next
from _bfts_state import (
    insert_node,
    insert_run,
    list_nodes_for_run,
    set_best_node,
)

WORKFLOW_NAME = "bfts_tree"


@dataclass
class Input:
    run_id: str                       # this tree's run_id (matches workflow's own run_id)
    parent_run_id: str | None         # bfts_root run that started us
    idea: dict[str, Any] = field(default_factory=dict)
    # Search-policy fields default to None so the resolver chain
    # (Input → BFTS_* env → module default) reaches lower tiers even
    # when bfts_tree is started standalone (e.g. tests/debugging). The
    # parent bfts_root forwards already-resolved values here so the
    # tree's resolve_search_settings call is effectively a passthrough
    # on the happy path; the tree intentionally does NOT re-read the
    # bfts_hyperparams DB row (the parent owns that layer).
    num_drafts: int | None = None
    num_workers: int | None = None
    max_debug_depth: int | None = None
    debug_prob: float | None = None
    max_iters: int = 20
    seed: int = 0
    sandbox_id: str = ""              # pre-provisioned by bfts_root
    # Optional per-run overrides; bfts_root passes resolved values. When
    # bfts_tree is started directly, deployment env (BFTS_*) applies.
    llm_api_key_secret: str | None = None
    draft_model: str | None = None
    feedback_model: str | None = None
    vlm_model: str | None = None
    # Reducer for _bfts_metric.score (Phase 4g.2). bfts_root passes the
    # resolved value here so each child tree scores nodes identically;
    # left as None when bfts_tree starts standalone, deployment env
    # then applies via resolve_search_settings.
    metric_reducer: str | None = None


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


def _to_noderef(
    row: dict[str, Any], *, reducer: str = DEFAULT_METRIC_REDUCER
) -> NodeRef:
    # ``child_count`` comes from the correlated subquery in
    # ``_bfts_state.list_nodes_for_run``. Missing key (older callers /
    # test fixtures) defaults to 0 → ``is_leaf=True``, matching the
    # pre-fix behavior for rows the DAO didn't populate.
    #
    # ``reducer`` defaults to "mean" so unit tests and any pre-Phase-4g
    # caller that doesn't pass it preserve the original score signature.
    return NodeRef(
        node_id=row["node_id"],
        parent_id=row.get("parent_node_id"),
        root_id=_root_id(row),
        is_buggy=row.get("is_buggy"),
        is_buggy_plots=row.get("is_buggy_plots"),
        debug_depth=int(row.get("debug_depth") or 0),
        metric_score=score(
            _parse_metric_json(row.get("metric_json")), reducer=reducer
        ),
        stage_name=row.get("stage_name", "draft"),
        is_leaf=(int(row.get("child_count") or 0) == 0),
    )


def _root_id(row: dict[str, Any]) -> str:
    return row["node_id"] if row.get("parent_node_id") is None else (row.get("parent_node_id") or "ROOT")


async def handler(inp: Input, ctx: WorkflowContext) -> dict[str, Any]:
    llm = resolve_llm_settings(
        draft_model=inp.draft_model,
        feedback_model=inp.feedback_model,
        vlm_model=inp.vlm_model,
        llm_api_key_secret=inp.llm_api_key_secret,
    )
    # Phase 4c.4: tree resolves all five search-policy fields through
    # the sync (no-DB) resolver. On the happy path bfts_root has
    # already forwarded resolved values via Input, so this is a
    # passthrough; for standalone tree invocations the env/default
    # tier still applies. The DB layer lives on bfts_root only —
    # re-reading bfts_hyperparams here would risk siblings disagreeing
    # if the table was updated mid-run. ``sources`` records which tier
    # won each field; persisted into bfts_runs.config_json so an
    # operator postmortem can reconstruct the run's provenance.
    search, sources = resolve_search_settings(
        debug_prob=inp.debug_prob,
        max_debug_depth=inp.max_debug_depth,
        num_drafts=inp.num_drafts,
        num_workers=inp.num_workers,
        metric_reducer=inp.metric_reducer,
    )

    rng = random.Random(inp.seed)
    pool = ctx._pool

    await ctx.step(
        "insert_run",
        lambda: insert_run(
            pool,
            run_id=inp.run_id,
            parent_run_id=inp.parent_run_id,
            idea=inp.idea,
            # Persist the resolved snapshot so replay reproduces the
            # exact run config even if bfts_hyperparams / env changes
            # between runs (Phase 4c.4 contract). ``sources`` answers
            # the postmortem question "which tier set this value?".
            config={
                "num_drafts": search.num_drafts,
                "num_workers": search.num_workers,
                "max_debug_depth": search.max_debug_depth,
                "debug_prob": search.debug_prob,
                "max_iters": inp.max_iters,
                "seed": inp.seed,
                "llm_api_key_secret": llm.llm_api_key_secret,
                "draft_model": llm.draft_model,
                "feedback_model": llm.feedback_model,
                "vlm_model": llm.vlm_model,
                "metric_reducer": search.metric_reducer,
                "sources": asdict(sources),
            },
            seed=inp.seed,
        ),
    )

    cfg = SearchConfig(
        num_drafts=search.num_drafts,
        num_workers=search.num_workers,
        max_debug_depth=search.max_debug_depth,
        debug_prob=search.debug_prob,
    )

    iters_used = 0
    while iters_used < inp.max_iters:
        nodes = await ctx.step("list_nodes", lambda: list_nodes_for_run(pool, run_id=inp.run_id))
        if _should_terminate(nodes, iters_used, inp.max_iters):
            break

        noderefs = [_to_noderef(n, reducer=search.metric_reducer) for n in nodes]
        selections = select_next(nodes=noderefs, cfg=cfg, rng=rng)
        # Defensive: the current selector always pads with phantom-draft
        # ``None`` entries up to ``num_workers``, but if a future change
        # ever yields an empty list we must break rather than spin.
        if not selections:
            break

        # Insert one bfts_nodes row per selection up-front. The
        # placeholder is required so the child workflow's
        # ``update_node_metric`` has an existing row to update; if a
        # child crashes between ``expand_node`` success and
        # ``update_node_metric`` success, replay re-runs from the
        # cached ``expand_node`` step and writes the update on retry.
        # The placeholder stays until then.
        prepared: list[tuple[str, dict[str, Any] | None]] = []
        for i, sel in enumerate(selections):
            parent_id = sel.node_id if sel is not None else None
            parent_row = (
                next((n for n in nodes if n["node_id"] == parent_id), None)
                if parent_id
                else None
            )
            stage = (
                "draft"
                if sel is None
                else ("debug" if parent_row and parent_row.get("is_buggy") else "improve")
            )
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

            # Per-iteration unique step name so each placeholder insert
            # has a distinct row in the checkpoint table (no reliance on
            # the engine's auto-suffix). Matches the ``create_sandbox_{i}``
            # convention in ``bfts_root``.
            node_id = await ctx.step(f"insert_node_{i}", _insert)
            prepared.append((node_id, parent_row))

        # Fan out: start every child eagerly so the engine schedules
        # them in parallel rather than waiting for the next worker poll.
        # The trigger_key is deterministic in (run_id, node_id) so a
        # parent replay reuses the same child run rather than spawning
        # a duplicate.
        children: list[tuple[str, dict[str, Any]]] = []
        for node_id, parent_row in prepared:
            child_run_id = f"{inp.run_id}:expand:{node_id}"
            # Step name embeds the child's ``node_id`` so a postmortem
            # against the checkpoint table can correlate each start back
            # to a specific child without auto-suffix lookup.
            child = await ctx.start_workflow(
                f"start_expand_{node_id}",
                workflow_name="bfts_expand_one",
                run_input={
                    "run_id": inp.run_id,
                    "node_id": node_id,
                    "sandbox_id": inp.sandbox_id,
                    # The 8-hex prefix matches the executor's allowlist
                    # (``^[A-Za-z0-9_-]+$``) and isolates each child's
                    # workspace files (runfile.py / experiment_data.npy
                    # / *.png) so concurrent siblings inside the shared
                    # sandbox don't race.
                    "working_dir": f"node_{node_id[:8]}",
                    "parent_node": parent_row,
                    "idea": inp.idea,
                    "llm_api_key_secret": llm.llm_api_key_secret,
                    "draft_model": llm.draft_model,
                    "feedback_model": llm.feedback_model,
                    "vlm_model": llm.vlm_model,
                },
                trigger_key=child_run_id,
                eager_start=True,
            )
            children.append((node_id, child))

        # Wait for every child to reach a terminal state before the next
        # iteration's ``list_nodes_for_run`` runs. Each child workflow
        # writes ``update_node_metric`` (and optionally
        # ``mark_buggy_plots``) before returning, so the controller
        # re-queries the DB on the next iteration to see the results.
        # The child's return-value envelope is logging-only — the DB
        # row is the source of truth.
        #
        # NOTE: ``wait_for_workflow`` returns the child record even for
        # failed/cancelled children. We do not inspect status here, which
        # means a permanently-failed child leaves its placeholder row with
        # NULL ``is_buggy`` / ``code`` / ``metric_json`` in the DB. Such a
        # row is invisible to ``_buggy_leaf_nodes`` (checks ``is True``) and
        # ``_good_nodes`` (checks ``is False``), but a draft-stage failure
        # still occupies a slot in ``select_next``'s ``len(drafts)`` count
        # and can stall the selector below ``num_drafts``. A follow-up will
        # add bounded waits + explicit failure inspection (tracked alongside
        # the same gap in ``bfts_root.py``).
        for node_id, child in children:
            # Step name suffix matches the ``start_expand_{node_id}`` above
            # so start/wait pairs share a node_id for easy correlation.
            await ctx.wait_for_workflow(
                f"wait_expand_{node_id}", run_id=child["run_id"]
            )
        iters_used += 1

    final_nodes = await ctx.step(
        "list_nodes_final", lambda: list_nodes_for_run(pool, run_id=inp.run_id)
    )
    from _bfts_export import (  # local import keeps top tidy
        select_best,
        write_best_artifact,
        write_best_node_id_artifact,
    )

    best = select_best(final_nodes, reducer=search.metric_reducer)
    if best is not None:
        await ctx.step(
            "write_best_artifact",
            lambda: write_best_artifact(pool, node_id=best["node_id"], code=best["code"]),
        )
        await ctx.step(
            "write_best_node_id_artifact",
            lambda: write_best_node_id_artifact(pool, node_id=best["node_id"]),
        )
        await ctx.step(
            "set_best",
            lambda: set_best_node(pool, run_id=inp.run_id, best_node_id=best["node_id"]),
        )
        ctx.log(
            "export_best",
            run_id=inp.run_id,
            best_node_id=best["node_id"],
            node_count=len(final_nodes),
        )

    return {
        "run_id": inp.run_id,
        "iters_used": iters_used,
        "node_count": len(final_nodes),
        "best_node_id": best["node_id"] if best else None,
    }
