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
from packages.bfts_sdk.schema import assert_bfts_schema_present
from tools.bfts_runner.slack.format import (
    format_progress_message,
    format_search_config_line,
    slack_mention_prefix,
)
from tools.bfts_runner.slack.post import (
    enrich_slack_delivery_recipient,
    notify_thread_failure,
    post_thread_message,
    resolve_slack_delivery,
    workflow_run_error_text,
    workflow_run_failed,
)
from tools.bfts_runner.slack.stream import (
    SlackStreamTarget,
    close_session,
    notify_run_failure,
    post_markdown,
    post_step,
)

WORKFLOW_NAME = "bfts_root"
# Auto-copied into schedule metadata by the workflow loader (see
# ``.centaur/services/api/api/workflow_engine.py:1538-1541``); ``bfts_root``
# has no ``SCHEDULE`` so the constant only gates the post inside
# ``handler``. Empty string ⇒ skip the post entirely.
SLACK_CHANNEL = "bfts-runs"

# Slack-summary cap. The upstream slack tool will happily POST a 4000-char
# message, but operator-readable means one line on a phone — truncate the
# idea label past this and append an ellipsis so the run_id + success
# ratio remain visible.
_SLACK_SUMMARY_MAX_LEN = 200


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


def _reject_default_idea(
    inp: Input,
    run_input: dict[str, Any],
    *,
    idea_was_defaulted: bool,
) -> bool:
    """Whether to abort before provisioning sandboxes."""
    if not idea_was_defaulted or inp.allow_smoke_idea:
        return False
    if resolve_slack_delivery(
        explicit_delivery=inp.delivery,
        run_input=run_input,
        explicit_thread_key=inp.thread_key,
    ):
        return True
    thread_key = inp.thread_key or run_input.get("thread_key")
    return bool(thread_key)


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
    # Optional Slack delivery for the thread that triggered the run. When
    # set (or derivable from ``thread_key``), ``bfts_root`` posts kickoff
    # and completion summaries in that thread and @-mentions
    # ``recipient_user_id``. Operators still get the ``#bfts-runs`` post.
    delivery: dict[str, Any] | None = None
    thread_key: str | None = None
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
    # When True, allow the baked-in toy idea if ``idea`` is empty. Default
    # False so Slack/API runs without an idea fail fast instead of burning
    # hours on smoke. Operators pass True for ``just bfts-toy-run`` only.
    allow_smoke_idea: bool = False
    # When set, BFTS progress uses Slack native streaming (separate agent-session
    # message) instead of ``send_message`` posts in the user thread.
    slack_stream_session_id: str | None = None


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
    # Pre-flight: assert the overlay-owned BFTS tables exist BEFORE
    # any DB-touching step. Catches the schema-drift state where
    # ``schema_migrations_overlay`` says a table is applied but the
    # table itself was dropped out-of-band (see ``packages.bfts_sdk
    # .schema`` and ``docs/overlay-db-migrations.md`` "Drift
    # recovery"). Without this guard, the failure surfaces deep
    # inside ``resolve_search_config`` / ``insert_run`` as a
    # confusing ``UndefinedTableError``; with it, the run aborts at
    # iteration 0 with a message naming the missing table and the
    # recovery procedure.
    await ctx.step(
        "preflight_schema_check",
        lambda: assert_bfts_schema_present(ctx._pool),
    )

    # Idea resolution happens BEFORE anything else: a defaulted toy idea
    # should win every downstream prompt the same way an operator-supplied
    # idea would, and the substitution must be visible in workflow logs so
    # the postmortem ``why-did-this-run-use-toy-linreg?`` question is
    # answerable. Slack-triggered ``bfts_root`` runs that ship ``idea={}``
    # are the canonical case this catches.
    idea, idea_was_defaulted = _resolve_idea(inp.idea)
    if _reject_default_idea(inp, ctx.run_input, idea_was_defaulted=idea_was_defaulted):
        missing = [f for f in _REQUIRED_IDEA_FIELDS if not inp.idea.get(f)]
        raise ValueError(
            "bfts_root requires a populated idea (Name, Title, Short Hypothesis, "
            "Experiments). Run the ideation workflow on a research topic first, or "
            f"pass a full idea dict. Missing fields: {missing}"
        )
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

    slack_delivery = resolve_slack_delivery(
        explicit_delivery=inp.delivery,
        run_input=ctx.run_input,
        explicit_thread_key=inp.thread_key,
    )
    slack_delivery = await enrich_slack_delivery_recipient(
        ctx,
        slack_delivery,
        thread_key=inp.thread_key or ctx.run_input.get("thread_key"),
    )
    stream = _stream_target(inp.slack_stream_session_id)
    try:
        return await _run_bfts_trees(
            ctx,
            inp=inp,
            idea=idea,
            idea_was_defaulted=idea_was_defaulted,
            search=search,
            sources=sources,
            llm=llm,
            slack_delivery=slack_delivery,
            stream=stream,
        )
    except Exception as exc:
        from api.workflow_engine import SuspendWorkflow

        if isinstance(exc, SuspendWorkflow):
            raise
        await notify_run_failure(
            ctx,
            delivery=slack_delivery,
            stream=stream,
            orchestrator_run_id=ctx.run_id,
            headline="BFTS run failed",
            error_text=str(exc),
            thread_step_name="post_slack_bfts_root_failed",
        )
        raise


