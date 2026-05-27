"""Workflow: streamed ideation (brief + idea) → eager ``bfts_root`` with streamed status.

Slack-driven science entrypoint:

1. **Ideation stream** (first Slack agent-session message): literature brief,
   then structured research idea.
2. **BFTS stream** (second message): tree-search kickoff and live progress
   until completion (via ``slack_stream_session_id`` on the child run).

Falls back to plain ``send_message`` when ``SLACKBOT_URL`` is unset.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from api.workflow_engine import WorkflowContext

from packages.bfts_sdk.research import build_bfts_run_input
from packages.bfts_sdk.slack_delivery import resolve_slack_delivery
from packages.bfts_sdk.slack_stream import (
    close_session,
    format_bfts_stream_intro,
    format_idea_markdown,
    format_research_stream_intro,
    open_session,
    post_markdown,
    post_step,
    streaming_available,
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


async def handler(inp: Input, ctx: WorkflowContext) -> dict[str, Any]:
    if not inp.topic or not inp.topic.strip():
        raise ValueError("topic cannot be empty")

    topic = inp.topic.strip()
    thread_key = (inp.thread_key or str(ctx.run_input.get("thread_key") or "")).strip()
    delivery = resolve_slack_delivery(
        explicit_delivery=inp.delivery,
        run_input=ctx.run_input,
        explicit_thread_key=inp.thread_key,
    )
    use_stream = streaming_available() and bool(delivery and thread_key)
    metadata = _slack_metadata(ctx)

    brief_limit = inp.brief_paper_limit or inp.seed_paper_limit or _DEFAULT_BRIEF_LIMIT
    ideation_session = None
    bfts_session = None

    if use_stream and delivery:
        ideation_session = await open_session(
            ctx,
            delivery=delivery,
            thread_key=thread_key,
            metadata=metadata,
            title="Research brief & idea",
            header="scientist · research",
            step_name="open_slack_ideation_stream",
        )
        if ideation_session:
            await post_markdown(
                ctx,
                ideation_session,
                format_research_stream_intro(topic),
                step_name="stream_ideation_intro",
            )
            await post_step(
                ctx,
                ideation_session,
                step_id="literature",
                title="Literature search",
                status="in_progress",
                details=f"Topic: {topic}",
                step_name="stream_literature_start",
            )

    brief_result = await _run_research_brief(ctx, topic=topic, limit=brief_limit)
    brief_markdown = ""
    if isinstance(brief_result, dict) and brief_result.get("status") == "completed":
        brief_markdown = str(
            brief_result.get("compact_markdown")
            or brief_result.get("markdown")
            or ""
        )
        if ideation_session:
            await post_step(
                ctx,
                ideation_session,
                step_id="literature",
                title="Research brief",
                status="complete",
                output=f"{brief_result.get('results_count', 0)} papers",
                step_name="stream_literature_done",
            )
            if brief_markdown:
                await post_markdown(
                    ctx,
                    ideation_session,
                    brief_markdown,
                    step_name="stream_brief_markdown",
                )
    elif ideation_session:
        await post_step(
            ctx,
            ideation_session,
            step_id="literature",
            title="Research brief",
            status="error",
            output=str(brief_result.get("error") or brief_result.get("status")),
            step_name="stream_literature_failed",
        )

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

    if ideation_session:
        await post_step(
            ctx,
            ideation_session,
            step_id="idea",
            title="Synthesize research idea",
            status="in_progress",
            step_name="stream_ideation_start",
        )

    ideation_child = await ctx.start_workflow(
        "start_ideation",
        workflow_name="ideation",
        run_input=ideation_input,
        trigger_key=f"{ctx.run_id}:ideation",
        eager_start=True,
    )
    ideation_result = await ctx.wait_for_workflow(
        "wait_ideation",
        run_id=ideation_child["run_id"],
    )
    ideation_output = _child_workflow_output(ideation_result)
    idea = ideation_output.get("idea")
    if not isinstance(idea, dict) or not idea.get("Title"):
        if ideation_session:
            await post_step(
                ctx,
                ideation_session,
                step_id="idea",
                title="Ideation failed",
                status="error",
                output=str(
                    ideation_result.get("error_text")
                    if isinstance(ideation_result, dict)
                    else ideation_result
                ),
                step_name="stream_ideation_failed",
            )
            await close_session(ctx, ideation_session, step_name="close_ideation_stream_error")
        raise RuntimeError(
            "ideation child did not return a valid idea; "
            f"status={ideation_result.get('status') if isinstance(ideation_result, dict) else None}"
        )

    if ideation_session:
        await post_step(
            ctx,
            ideation_session,
            step_id="idea",
            title="Research idea",
            status="complete",
            step_name="stream_ideation_done",
        )
        await post_markdown(
            ctx,
            ideation_session,
            format_idea_markdown(idea),
            step_name="stream_idea_markdown",
        )
        await close_session(ctx, ideation_session, step_name="close_ideation_stream")

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
    if use_stream and delivery:
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

    ctx.log(
        "bfts_research_started",
        ideation_run_id=ideation_child["run_id"],
        bfts_run_id=bfts_child["run_id"],
        slack_stream=bool(slack_stream_session_id),
        num_seeds=bfts_run_input["num_seeds"],
        num_drafts=bfts_run_input["num_drafts"],
        num_workers=bfts_run_input["num_workers"],
    )

    return {
        "topic": topic,
        "ideation_run_id": ideation_child["run_id"],
        "bfts_run_id": bfts_child["run_id"],
        "idea": idea,
        "brief_document_id": brief_result.get("brief_document_id"),
        "brief_results_count": brief_result.get("results_count"),
        "seed_papers": ideation_output.get("seed_papers"),
        "papers_persisted": ideation_output.get("papers_persisted"),
        "bfts_run_input": bfts_run_input,
        "slack_stream_session_id": slack_stream_session_id,
        "slack_streaming": bool(slack_stream_session_id),
    }
