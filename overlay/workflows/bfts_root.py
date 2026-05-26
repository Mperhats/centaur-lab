"""Workflow: BFTS root — fans out num_drafts independent bfts_tree children.

Each child gets a Sandbox provisioned by `bfts_executor.create_sandbox`
(Task 1.6 / 1.9 in plan Phase 1). We do NOT call `ctx.agent_turn` to
provision — Spec correction #11: do_agent_turn (.centaur/services/api/api
/workflow_engine.py:1124) is for spawn→message→execute→wait-for-terminal
agent runs and drags in spawn_assignment, slackbot session opening, and
agent-execution event rows that BFTS does not need (BFTS sandboxes have
no harness; the executor's CMD is `sleep infinity`).

See docs/superpowers/plans/2026-05-25-bfts-on-centaur.md (Phase 2).
"""
from __future__ import annotations

import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

if TYPE_CHECKING:
    from api.workflow_engine import WorkflowContext

from _bfts_config import resolve_llm_settings, resolve_search_config

WORKFLOW_NAME = "bfts_root"


@dataclass
class Input:
    idea: dict[str, Any] = field(default_factory=dict)
    # Search-policy fields default to None so the Phase 4c.4 resolver
    # chain (Input → bfts_hyperparams DB row → BFTS_* env → module
    # default) actually reaches the lower tiers; non-None dataclass
    # defaults would short-circuit every other layer and silence the
    # nightly reflection workflow's tuning. Operators set these on a
    # POST run_input only when they want to override the
    # reflection-tuned values.
    num_drafts: int | None = None
    num_workers: int | None = None
    max_debug_depth: int | None = None
    debug_prob: float | None = None
    prior_attempts_window: int | None = None
    num_seeds: int | None = None
    max_iters: int = 20
    seed_base: int = 0
    # Optional per-run LLM overrides. When omitted, deployment env (BFTS_* in
    # values.local.yaml api.extraEnv) and _bfts_config defaults apply.
    llm_api_key_secret: str | None = None
    draft_model: str | None = None
    feedback_model: str | None = None
    vlm_model: str | None = None
    # Optional per-run search-policy override. Resolves alongside the
    # other search-policy fields through resolve_search_config; the
    # resolved value is persisted into bfts_runs.config_json by
    # bfts_tree so replay is deterministic.
    metric_reducer: str | None = None


def _sandbox_id(*, run_id: str, tree_idx: int) -> str:
    """Deterministic per-tree sandbox id.

    Format chosen so the BFTS executor's Sandbox CRDs are easy to scope
    by run_id (label `centaur.ai/bfts-run`) and easy to clean up by
    prefix. Stable across workflow restarts because `ctx.run_id` is
    durable.

    RFC 1123 normalization: live ``ctx.run_id`` values are ``wfr_<hex>``
    whose underscore violates K8s ``metadata.name`` (lowercase
    alphanumeric + ``-`` + ``.`` only). Replace ``_`` with ``-`` so
    ``create_sandbox`` doesn't get rejected with HTTP 422.
    """
    safe_run_id = run_id.replace("_", "-").lower()
    return f"bfts-{safe_run_id}-tree-{tree_idx}"


