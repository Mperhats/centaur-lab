"""Workflow: BFTS tree controller (Stage 1 only).

Loops:
  select_next → insert placeholder rows → expand selected nodes in-process
  (bounded by ``num_workers``) → re-query DB → check terminate.

Terminate when ≥1 good_node exists (Sakana stage-1 completion rule,
agent_manager.py:434-442) OR iters_used >= max_iters.

See ``docs/bfts-phase5-orchestration.md`` (Phase 5a).
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import random
import uuid
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from api.workflow_engine import WorkflowContext

from packages.bfts_sdk.config import (
    DEFAULT_METRIC_REDUCER,
    resolve_llm_api_key,
    resolve_llm_settings,
    resolve_search_settings,
)
from packages.bfts_sdk.expand_runner import run_expand_for_node
from packages.bfts_sdk.metric import score
from packages.bfts_sdk.schema import assert_bfts_schema_present
from packages.bfts_sdk.select import NodeRef, SearchConfig, select_next
from packages.bfts_sdk.state import (
    insert_node,
    insert_run,
    list_nodes_for_run,
    list_seed_children,
    mark_node_failed,
    mark_run_completed,
    set_best_node,
    update_node_aggregate_metric,
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
    prior_attempts_window: int | None = None
    num_seeds: int | None = None
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
        is_seed_node=bool(row.get("is_seed_node")),
    )


def _root_id(row: dict[str, Any]) -> str:
    return row["node_id"] if row.get("parent_node_id") is None else (row.get("parent_node_id") or "ROOT")


async def _run_iteration_expansions(
    *,
    ctx: WorkflowContext,
    pool: Any,
    inp: Input,
    prepared: list[tuple[str, dict[str, Any] | None]],
    llm: Any,
    search: Any,
    llm_api_key: str,
) -> None:
    """Run up to ``num_workers`` node expansions inside this workflow."""
    sem = asyncio.Semaphore(search.num_workers)

    async def _run_one(node_id: str, parent_row: dict[str, Any] | None) -> None:
        async with sem:
            try:
                await run_expand_for_node(
                    ctx,
                    pool,
                    run_id=inp.run_id,
                    node_id=node_id,
                    sandbox_id=inp.sandbox_id,
                    working_dir=f"node_{node_id[:8]}",
                    parent_node=parent_row,
                    idea=inp.idea,
                    llm_api_key=llm_api_key,
                    draft_model=llm.draft_model,
                    feedback_model=llm.feedback_model,
                    vlm_model=llm.vlm_model,
                    prior_attempts_window=search.prior_attempts_window,
                )
            except Exception as exc:
                await ctx.step(
                    f"mark_failed_{node_id}",
                    lambda nid=node_id, err=exc: mark_node_failed(
                        pool,
                        node_id=nid,
                        exc_type=type(err).__name__,
                        exc_info={"error": str(err)},
                        analysis=f"expand failed: {err}",
                    ),
                )

    await asyncio.gather(
        *[_run_one(node_id, parent_row) for node_id, parent_row in prepared]
    )


async def _run_seed_evaluations(
    *,
    ctx: WorkflowContext,
    pool: Any,
    inp: Input,
    best: dict[str, Any],
    llm: Any,
    search: Any,
    llm_api_key: str,
) -> None:
    sem = asyncio.Semaphore(max(search.num_seeds, 1))

    async def _run_seed(seed_idx: int) -> None:
        seed_node_id = (
            f"{inp.run_id}-seed-{seed_idx}"
            .replace("_", "-").replace(":", "-").lower()
        )
        await ctx.step(
            f"insert_seed_node_{seed_idx}",
            lambda nid=seed_node_id, s=seed_idx: insert_node(
                pool,
                node_id=nid,
                run_id=inp.run_id,
                parent_node_id=best["node_id"],
                step=99000 + s,
                stage_name="seed",
                plan=f"seed re-eval {s}",
                code=best["code"],
                is_seed_node=True,
                seed=s,
            ),
        )
        async with sem:
            try:
                await run_expand_for_node(
                    ctx,
                    pool,
                    run_id=inp.run_id,
                    node_id=seed_node_id,
                    sandbox_id=inp.sandbox_id,
                    working_dir=f"seed_{seed_idx}",
                    parent_node=best,
                    idea=inp.idea,
                    llm_api_key=llm_api_key,
                    draft_model=llm.draft_model,
                    feedback_model=llm.feedback_model,
                    vlm_model=llm.vlm_model,
                    prior_attempts_window=0,
                    seed_override=seed_idx,
                )
            except Exception as exc:
                await ctx.step(
                    f"mark_seed_failed_{seed_node_id}",
                    lambda nid=seed_node_id, err=exc: mark_node_failed(
                        pool,
                        node_id=nid,
                        exc_type=type(err).__name__,
                        exc_info={"error": str(err)},
                        analysis=f"seed expand failed: {err}",
                    ),
                )

    await asyncio.gather(
        *[_run_seed(seed_idx) for seed_idx in range(search.num_seeds)]
    )


async def handler(inp: Input, ctx: WorkflowContext) -> dict[str, Any]:
    # Pre-flight schema check (see ``bfts_root.handler`` for the full
    # rationale). The parent already runs this when the tree is
    # spawned via ``bfts_root``, but ``bfts_tree`` is also reachable
    # standalone (tests, manual ``POST /api/workflows/...``), so the
    # check is repeated here at no measurable cost — every BFTS table
    # is queried with ``LIMIT 0``, which the planner short-circuits.
    await ctx.step(
        "preflight_schema_check",
        lambda: assert_bfts_schema_present(ctx._pool),
    )

    llm = resolve_llm_settings(
        draft_model=inp.draft_model,
        feedback_model=inp.feedback_model,
        vlm_model=inp.vlm_model,
        llm_api_key_secret=inp.llm_api_key_secret,
    )
    llm_api_key = resolve_llm_api_key(llm.llm_api_key_secret)
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
        prior_attempts_window=inp.prior_attempts_window,
        num_seeds=inp.num_seeds,
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
                # F.2 / F.4 fields — were missing from the persisted
                # snapshot even though ``sources`` claimed they'd been
                # resolved. Without them in ``config_json``, a replay or
                # postmortem couldn't reproduce the exact knob values
                # that drove the run (operator had to read the engine's
                # ``output_json`` instead, which is per-run-only).
                "prior_attempts_window": search.prior_attempts_window,
                "num_seeds": search.num_seeds,
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
        # placeholder is required so ``update_node_metric`` has an
        # existing row to update; if expansion fails between
        # ``expand_node`` success and ``update_node_metric`` success,
        # replay re-runs from the cached ``expand_node`` step and writes
        # the update on retry.
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

        # Resume the sandbox just-in-time for the children's
        # ``exec_python`` calls. Iter 0 sees the sandbox warm-from-create
        # (``bfts_root.handler`` provisions at ``replicas=1`` and waits
        # for the pod Ready), so we skip the resume there; from iter 1
        # onward the previous iteration's ``pause_sandbox`` has parked
        # the pod at ``replicas=0`` to release compute while the API
        # pod runs ``select_next`` + the LLM steps inside
        # ``bfts_expand_one``. ``resume_sandbox`` is idempotent — on a
        # workflow replay where the step result is already cached the
        # engine skips the call entirely; on a replay where it wasn't,
        # the underlying patch-to-1 + pod readiness wait completes in
        # ms against an already-running pod. Step name embeds
        # ``iters_used`` so each iteration's resume gets a distinct
        # checkpoint row (same convention as ``insert_node_{i}`` above).
        if iters_used > 0:
            await ctx.step(
                f"resume_sandbox_{iters_used}",
                lambda: ctx.tools.bfts_executor.resume_sandbox(
                    sandbox_id=inp.sandbox_id
                ),
            )

        await _run_iteration_expansions(
            ctx=ctx,
            pool=pool,
            inp=inp,
            prepared=prepared,
            llm=llm,
            search=search,
            llm_api_key=llm_api_key,
        )

        # All expansions in this iteration finished — their ``exec_python``
        # calls are done and we don't touch the sandbox again until the
        # next iteration. Park the pod at ``replicas=0`` to release
        # CPU/memory while the API pod runs the next ``select_next``.
        await ctx.step(
            f"pause_sandbox_{iters_used}",
            lambda: ctx.tools.bfts_executor.pause_sandbox(
                sandbox_id=inp.sandbox_id
            ),
        )
        iters_used += 1

        # Yield the workflow worker between iterations. Without this, the
        # tree handler holds its worker continuously for the full
        # ``max_iters`` x ~2-min/iter run (40-60 min), starving ``bfts_root``'s
        # progress poller (which sleeps 90s between polls and needs a
        # worker to re-claim). The 0-duration suspend persists a
        # checkpoint with ``available_at = now`` and raises
        # ``SuspendWorkflow``; the scheduler picks the most-overdue
        # runnable next, which is normally ``bfts_root`` if its
        # 90-second sleep elapsed during the iteration we just ran.
        # ``iters_used - 1`` because the increment happened above; the
        # checkpoint name pairs each yield with the iteration that
        # produced it. Step name is per-iteration so workflow replay
        # finds a distinct cache entry for each yield.
        await ctx.sleep(
            f"tree_iter_yield_{iters_used - 1}",
            dt.timedelta(seconds=0),
        )

    final_nodes = await ctx.step(
        "list_nodes_final", lambda: list_nodes_for_run(pool, run_id=inp.run_id)
    )
    from packages.bfts_sdk.export import (  # local import keeps top tidy
        render_tree_dot,
        select_best,
        write_best_artifact,
        write_best_node_id_artifact,
        write_tree_dot_artifact,
    )

    best = select_best(final_nodes, reducer=search.metric_reducer)
    best_solution_artifact_id: str | None = None
    best_node_id_artifact_id: str | None = None
    if best is not None:
        best_solution_artifact_id = await ctx.step(
            "write_best_artifact",
            lambda: write_best_artifact(pool, node_id=best["node_id"], code=best["code"]),
        )
        best_node_id_artifact_id = await ctx.step(
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

    # F.3: emit tree.dot visualization artifact. Anchor on the best
    # node when present, else the first final_nodes row, so even runs
    # with no good leaves get a queryable tree.dot for postmortem
    # debugging. Skip silently when there are zero nodes (no anchor
    # available — bfts_artifacts has FK on node_id).
    anchor_node_id: str | None = (
        best["node_id"] if best is not None
        else (final_nodes[0]["node_id"] if final_nodes else None)
    )
    tree_dot_artifact_id: str | None = None
    if anchor_node_id is not None:
        dot_text = render_tree_dot(
            final_nodes,
            run_id=inp.run_id,
            best_node_id=best["node_id"] if best else None,
        )
        tree_dot_artifact_id = await ctx.step(
            "write_tree_dot",
            lambda dt=dot_text, aid=anchor_node_id: write_tree_dot_artifact(
                pool, run_id=inp.run_id, dot_text=dt, anchor_node_id=aid,
            ),
        )

    # F.4: multi-seed re-evaluation of the best node. Opt-in via
    # ``num_seeds > 0``; default 0 preserves Phase 0-4 behavior. We
    # fan out N seed children of ``best`` (each a ``bfts_expand_one``
    # run in seed-override mode → no LLM, just the executor), wait,
    # then aggregate mean/std of ``final_value`` into the best node's
    # ``metric_json`` so downstream tooling sees the noise estimate
    # alongside the point metric.
    seed_rows: list[dict[str, Any]] = []
    aggregate: dict[str, float | None] | None = None
    if best is not None and search.num_seeds > 0:
        # The final main-loop iteration left the sandbox at
        # ``replicas=0``. Seed-eval children each ``exec_python`` the
        # best node's code with overridden seeds, so we need the pod
        # awake before fanning out. One unsuffixed step name is fine —
        # there is exactly one seed block per ``bfts_tree`` workflow.
        # No matching pause after the seed fan-out: ``bfts_root`` calls
        # ``stop_sandbox`` only after all ``wait_tree_*`` steps finish,
        # which deletes the CR + pod outright; a pause→stop sequence
        # would just waste a CRD patch.
        await ctx.step(
            "resume_sandbox_seeds",
            lambda: ctx.tools.bfts_executor.resume_sandbox(
                sandbox_id=inp.sandbox_id
            ),
        )
        await _run_seed_evaluations(
            ctx=ctx,
            pool=pool,
            inp=inp,
            best=best,
            llm=llm,
            search=search,
            llm_api_key=llm_api_key,
        )
        seed_rows = await ctx.step(
            "list_seed_children",
            lambda: list_seed_children(pool, parent_node_id=best["node_id"]),
        ) or []
        aggregate = _aggregate_seed_metrics(seed_rows)
        if aggregate is not None:
            await ctx.step(
                "write_aggregate_metric",
                lambda agg=aggregate: update_node_aggregate_metric(
                    pool, node_id=best["node_id"], aggregate=agg,
                ),
            )
        ctx.log(
            "seed_aggregate",
            run_id=inp.run_id,
            best_node_id=best["node_id"],
            num_seeds=search.num_seeds,
            aggregate=aggregate,
        )

    # Unconditional terminal-status write — covers the "all-buggy tree,
    # no best leaf" case where ``set_best_node`` is never called and the
    # row would otherwise stay ``running`` forever. ``set_best_node`` no
    # longer touches ``status`` so this is the single writer for the
    # ``running -> completed`` transition (idempotent on replay).
    await ctx.step(
        "mark_run_completed",
        lambda: mark_run_completed(pool, run_id=inp.run_id),
    )

    return {
        "run_id": inp.run_id,
        "iters_used": iters_used,
        "node_count": len(final_nodes),
        "best_node_id": best["node_id"] if best else None,
        # F.6 verification surface — keep entries small so the whole dict
        # round-trips through ``workflow_runs.output_json`` (jsonb) on a
        # Slack-driven smoke without hitting payload limits. Large payloads
        # (best_solution.py code, full per-node code/metric history) stay
        # in ``bfts_artifacts`` / ``bfts_nodes`` and are reachable via the
        # artifact ids surfaced below.
        "best_metric_json": _coerce_metric_json(best.get("metric_json")) if best else None,
        "best_stage_name": (
            str(best["stage_name"])
            if best is not None and best.get("stage_name") is not None
            else None
        ),
        "best_solution_artifact_id": best_solution_artifact_id,
        "best_node_id_artifact_id": best_node_id_artifact_id,
        "tree_dot_artifact_id": tree_dot_artifact_id,
        "seed_aggregate": aggregate,
        "seed_children": _project_seed_children(seed_rows),
    }


def _coerce_metric_json(value: Any) -> dict[str, Any] | None:
    """Coerce asyncpg's jsonb return shape (``str | dict | None``) into a
    plain dict so the return value round-trips through jsonb again on the
    parent's ``output_json``. Mirrors ``_aggregate_seed_metrics``' parse
    tolerance so a malformed metric_json doesn't crash the return.
    """
    import json as _json

    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = _json.loads(value)
        except _json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def _project_seed_children(
    seed_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Reduce ``list_seed_children`` rows to the verification-relevant
    subset. Slack agents only need ``seed`` + ``node_id`` + ``is_buggy``
    + ``final_value`` to confirm the F.4 fan-out fired and produced
    aggregatable values. The full ``metric_json`` blob (with parse_*,
    plot_*, etc.) stays in the DB to keep the parent's ``output_json``
    compact.
    """
    out: list[dict[str, Any]] = []
    for r in seed_rows:
        metric = _coerce_metric_json(r.get("metric_json")) or {}
        final_value = metric.get("final_value")
        if not isinstance(final_value, (int, float)):
            names = metric.get("metric_names")
            if isinstance(names, list) and names:
                first = names[0]
                if isinstance(first, dict):
                    data = first.get("data")
                    if isinstance(data, list) and data and isinstance(data[0], dict):
                        inner = data[0].get("final_value")
                        if isinstance(inner, (int, float)):
                            final_value = float(inner)
        out.append({
            "node_id": str(r["node_id"]),
            "seed": r.get("seed"),
            "is_buggy": bool(r.get("is_buggy")) if r.get("is_buggy") is not None else None,
            "final_value": (
                float(final_value) if isinstance(final_value, (int, float)) else None
            ),
        })
    return out


