"""Slack thread posting and delivery resolution for BFTS workflows."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from tools.bfts_runner.slack.format import format_failure_thread_message

if TYPE_CHECKING:
    from api.workflow_engine import WorkflowContext

_MAX_THREAD_CHARS = 39_000
_TERMINAL_FAILURE_STATUSES = frozenset({"failed", "cancelled", "failed_permanent"})
_MAX_ERROR_SNIPPET_CHARS = 1500


def delivery_from_thread_key(thread_key: str) -> dict[str, Any] | None:
    """Map a Slack ``thread_key`` to a minimal delivery dict.

    Supports both legacy ``slack:<channel>:<thread_ts>`` and the
    production ``slack:<team_id>:<channel_id>:<thread_ts>`` shape.
    """
    parts = thread_key.strip().split(":")
    if not parts or parts[0] != "slack":
        return None
    if len(parts) == 3 and parts[1] and parts[2]:
        channel, thread_ts, team_id = parts[1], parts[2], None
    elif len(parts) >= 4 and parts[1] and parts[2] and parts[3]:
        team_id, channel, thread_ts = parts[1], parts[2], parts[3]
    else:
        return None
    out: dict[str, Any] = {
        "platform": "slack",
        "channel": channel,
        "thread_ts": thread_ts,
    }
    if team_id:
        out["recipient_team_id"] = team_id
    return out


def enrich_run_input_from_headers(
    *,
    header_thread_key: str | None,
    run_input: dict[str, Any],
) -> dict[str, Any]:
    """Merge ``X-Centaur-Thread-Key`` into workflow input when body omits it.

    Sandboxes send the header on every ``call workflow run``; without this
    enrichment, ``bfts_root`` cannot post back into the user's Slack thread.
    """
    out = dict(run_input)
    header_tk = (header_thread_key or "").strip()
    if header_tk and not str(out.get("thread_key") or "").strip():
        out["thread_key"] = header_tk

    delivery_raw = out.get("delivery")
    delivery = dict(delivery_raw) if isinstance(delivery_raw, dict) else {}
    if str(delivery.get("platform") or "").strip().lower() != "slack":
        delivery = {}

    derived = delivery_from_thread_key(
        str(out.get("thread_key") or header_tk or ""),
    )
    if derived:
        delivery = {**derived, **delivery}

    if delivery:
        out["delivery"] = delivery
    elif "delivery" in out and not delivery:
        del out["delivery"]

    return out


def resolve_slack_delivery(
    *,
    explicit_delivery: dict[str, Any] | None,
    run_input: dict[str, Any],
    explicit_thread_key: str | None = None,
) -> dict[str, Any] | None:
    """Return a Slack delivery dict when the run should notify a thread."""
    raw = dict(explicit_delivery or run_input.get("delivery") or {})
    if str(raw.get("platform") or "").strip().lower() != "slack":
        raw = {}
    if not raw.get("channel"):
        for key in (explicit_thread_key, run_input.get("thread_key")):
            if not key:
                continue
            derived = delivery_from_thread_key(str(key))
            if derived:
                raw = {**derived, **raw}
                break
    channel = raw.get("channel") or raw.get("channel_id")
    if not channel:
        return None
    out: dict[str, Any] = {
        "platform": "slack",
        "channel": str(channel),
    }
    if raw.get("thread_ts"):
        out["thread_ts"] = str(raw["thread_ts"])
    recipient = raw.get("recipient_user_id") or raw.get("user_id")
    if recipient:
        out["recipient_user_id"] = str(recipient)
    return out


async def enrich_slack_delivery_recipient(
    ctx: WorkflowContext,
    delivery: dict[str, Any] | None,
    *,
    thread_key: str | None,
) -> dict[str, Any] | None:
    """Fill ``recipient_user_id`` from the Slack thread when omitted."""
    if not delivery or delivery.get("recipient_user_id") or not thread_key:
        return delivery
    from api.agent import _get_latest_thread_user_id

    user_id = await ctx.step(
        "resolve_slack_recipient",
        lambda: _get_latest_thread_user_id(thread_key),
    )
    if user_id:
        return {**delivery, "recipient_user_id": str(user_id)}
    return delivery


def workflow_run_failed(run: dict[str, Any] | None) -> bool:
    """True when ``wait_for_workflow`` returned a terminal non-success run."""
    if not isinstance(run, dict):
        return False
    status = str(run.get("status") or "")
    if status == "completed":
        return False
    return status in _TERMINAL_FAILURE_STATUSES or status not in (
        "",
        "running",
        "waiting",
        "queued",
        "sleeping",
    )


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
