"""Slack native streaming (agent-sessions) for BFTS workflows.

Uses upstream ``api.slackbot_client`` when the API pod has ``SLACKBOT_URL``
configured. Falls back silently so CLI / non-Slack runs are unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from api.workflow_engine import WorkflowContext

# slackbot clips large markdown posts
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


def format_research_stream_intro(topic: str) -> str:
    """Opening copy for the ideation Slack stream (message 1 of 2)."""
    topic_line = topic.strip()
    return (
        "**Step 1 of 2 — Research**\n"
        f"Literature review and experiment idea for _{topic_line}_. "
        "BFTS tree search starts in a **separate message** when this finishes."
    )


def format_bfts_stream_intro(idea_title: str) -> str:
    """Opening copy for the BFTS Slack stream (message 2 of 2)."""
    label = idea_title.strip() or "(untitled)"
    return (
        "**Step 2 of 2 — BFTS**\n"
        f"Long-running tree search for **{label}**. Live progress below."
    )


def format_idea_markdown(idea: dict[str, Any]) -> str:
    """Compact structured idea block for the ideation stream."""
    title = idea.get("Title") or idea.get("Name") or "(untitled)"
    hypothesis = (idea.get("Short Hypothesis") or "").strip()
    experiments = idea.get("Experiments") or []
    exp_lines: list[str] = []
    if isinstance(experiments, list):
        exp_lines = [str(x).strip() for x in experiments if x]
    elif experiments:
        exp_lines = [str(experiments).strip()]
    parts = ["**Research idea**", f"**{title}**"]
    if hypothesis:
        parts.append(hypothesis)
    if exp_lines:
        parts.append("")
        parts.extend(f"• {line}" for line in exp_lines[:4])
    return "\n".join(parts)