def _aggregate_seed_metrics(
    seed_rows: list[dict[str, Any]],
) -> dict[str, float | None] | None:
    """Compute mean / std / n over ``final_value`` of non-buggy seed
    children. ``metric_json`` arrives as either a raw JSON string
    (asyncpg jsonb return) or a pre-parsed dict; tolerate both.

    Returns ``None`` if no seed child produced a usable scalar, so
    the caller can skip the aggregate write. Excludes buggy children
    so a single seed crash doesn't poison the mean.

    ``aggregate_std`` uses Bessel's correction (``n-1`` denominator)
    when ``n >= 2`` — matches ``numpy.std(ddof=1)`` and signals that
    the value is a sample-variance estimator. With a single surviving
    seed (e.g. one seed_override child failed and was marked buggy
    upstream), we emit ``aggregate_std=None`` rather than the previous
    misleading ``0.0`` — downstream consumers must distinguish "we
    observed zero variance" from "we don't have enough samples to
    estimate variance".
    """
    import json as _json

    values: list[float] = []
    for r in seed_rows:
        if r.get("is_buggy"):
            continue
        m = r.get("metric_json")
        if isinstance(m, str):
            try:
                m = _json.loads(m)
            except _json.JSONDecodeError:
                continue
        if not isinstance(m, dict):
            continue
        v = m.get("final_value")
        if isinstance(v, (int, float)):
            values.append(float(v))
            continue
        # Newer schema: nested metric_names[*].data[*].final_value
        names = m.get("metric_names")
        if isinstance(names, list) and names:
            first = names[0]
            if isinstance(first, dict):
                data = first.get("data")
                if isinstance(data, list) and data:
                    inner = data[0].get("final_value") if isinstance(data[0], dict) else None
                    if isinstance(inner, (int, float)):
                        values.append(float(inner))
    if not values:
        return None
    mean = sum(values) / len(values)
    std: float | None
    if len(values) >= 2:
        var = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
        std = var ** 0.5
    else:
        std = None
    return {
        "aggregate_mean": mean,
        "aggregate_std": std,
        "aggregate_n": float(len(values)),
    }
