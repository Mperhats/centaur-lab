"""Slack agent-session streaming for long-running BFTS progress."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from tools.bfts_runner.slack.post import notify_thread_failure

if TYPE_CHECKING:
    from api.workflow_engine import WorkflowContext

_MAX_STREAM_CHARS = 11_000


@dataclass(frozen=True)
class SlackStreamTarget:
    session_id: str


def streaming_available() -> bool:
    try:
        from api import slackbot_client
    except ImportError:
        return False
    return slackbot_client.enabled()


async def open_session(
    ctx: WorkflowContext,
    *,
    delivery: dict[str, Any],
    thread_key: str,
    metadata: dict[str, Any],
    title: str,
    header: str | None,
    step_name: str,
) -> SlackStreamTarget | None:
    """Open a new Slack stream message in the thread (checkpointed)."""

    async def _open() -> str | None:
        from api import slackbot_client

        if not slackbot_client.enabled():
            return None
        session_id = await slackbot_client.open_agent_session(
            delivery=delivery,
            metadata=metadata,
            thread_key=thread_key,
            title=title,
            header=header,
        )
        return session_id or None

    session_id = await ctx.step(step_name, _open)
    if not session_id:
        return None
    return SlackStreamTarget(session_id=str(session_id))


async def post_markdown(
    ctx: WorkflowContext,
    target: SlackStreamTarget | None,
    markdown: str,
    *,
    step_name: str,
) -> None:
    if not target:
        return
    text = markdown.strip()
    if not text:
        return

    async def _post() -> None:
        from api import slackbot_client

        if len(text) <= _MAX_STREAM_CHARS:
            await slackbot_client.session_text(target.session_id, text)
            return
        await slackbot_client.session_text(
            target.session_id,
            text[:_MAX_STREAM_CHARS] + "\n\n…",
        )

    await ctx.step(step_name, _post)


async def post_step(
    ctx: WorkflowContext,
    target: SlackStreamTarget | None,
    *,
    step_id: str,
    title: str,
    status: str,
    details: str | None = None,
    output: str | None = None,
    step_name: str,
) -> None:
    if not target:
        return

    async def _post() -> None:
        from api import slackbot_client

        await slackbot_client.session_step(
            target.session_id,
            step_id=step_id,
            title=title,
            status=status,
            details=details,
            output=output,
        )

    await ctx.step(step_name, _post)


async def close_session(
    ctx: WorkflowContext,
    target: SlackStreamTarget | None,
    *,
    step_name: str,
) -> None:
    if not target:
        return

    async def _done() -> None:
        from api import slackbot_client

        await slackbot_client.session_done(target.session_id)

    await ctx.step(step_name, _done)


async def notify_run_failure(
    ctx: WorkflowContext,
    *,
    delivery: dict[str, Any] | None,
    stream: SlackStreamTarget | None,
    orchestrator_run_id: str,
    headline: str,
    error_text: str,
    thread_step_name: str,
    child_run_id: str | None = None,
    child_workflow: str | None = None,
) -> None:
    """Thread + optional BFTS stream failure (closes the stream when present)."""
    await notify_thread_failure(
        ctx,
        delivery=delivery,
        headline=headline,
        orchestrator_run_id=orchestrator_run_id,
        error_text=error_text,
        step_name=thread_step_name,
        child_run_id=child_run_id,
        child_workflow=child_workflow,
    )
    if not stream:
        return
    snippet = error_text.strip()[:_MAX_STREAM_CHARS]
    await post_step(
        ctx,
        stream,
        step_id="bfts_failure",
        title=headline,
        status="error",
        output=snippet,
        step_name=f"{thread_step_name}_stream_step",
    )
    await post_markdown(
        ctx,
        stream,
        f"**{headline}**\n\n```\n{snippet}\n```",
        step_name=f"{thread_step_name}_stream_md",
    )
    await close_session(ctx, stream, step_name=f"{thread_step_name}_stream_done")