async def handler(inp: Input, ctx: "WorkflowContext") -> dict[str, Any]:
    llm = resolve_llm_settings(
        draft_model=inp.draft_model,
        feedback_model=inp.feedback_model,
        vlm_model=inp.vlm_model,
        llm_api_key_secret=inp.llm_api_key_secret,
    )
    # Resolve search-policy once at the root and thread the resolved
    # snapshot into every child tree's run_input so all siblings share
    # one coherent config (Phase 4c.4). Layering: Input override →
    # bfts_hyperparams latest row (reflection-tuned) → BFTS_* env →
    # module default. The DB read is on the parent only; tree handlers
    # treat the values as authoritative and don't re-resolve. The
    # companion ``sources`` records which tier won each field so the
    # postmortem ``why-did-this-run-use-X`` query is one SELECT.
    search, sources = await resolve_search_config(
        ctx._pool,
        debug_prob=inp.debug_prob,
        max_debug_depth=inp.max_debug_depth,
        num_drafts=inp.num_drafts,
        num_workers=inp.num_workers,
        metric_reducer=inp.metric_reducer,
        prior_attempts_window=inp.prior_attempts_window,
        num_seeds=inp.num_seeds,
    )
    ctx.log(
        "bfts_root_resolved_search_config",
        **asdict(search),
        sources=asdict(sources),
    )

    # Every Sandbox we successfully create lands here BEFORE start_workflow
    # is attempted, so a start_workflow failure (which leaves the CR behind)
    # is still cleaned up by the finally block. `children` separately holds
    # only fully-started trees that the wait loop iterates.
    sandboxes_to_clean: list[tuple[int, str]] = []
    children: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []
    body_succeeded = False

    try:
        for i in range(search.num_drafts):
            sandbox_id = _sandbox_id(run_id=ctx.run_id, tree_idx=i)
            await ctx.step(
                f"create_sandbox_{i}",
                lambda sid=sandbox_id: ctx.tools.bfts_executor.create_sandbox(
                    sandbox_id=sid,
                    run_id=ctx.run_id,
                ),
            )
            sandboxes_to_clean.append((i, sandbox_id))

            child_run_id = f"{ctx.run_id}:tree:{i}"
            child = await ctx.start_workflow(
                f"start_tree_{i}",
                workflow_name="bfts_tree",
                run_input={
                    "run_id": child_run_id,
                    "parent_run_id": ctx.run_id,
                    "idea": inp.idea,
                    "num_drafts": 1,    # each child tree has 1 root; root-level num_drafts = num trees
                    "num_workers": search.num_workers,
                    "max_debug_depth": search.max_debug_depth,
                    "debug_prob": search.debug_prob,
                    "prior_attempts_window": search.prior_attempts_window,
                    "num_seeds": search.num_seeds,
                    "max_iters": inp.max_iters,
                    "seed": inp.seed_base + i,
                    "sandbox_id": sandbox_id,
                    "llm_api_key_secret": llm.llm_api_key_secret,
                    "draft_model": llm.draft_model,
                    "feedback_model": llm.feedback_model,
                    "vlm_model": llm.vlm_model,
                    "metric_reducer": search.metric_reducer,
                },
                trigger_key=child_run_id,
                eager_start=True,
            )
            children.append(
                {"run_id": child["run_id"], "tree_index": i, "sandbox_id": sandbox_id}
            )

        for child in children:
            res = await ctx.wait_for_workflow(
                f"wait_tree_{child['tree_index']}", run_id=child["run_id"]
            )
            results.append(res)

        body_succeeded = True
    finally:
        # Best-effort teardown of every Sandbox we provisioned. Each
        # stop_sandbox is its own ctx.step so the engine checkpoints it,
        # but a stuck CR (e.g. finalizer still running) must not block the
        # other stops. We collect per-tree errors and surface them after
        # the loop: aggregated re-raise on the happy path (so the failure
        # is visible), structured log only when the body already raised
        # (so the root-cause exception keeps its propagation slot).
        # PVC follows owner refs (Spec correction #12 + agent-sandbox
        # `shutdownPolicy: "Retain"` is overridden by an explicit delete).
        teardown_errors: list[tuple[int, BaseException]] = []
        for tree_index, sandbox_id in sandboxes_to_clean:
            try:
                await ctx.step(
                    f"stop_sandbox_{tree_index}",
                    lambda sid=sandbox_id: ctx.tools.bfts_executor.stop_sandbox(
                        sandbox_id=sid
                    ),
                )
            except Exception as exc:  # noqa: BLE001 — aggregated below
                teardown_errors.append((tree_index, exc))

        if teardown_errors:
            ctx.log(
                "bfts_root_teardown_errors",
                run_id=ctx.run_id,
                errors=[
                    {"tree_index": idx, "error": repr(exc)}
                    for idx, exc in teardown_errors
                ],
            )
            if body_succeeded:
                raise RuntimeError(
                    "bfts_root teardown failed for "
                    + ", ".join(
                        f"tree_index={idx}: {exc!r}"
                        for idx, exc in teardown_errors
                    )
                )

    return {"trees": children, "results": results}