async def _run_bfts_trees(
    ctx: WorkflowContext,
    *,
    inp: Input,
    idea: dict[str, Any],
    idea_was_defaulted: bool,
    search: Any,
    sources: Any,
    llm: Any,
    slack_delivery: dict[str, Any] | None,
    stream: SlackStreamTarget | None,
) -> dict[str, Any]:
    await _post_slack_kickoff(
        ctx,
        run_id=ctx.run_id,
        idea=idea,
        num_drafts=search.num_drafts,
        num_seeds=search.num_seeds,
        num_workers=search.num_workers,
        sources=asdict(sources),
        delivery=slack_delivery,
        stream=stream,
    )

    # Every Sandbox we successfully create lands in ``sandboxes_to_clean``
    # before ``start_workflow``. Do **not** wrap the wait loop in
    # ``try/finally`` — Centaur replays ``finally`` when the handler
    # suspends at the first ``wait_for_workflow``, which deletes pods
    # while child trees are still running (see run ``wfr_33d0f01a091f4681``).
    sandboxes_to_clean: list[tuple[int, str]] = []
    children: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []

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
    except Exception:
        await _teardown_sandboxes(ctx, sandboxes_to_clean, re_raise_failures=False)
        raise

    await _post_slack_progress(
        ctx,
        run_id=ctx.run_id,
        delivery=slack_delivery,
        stream=stream,
        phase="launched",
        children=children,
        child_results=[],
    )

    for child in children:
        res = await ctx.wait_for_workflow(
            f"wait_tree_{child['tree_index']}", run_id=child["run_id"]
        )
        results.append(res)
        await _post_slack_progress(
            ctx,
            run_id=ctx.run_id,
            delivery=slack_delivery,
            stream=stream,
            phase="progress",
            children=children,
            child_results=results,
        )

    # Explicit teardown only after every ``wait_tree_*`` completes.
    await _teardown_sandboxes(ctx, sandboxes_to_clean, re_raise_failures=True)

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

    failed_lines: list[str] = []
    for child_meta, child_result in zip(children, results, strict=True):
        if not workflow_run_failed(child_result):
            continue
        idx = child_meta["tree_index"]
        err = workflow_run_error_text(
            child_result if isinstance(child_result, dict) else None
        )
        failed_lines.append(f"• tree {idx} (`{child_meta['run_id']}`): {err}")
    if failed_lines and slack_delivery:
        await notify_thread_failure(
            ctx,
            delivery=slack_delivery,
            headline="BFTS trees reported failures",
            orchestrator_run_id=ctx.run_id,
            error_text="\n".join(failed_lines),
            step_name="post_slack_bfts_tree_failures",
        )

    # Post after explicit sandbox teardown — a teardown failure raises and
    # this post is correctly skipped.
    summary_text = _format_run_summary(
        run_id=ctx.run_id,
        idea=idea,
        tree_summaries=tree_summaries,
    )
    await _post_slack_summary(
        ctx,
        summary_text,
        delivery=slack_delivery,
        stream=stream,
    )

    return {
        "run_id": ctx.run_id,
        "idea_used": idea,
        "idea_was_defaulted": idea_was_defaulted,
        "resolved_search_config": asdict(search),
        "sources": asdict(sources),
        "trees": tree_summaries,
    }


def _format_run_summary(
    *,
    run_id: str,
    idea: dict[str, Any],
    tree_summaries: list[dict[str, Any]],
) -> str:
    """Build the one-line Slack summary for a completed ``bfts_root`` run.

    Operator-readability targets: ``run_id`` is wrapped in backticks so
    Slack's mobile UI doesn't line-wrap on the underscore in
    ``wfr_<hex>``; the idea label falls back through ``Title`` →
    ``Name`` → a literal ``"(unnamed)"`` so the toy-defaulted case still
    reads cleanly; the suffix is truncated (not the prefix) so the
    grep-by-run-id workflow never loses the id.
    """
    total = len(tree_summaries)
    completed = sum(1 for s in tree_summaries if s.get("status") == "completed")
    label = idea.get("Title") or idea.get("Name") or "(unnamed)"
    prefix = f"BFTS run `{run_id}`: {completed}/{total} trees completed. Idea: "
    budget = _SLACK_SUMMARY_MAX_LEN - len(prefix)
    if budget > 1 and len(label) > budget:
        label = label[: budget - 1] + "…"
    return prefix + label


