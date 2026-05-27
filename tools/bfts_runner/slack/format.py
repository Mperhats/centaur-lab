"""Slack message formatting for BFTS workflow notifications."""

from __future__ import annotations

from typing import Any


def slack_mention_prefix(delivery: dict[str, Any] | None) -> str:
    if not delivery:
        return ""
    user_id = delivery.get("recipient_user_id")
    if user_id:
        return f"<@{user_id}> "
    return ""


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
        f"{mention}*{headline}*",
        f"Run `{orchestrator_run_id}` did not complete successfully.",
    ]
    if child_run_id:
        wf = f" ({child_workflow})" if child_workflow else ""
        lines.append(f"Child `{child_run_id}`{wf}.")
    lines.extend(["", f"```\n{error_text.strip()}\n```"])
    return "\n".join(lines)


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


def format_search_config_line(
    *,
    num_drafts: int,
    num_seeds: int,
    num_workers: int,
    sources: dict[str, str],
) -> str:
    """One-line resolved search config for Slack kickoff (postmortem clarity)."""

    def _src(field: str) -> str:
        return sources.get(field) or "?"

    return (
        f"config: {num_drafts} trees (num_drafts, {_src('num_drafts')}), "
        f"{num_seeds} seeds/tree (num_seeds, {_src('num_seeds')}), "
        f"{num_workers} workers (num_workers, {_src('num_workers')})"
    )


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


def format_research_brief_thread_message(
    *,
    topic: str,
    markdown: str,
    run_id: str | None = None,
) -> str:
    """Return compact lit-review mrkdwn for a plain thread post (no wrappers)."""
    _ = run_id
    body = markdown.strip()
    if body:
        return body
    display_topic = topic.strip()
    if display_topic:
        return f"*Literature* — {display_topic}\n\n_No papers found._"
    return ""


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
    parts = ["*Research idea*", f"*{title}*"]
    if hypothesis:
        parts.append(hypothesis)
    if exp_lines:
        parts.append("")
        parts.extend(f"• {line}" for line in exp_lines[:4])
    return "\n".join(parts)
