"""Slack delivery helpers for BFTS workflows.

- **Plain thread posts** (`post_thread_message`): research brief, research idea.
- **Agent-session streams** (`open_session`, …): long-running BFTS progress only.

Uses upstream ``api.slackbot_client`` when the API pod has ``SLACKBOT_URL``
configured. Falls back silently so CLI / non-Slack runs are unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from packages.bfts_sdk.slack_delivery import slack_mention_prefix

if TYPE_CHECKING:
    from api.workflow_engine import WorkflowContext

_TERMINAL_FAILURE_STATUSES = frozenset({"failed", "cancelled", "failed_permanent"})
_MAX_ERROR_SNIPPET_CHARS = 1500

# slackbot clips large markdown posts
_MAX_STREAM_CHARS = 11_000
# Slack ``chat.postMessage`` text fallback limit (leave headroom for blocks)
_MAX_THREAD_CHARS = 39_000


@dataclass(frozen=True)
class SlackStreamTarget:
    session_id: str


def workflow_run_failed(run: dict[str, Any] | None) -> bool:
    """True when ``wait_for_workflow`` returned a terminal non-success run."""
    if not isinstance(run, dict):
        return False
    status = str(run.get("status") or "")
    if status == "completed":
        return False
    return status in _TERMINAL_FAILURE_STATUSES or status not in ("", "running", "waiting", "queued", "sleeping")


def workflow_run_error_text(run: dict[str, Any] | None) -> str:
    """Best-effort error string from a workflow run dict."""
    if not isinstance(run, dict):
        return "unknown error"
    for key in ("error_text", "error"):
        val = run.get(key)
        if val is not None and str(val).strip():
            return str(val).strip()[:_MAX_ERROR_SNIPPET_CHARS]
    status = str(run.get("status") or "unknown")
    return f"no error_text on run (status={status})"


def format_failure_thread_message(
    *,
    delivery: dict[str, Any],
    headline: str,
    orchestrator_run_id: str,
    error_text: str,
    child_run_id: str | None = None,
    child_workflow: str | None = None,
) -> str:
    """Plain-thread failure notice (always @-mentions when delivery has user id)."""
    mention = slack_mention_prefix(delivery)
    lines = [
        f"{mention}**{headline}**",
        f"Run `{orchestrator_run_id}` did not complete successfully.",
    ]
    if child_run_id:
        wf = f" ({child_workflow})" if child_workflow else ""
        lines.append(f"Child `{child_run_id}`{wf}.")
    lines.extend(["", f"```\n{error_text.strip()}\n```"])
    return "\n".join(lines)


async def notify_thread_failure(
    ctx: WorkflowContext,
    *,
    delivery: dict[str, Any] | None,
    headline: str,
    orchestrator_run_id: str,
    error_text: str,
    step_name: str,
    child_run_id: str | None = None,
    child_workflow: str | None = None,
) -> None:
    """Post a failure notice to the Slack thread (best-effort)."""
    if not delivery:
        return
    await post_thread_message(
        ctx,
        delivery=delivery,
        text=format_failure_thread_message(
            delivery=delivery,
            headline=headline,
            orchestrator_run_id=orchestrator_run_id,
            error_text=error_text,
            child_run_id=child_run_id,
            child_workflow=child_workflow,
        ),
        step_name=step_name,
        log_event="slack_failure_thread_post_failed",
    )


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


def streaming_available() -> bool:
    try:
        from api import slackbot_client
    except ImportError:
        return False
    return slackbot_client.enabled()


async def post_thread_message(
    ctx: WorkflowContext,
    *,
    delivery: dict[str, Any],
    text: str,
    step_name: str,
    log_event: str = "slack_thread_post_failed",
) -> None:
    """Post a plain ``chat.postMessage`` into the Slack thread (checkpointed)."""
    channel = str(delivery.get("channel") or "").strip()
    body = text.strip()
    if not channel or not body:
        return
    if len(body) > _MAX_THREAD_CHARS:
        body = body[: _MAX_THREAD_CHARS - 13] + "\n// truncated"
    thread_ts = delivery.get("thread_ts")

    async def _post() -> dict[str, Any]:
        from api.app import get_tool_manager

        tm = get_tool_manager()
        args: dict[str, Any] = {
            "channel": channel,
            "text": body,
            "no_attribution": True,
        }
        if thread_ts:
            args["thread_ts"] = str(thread_ts)
        raw = await tm.call_tool("slack", "send_message", args)
        import json as _json

        try:
            result = _json.loads(raw) if isinstance(raw, str) else raw
        except (ValueError, TypeError):
            result = {"raw": raw}
        if isinstance(result, dict) and result.get("error"):
            raise RuntimeError(str(result["error"]))
        return result if isinstance(result, dict) else {"raw": result}

    try:
        await ctx.step(step_name, _post, step_kind="slack_post")
    except Exception as exc:
        ctx.log(log_event, channel=channel, error=repr(exc))


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


def format_research_brief_thread_message(*, topic: str, markdown: str) -> str:
    """Plain-thread research brief (not an agent-session stream)."""
    body = markdown.strip()
    if not body:
        return ""
    return f"**Research brief** — _{topic.strip()}_\n\n{body}"


def format_bfts_stream_intro(idea_title: str) -> str:
    """Opening copy for the BFTS-only Slack agent-session stream."""
    label = idea_title.strip() or "(untitled)"
    return (
        f"**BFTS tree search** — **{label}**\n"
        "Live tree progress below."
    )


def format_idea_markdown(idea: dict[str, Any]) -> str:
    """Compact structured idea block for a plain thread post."""
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