def _stream_target(session_id: str | None) -> SlackStreamTarget | None:
    if not session_id:
        return None
    return SlackStreamTarget(session_id=session_id)


async def _post_slack_progress(
    ctx: WorkflowContext,
    *,
    run_id: str,
    delivery: dict[str, Any] | None,
    stream: SlackStreamTarget | None,
    phase: str,
    children: list[dict[str, Any]],
    child_results: list[dict[str, Any]],
) -> None:
    """Periodic progress to Slack stream or plain thread message."""
    text = format_progress_message(
        run_id=run_id,
        phase=phase,
        children=children,
        child_results=child_results,
    )
    step_name = (
        "post_slack_progress_launched"
        if phase == "launched"
        else f"post_slack_progress_{len(child_results)}"
    )
    if stream:
        await post_step(
            ctx,
            stream,
            step_id="bfts_trees",
            title=f"BFTS `{run_id}`",
            status="in_progress",
            output=text,
            step_name=step_name,
        )
        return
    if not delivery:
        return
    await post_thread_message(
        ctx,
        delivery=delivery,
        text=text,
        step_name=step_name,
        log_event="bfts_root_slack_progress_failed",
    )


async def _teardown_sandboxes(
    ctx: WorkflowContext,
    sandboxes_to_clean: list[tuple[int, str]],
    *,
    re_raise_failures: bool,
) -> None:
    """Stop every BFTS executor Sandbox we provisioned.

    Must run only after all child ``bfts_tree`` workflows finish — never
    from a ``try/finally`` around ``wait_for_workflow`` (the workflow engine
    executes ``finally`` on suspend/replay, not on normal completion).
    """
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
        if re_raise_failures:
            raise RuntimeError(
                "bfts_root teardown failed for "
                + ", ".join(
                    f"tree_index={idx}: {exc!r}"
                    for idx, exc in teardown_errors
                )
            )


async def _post_slack_kickoff(
    ctx: WorkflowContext,
    *,
    run_id: str,
    idea: dict[str, Any],
    num_drafts: int,
    num_seeds: int,
    num_workers: int,
    sources: dict[str, str],
    delivery: dict[str, Any] | None,
    stream: SlackStreamTarget | None,
) -> None:
    """Kickoff via Slack stream (preferred) or plain ``send_message``."""
    label = idea.get("Title") or idea.get("Name") or "(unnamed)"
    config_line = format_search_config_line(
        num_drafts=num_drafts,
        num_seeds=num_seeds,
        num_workers=num_workers,
        sources=sources,
    )
    if stream:
        prefix = slack_mention_prefix(delivery or {})
        text = f"{prefix}**BFTS** `{run_id}` — {label}\n{config_line}"
        await post_markdown(
            ctx, stream, text, step_name="stream_bfts_kickoff_md",
        )
        await post_step(
            ctx,
            stream,
            step_id="bfts_trees",
            title=f"BFTS `{run_id}`",
            status="in_progress",
            details="Provisioning sandboxes and launching trees…",
            step_name="stream_bfts_kickoff_step",
        )
        return
    if not delivery:
        return
    prefix = slack_mention_prefix(delivery)
    text = (
        f"{prefix}BFTS run `{run_id}` started "
        f"({num_drafts} tree{'s' if num_drafts != 1 else ''}). Idea: {label}\n"
        f"{config_line}"
    )
    await post_thread_message(
        ctx,
        delivery=delivery,
        text=text,
        step_name="post_slack_kickoff",
        log_event="bfts_root_slack_kickoff_failed",
    )


async def _post_slack_summary(
    ctx: WorkflowContext,
    text: str,
    *,
    delivery: dict[str, Any] | None,
    stream: SlackStreamTarget | None,
) -> None:
    """Completion via stream and/or plain thread + ``#bfts-runs``."""
    if stream:
        await post_step(
            ctx,
            stream,
            step_id="bfts_trees",
            title="BFTS run finished",
            status="complete",
            output=text,
            step_name="stream_bfts_completion_step",
        )
        prefix = slack_mention_prefix(delivery or {})
        await post_markdown(
            ctx,
            stream,
            f"{prefix}{text}",
            step_name="stream_bfts_completion_md",
        )
        await close_session(ctx, stream, step_name="stream_bfts_done")
    elif delivery:
        thread_text = f"{slack_mention_prefix(delivery)}{text}"
        await post_thread_message(
            ctx,
            delivery=delivery,
            text=thread_text,
            step_name="post_slack_completion_thread",
            log_event="bfts_root_slack_thread_post_failed",
        )
    if not SLACK_CHANNEL:
        return
    try:
        await ctx.post_to_slack(SLACK_CHANNEL, text)
    except Exception as exc:
        ctx.log("bfts_root_slack_post_failed", error=repr(exc))
