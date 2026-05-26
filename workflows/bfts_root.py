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

from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from api.workflow_engine import WorkflowContext

from packages.bfts_sdk.config import resolve_llm_settings, resolve_search_config

WORKFLOW_NAME = "bfts_root"


# Plan-required idea fields — the minimum subset ``_bfts_expand._propose_prompt``
# needs to produce non-degenerate drafts. Matches
# ``ideation._PLAN_REQUIRED_IDEA_FIELDS`` so an idea hand-built upstream of
# ``ideation`` is held to the same bar as one synthesized by it. An idea
# missing any of these renders an empty ``## Idea`` markdown block to the
# LLM, which deterministically produces unfocused boilerplate code.
_REQUIRED_IDEA_FIELDS: tuple[str, ...] = (
    "Name",
    "Title",
    "Short Hypothesis",
    "Experiments",
)

# Baked-in toy idea used when the caller invokes ``bfts_root`` with an empty
# (or partial) ``idea`` — e.g. a Slack-driven smoke test where the operator
# wants the wiring exercised without hand-crafting an idea dict. Matches the
# linear-regression toy used by ``just bfts-toy-run`` so the smoke + Slack
# paths converge on the same fixture. Operators who want a real research
# experiment must pass a populated ``idea`` (typically the output of the
# ``ideation`` workflow).
_DEFAULT_SMOKE_IDEA: dict[str, Any] = {
    "Name": "toy-linreg-smoke",
    "Title": "Linear regression baseline on 200 synthetic samples",
    "Short Hypothesis": (
        "A least-squares fit on a 1-feature synthetic dataset should "
        "achieve MSE below the variance of y."
    ),
    "Related Work": (
        "Standard ordinary-least-squares baseline; included so the smoke "
        "run has a non-empty Related Work field for the draft prompt."
    ),
    "Abstract": (
        "Fit ``sklearn.linear_model.LinearRegression`` to 200 synthetic "
        "(x, y) pairs and report MSE on a held-out split."
    ),
    "Experiments": [
        "sklearn.linear_model.LinearRegression on a single synthetic "
        "dataset of 200 samples; 80/20 train/test split; report MSE.",
    ],
    "Risk Factors and Limitations": (
        "Toy fixture — no actual research signal; only used to exercise "
        "the BFTS control plane end-to-end."
    ),
}


def _resolve_idea(idea: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Return ``(resolved_idea, was_defaulted)``.

    The caller-supplied ``idea`` is accepted as-is when every plan-required
    field is present and non-empty. Otherwise we substitute the baked-in
    toy idea so the run is still meaningful (degenerate drafts on an empty
    idea dict are useless to operators and burn LLM budget).

    Empty strings are treated as missing — an empty ``Short Hypothesis``
    is as useless to ``_bfts_expand`` as a missing key. Mirrors
    ``ideation._validate_idea``'s semantics.
    """
    missing = [f for f in _REQUIRED_IDEA_FIELDS if not idea.get(f)]
    if missing:
        return _DEFAULT_SMOKE_IDEA, True
    return idea, False


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


async def handler(inp: Input, ctx: WorkflowContext) -> dict[str, Any]:
    # Idea resolution happens BEFORE anything else: a defaulted toy idea
    # should win every downstream prompt the same way an operator-supplied
    # idea would, and the substitution must be visible in workflow logs so
    # the postmortem ``why-did-this-run-use-toy-linreg?`` question is
    # answerable. Slack-triggered ``bfts_root`` runs that ship ``idea={}``
    # are the canonical case this catches.
    idea, idea_was_defaulted = _resolve_idea(inp.idea)
    if idea_was_defaulted:
        missing = [f for f in _REQUIRED_IDEA_FIELDS if not inp.idea.get(f)]
        ctx.log(
            "bfts_root_using_default_idea",
            run_id=ctx.run_id,
            missing_fields=missing,
            default_idea_name=_DEFAULT_SMOKE_IDEA["Name"],
        )

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
                    "idea": idea,
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
            except Exception as exc:
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

    # Richer verification surface for Slack-driven runs (the sandbox token
    # cannot run direct DB queries via ``/agent/query``, so the workflow
    # return value is the ONLY postmortem channel for the agent). Each
    # ``bfts_tree`` child returns a per-tree summary dict via
    # ``ctx.wait_for_workflow``; we merge it with the controller-side
    # bookkeeping (sandbox_id, tree_index) so a single ``call workflow get
    # <run_id>`` exposes everything an operator would otherwise need
    # ``psql`` for: best node + its metric, F.4 seed aggregate + seed
    # children, F.3 tree.dot artifact id, the resolved idea, and which
    # tier of the F.2/F.4 resolver chain won each field.
    tree_summaries: list[dict[str, Any]] = []
    for child_meta, child_result in zip(children, results, strict=True):
        # ``wait_for_workflow`` returns the ``_fetch_run_response`` dict;
        # the child handler's return value lives under ``output_json``
        # (asyncpg jsonb decode → dict | None). On a failed child the
        # output is None — we still emit a row so the operator can see
        # which tree died from the workflow's own return value.
        output = (
            child_result.get("output_json")
            if isinstance(child_result, dict)
            else None
        )
        summary: dict[str, Any] = {
            "tree_index": child_meta["tree_index"],
            "run_id": child_meta["run_id"],
            "sandbox_id": child_meta["sandbox_id"],
            "status": (
                child_result.get("status") if isinstance(child_result, dict) else None
            ),
        }
        if isinstance(output, dict):
            summary.update(output)
        tree_summaries.append(summary)

    return {
        "run_id": ctx.run_id,
        "idea_used": idea,
        "idea_was_defaulted": idea_was_defaulted,
        "resolved_search_config": asdict(search),
        "sources": asdict(sources),
        "trees": tree_summaries,
    }
