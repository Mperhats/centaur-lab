"""Slack delivery helpers for BFTS workflows and workflow run creation."""

from __future__ import annotations

from typing import Any


def delivery_from_thread_key(thread_key: str) -> dict[str, Any] | None:
    """Map ``slack:<channel>:<thread_ts>`` to a minimal delivery dict."""
    parts = thread_key.strip().split(":")
    if len(parts) == 3 and parts[0] == "slack" and parts[1] and parts[2]:
        return {
            "platform": "slack",
            "channel": parts[1],
            "thread_ts": parts[2],
        }
    return None


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


def slack_mention_prefix(delivery: dict[str, Any] | None) -> str:
    if not delivery:
        return ""
    user_id = delivery.get("recipient_user_id")
    if user_id:
        return f"<@{user_id}> "
    return ""


def _metric_snippet(metric_json: dict[str, Any] | None) -> str:
    if not metric_json:
        return "no metric"
    for key in ("metric", "mse", "loss", "score"):
        if key in metric_json and metric_json[key] is not None:
            return f"{key}={metric_json[key]}"
    keys = sorted(metric_json.keys())[:2]
    if not keys:
        return "metric present"
    return ", ".join(f"{k}={metric_json[k]}" for k in keys)


def format_tree_progress_line(
    *,
    tree_index: int,
    status: str | None,
    output: dict[str, Any] | None,
) -> str:
    """One tree line for a Slack progress update."""
    label = f"tree {tree_index}"
    st = status or "unknown"
    if st not in ("completed", "failed", "failed_permanent", "cancelled"):
        return f"• {label}: {st}…"

    if st != "completed":
        err = (output or {}).get("error") or (output or {}).get("error_text")
        suffix = f" — {err}" if err else ""
        return f"• {label}: {st}{suffix}"

    if not isinstance(output, dict):
        return f"• {label}: completed"

    best_id = output.get("best_node_id")
    nodes = output.get("node_count")
    metric = output.get("best_metric_json")
    if isinstance(metric, str):
        try:
            import json as _json

            parsed = _json.loads(metric)
            metric = parsed if isinstance(parsed, dict) else None
        except ValueError:
            metric = None
    if not isinstance(metric, dict):
        metric = None

    parts: list[str] = []
    if best_id:
        parts.append(f"best `{best_id}`")
    if nodes is not None:
        parts.append(f"{nodes} nodes")
    parts.append(_metric_snippet(metric))
    seeds = output.get("seed_aggregate")
    if isinstance(seeds, dict) and seeds.get("aggregate_n"):
        parts.append(f"seeds n={seeds.get('aggregate_n')}")
    return f"• {label}: completed ({', '.join(parts)})"


def format_progress_message(
    *,
    run_id: str,
    phase: str,
    children: list[dict[str, Any]],
    child_results: list[dict[str, Any] | None],
) -> str:
    """Build a multi-line Slack progress post for ``bfts_root``."""
    total = len(children)
    finished = len(child_results)
    header = f"*BFTS progress* `{run_id}`"
    if phase == "launched":
        header += f" — {total} tree{'s' if total != 1 else ''} launched"
    else:
        header += f" — {finished}/{total} tree{'s' if total != 1 else ''} finished"

    lines = [header, ""]
    for pos, child in enumerate(children):
        idx = int(child.get("tree_index", pos))
        if pos < len(child_results):
            res = child_results[pos]
            if not isinstance(res, dict):
                lines.append(format_tree_progress_line(
                    tree_index=idx, status="failed", output=None,
                ))
                continue
            output = res.get("output_json")
            if not isinstance(output, dict):
                output = None
            lines.append(format_tree_progress_line(
                tree_index=idx,
                status=str(res.get("status") or ""),
                output=output,
            ))
        else:
            lines.append(f"• tree {idx}: running…")
    return "\n".join(lines)
