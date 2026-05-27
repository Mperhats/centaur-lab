"""Workflow: research brief + ideation → ``bfts_root`` with BFTS-only Slack stream.

Slack-driven science entrypoint:

1. **Agent turn** (``slack_thread_turn``): live stream-of-consciousness only —
   the sandbox agent posts one short kickoff line; workflows do not open a
   competing agent-session stream for research.
2. **Plain thread posts**: full ``research_brief`` markdown, then the
   structured research idea after the ``ideation`` child completes.
3. **BFTS stream** (one agent-session message): tree-search kickoff and live
   progress until completion (via ``slack_stream_session_id`` on ``bfts_root``).

Failures post to the Slack thread (and close the BFTS stream when open).
``bfts_root`` runs asynchronously — its errors are also reported from
``bfts_root`` via ``notify_run_failure`` (not by re-waiting here).

Falls back to no Slack UI when ``delivery`` / ``SLACKBOT_URL`` are unset.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from api.workflow_engine import WorkflowContext

from packages.bfts_sdk.research import build_bfts_run_input
from packages.bfts_sdk.slack_delivery import (
    enrich_run_input_from_headers,
    resolve_slack_delivery,
)
from packages.bfts_sdk.slack_stream import (
    format_bfts_stream_intro,
    format_idea_markdown,
    format_research_brief_thread_message,
    notify_run_failure,
    notify_thread_failure,
    open_session,
    post_markdown,
    post_thread_message,
    streaming_available,
    workflow_run_error_text,
    workflow_run_failed,
)
from workflows.ideation import _child_workflow_output

WORKFLOW_NAME = "bfts_research"
SCHEDULE: dict[str, Any] = {}

_DEFAULT_BRIEF_LIMIT = 6


@dataclass
class Input:
    topic: str
    thread_key: str | None = None
    delivery: dict[str, Any] | None = None
    num_seeds: int | None = None
    num_drafts: int | None = None
    num_workers: int | None = None
    seed_paper_limit: int | None = None
    brief_paper_limit: int | None = None
    critic_retries: int = 0
    draft_model: str | None = None
    llm_api_key_secret: str | None = None


def _slack_metadata(ctx: WorkflowContext) -> dict[str, Any]:
    raw = ctx.run_input.get("metadata")
    return dict(raw) if isinstance(raw, dict) else {}


def _brief_markdown_for_slack(brief_result: dict[str, Any]) -> str:
    if brief_result.get("status") == "completed":
        return str(
            brief_result.get("markdown") or brief_result.get("compact_markdown") or ""
        ).strip()
    return ""


async def _run_research_brief(
    ctx: WorkflowContext,
    *,
    topic: str,
    limit: int,
) -> dict[str, Any]:
    """Persisted research brief via checkpointed ``ctx.tools`` (async proxy)."""

    # ``ctx.tools.*`` returns a coroutine; do not wrap in ``asyncio.to_thread``
    # (that checkpoints an unawaited coroutine → JSON serialize failure).
    return await ctx.step(
        "research_brief",
        lambda: ctx.tools.semantic_scholar.research_brief(
            query=topic,
            limit=limit,
        ),
    )


async def _run_research_pipeline(inp: Input, ctx: WorkflowContext) -> dict[str, Any]:
    topic = inp.topic.strip()
    merged_input = enrich_run_input_from_headers(
        header_thread_key=(
            inp.thread_key
            or str(ctx.run_input.get("thread_key") or "")
            or None
        ),
        run_input=dict(ctx.run_input),
    )
    thread_key = str(merged_input.get("thread_key") or "").strip()
    delivery = resolve_slack_delivery(
        explicit_delivery=inp.delivery or merged_input.get("delivery"),
        run_input=merged_input,
        explicit_thread_key=thread_key or inp.thread_key,
    )
    use_bfts_stream = streaming_available() and bool(delivery and thread_key)
    metadata = _slack_metadata(ctx)
    bfts_session = None
    brief_limit = inp.brief_paper_limit or inp.seed_paper_limit or _DEFAULT_BRIEF_LIMIT

    try:
        brief_result = await _run_research_brief(ctx, topic=topic, limit=brief_limit)
        if not isinstance(brief_result, dict):
            msg = f"research_brief returned unexpected type: {type(brief_result).__name__}"
            raise RuntimeError(msg)

        brief_markdown = _brief_markdown_for_slack(brief_result)
        if delivery and brief_markdown:
            await post_thread_message(
                ctx,
                delivery=delivery,
                text=format_research_brief_thread_message(
                    topic=topic,
                    markdown=brief_markdown,
                ),
                step_name="post_slack_research_brief",
                log_event="bfts_research_slack_brief_failed",
            )
        elif delivery and str(brief_result.get("status") or "") != "completed":
            err = workflow_run_error_text(brief_result)
            await notify_thread_failure(
                ctx,
                delivery=delivery,
                headline="Research brief failed",
                orchestrator_run_id=ctx.run_id,
                error_text=err,
                step_name="post_slack_research_brief_failed",
            )
            raise RuntimeError(f"research_brief did not complete: {err}")

        ideation_input: dict[str, Any] = {"topic": topic}
        if inp.thread_key:
            ideation_input["thread_key"] = inp.thread_key
        if inp.delivery is not None:
            ideation_input["delivery"] = inp.delivery
        for key, val in (
            ("num_seeds", inp.num_seeds),
            ("num_drafts", inp.num_drafts),
            ("num_workers", inp.num_workers),
        ):
            if val is not None:
                ideation_input[key] = val
        if inp.seed_paper_limit is not None:
            ideation_input["seed_paper_limit"] = inp.seed_paper_limit
        if inp.critic_retries:
            ideation_input["critic_retries"] = inp.critic_retries
        if inp.draft_model is not None:
            ideation_input["draft_model"] = inp.draft_model
        if inp.llm_api_key_secret is not None:
            ideation_input["llm_api_key_secret"] = inp.llm_api_key_secret

        ideation_child = await ctx.start_workflow(
            "start_ideation",
            workflow_name="ideation",
            run_input=ideation_input,
            trigger_key=f"{ctx.run_id}:ideation",
            eager_start=True,
        )
        ideation_run_id = str(ideation_child.get("run_id") or "")
        ideation_result = await ctx.wait_for_workflow(
            "wait_ideation",
            run_id=ideation_run_id,
        )
        if workflow_run_failed(ideation_result):
            err = workflow_run_error_text(ideation_result)
            await notify_thread_failure(
                ctx,
                delivery=delivery,
                headline="Ideation failed",
                orchestrator_run_id=ctx.run_id,
                error_text=err,
                step_name="post_slack_ideation_child_failed",
                child_run_id=ideation_run_id or None,
                child_workflow="ideation",
            )
            raise RuntimeError(f"ideation child failed: {err}")

        ideation_output = _child_workflow_output(ideation_result)
        idea = ideation_output.get("idea")
        if not isinstance(idea, dict) or not idea.get("Title"):
            err = workflow_run_error_text(ideation_result)
            await notify_thread_failure(
                ctx,
                delivery=delivery,
                headline="Ideation produced no valid idea",
                orchestrator_run_id=ctx.run_id,
                error_text=err,
                step_name="post_slack_ideation_invalid",
                child_run_id=ideation_run_id or None,
                child_workflow="ideation",
            )
            raise RuntimeError(f"ideation child did not return a valid idea: {err}")

        if delivery:
            await post_thread_message(
                ctx,
                delivery=delivery,
                text=format_idea_markdown(idea),
                step_name="post_slack_research_idea",
                log_event="bfts_research_slack_idea_failed",
            )

        bfts_run_input = build_bfts_run_input(
            idea=idea,
            run_input=ctx.run_input,
            thread_key=inp.thread_key,
            delivery=inp.delivery,
            num_seeds=inp.num_seeds,
            num_drafts=inp.num_drafts,
            num_workers=inp.num_workers,
        )

        slack_stream_session_id: str | None = None
        if use_bfts_stream and delivery:
            idea_title = str(idea.get("Title") or idea.get("Name") or "")
            bfts_session = await open_session(
                ctx,
                delivery=delivery,
                thread_key=thread_key,
                metadata=metadata,
                title="BFTS tree search",
                header="scientist · bfts",
                step_name="open_slack_bfts_stream",
            )
            if bfts_session:
                slack_stream_session_id = bfts_session.session_id
                bfts_run_input["slack_stream_session_id"] = slack_stream_session_id
                await post_markdown(
                    ctx,
                    bfts_session,
                    format_bfts_stream_intro(idea_title),
                    step_name="stream_bfts_intro",
                )

        bfts_child = await ctx.start_workflow(
            "start_bfts_root",
            workflow_name="bfts_root",
            run_input=bfts_run_input,
            trigger_key=f"{ctx.run_id}:bfts",
            eager_start=True,
        )
        bfts_run_id = str(bfts_child.get("run_id") or "")

        if delivery:
            await post_thread_message(
                ctx,
                delivery=delivery,
                text=(
                    f"BFTS tree search started (`{bfts_run_id}`). "
                    "Progress and errors stream in the **BFTS tree search** message above."
                ),
                step_name="post_slack_bfts_started",
                log_event="bfts_research_slack_bfts_started_failed",
            )

        ctx.log(
            "bfts_research_started",
            ideation_run_id=ideation_run_id,
            bfts_run_id=bfts_run_id,
            slack_stream=bool(slack_stream_session_id),
            num_seeds=bfts_run_input["num_seeds"],
            num_drafts=bfts_run_input["num_drafts"],
            num_workers=bfts_run_input["num_workers"],
        )

        return {
            "topic": topic,
            "ideation_run_id": ideation_run_id,
            "bfts_run_id": bfts_run_id,
            "idea": idea,
            "brief_document_id": brief_result.get("brief_document_id"),
            "brief_results_count": brief_result.get("results_count"),
            "seed_papers": ideation_output.get("seed_papers"),
            "papers_persisted": ideation_output.get("papers_persisted"),
            "bfts_run_input": bfts_run_input,
            "slack_stream_session_id": slack_stream_session_id,
            "slack_streaming": bool(slack_stream_session_id),
        }
    except Exception as exc:
        await notify_run_failure(
            ctx,
            delivery=delivery,
            stream=bfts_session,
            orchestrator_run_id=ctx.run_id,
            headline="bfts_research failed",
            error_text=str(exc),
            thread_step_name="post_slack_bfts_research_failed",
        )
        raise


async def handler(inp: Input, ctx: WorkflowContext) -> dict[str, Any]:
    if not inp.topic or not inp.topic.strip():
        raise ValueError("topic cannot be empty")

    return await _run_research_pipeline(inp, ctx)
