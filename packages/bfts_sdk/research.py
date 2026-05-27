"""Research-run defaults for ideation → BFTS handoff.

Operators should not need to pass ``num_seeds`` / ``num_drafts`` manually for
Slack-driven science runs. Workflows call ``build_bfts_run_input`` so
``bfts_root`` receives explicit ``input``-tier hyperparams (visible in kickoff
as ``(num_seeds, input)``) instead of silently inheriting Helm ``BFTS_*`` env.

Defaults are tuned for iron-proxy's ~30s upstream timeout: fewer parallel
trees/workers reduce Anthropic burst load.
"""

from __future__ import annotations

from typing import Any

# Seed re-eval per tree (F.4). Non-zero for real research; smoke uses 0 via env.
DEFAULT_RESEARCH_NUM_SEEDS = 3
# Parallel draft trees at the root. 2 limits 2x num_workers LLM burst vs default 4.
DEFAULT_RESEARCH_NUM_DRAFTS = 2
# Concurrent expand_one children per tree. 1 avoids 8-way proxy storms (4x2).
DEFAULT_RESEARCH_NUM_WORKERS = 1


def build_bfts_run_input(
    *,
    idea: dict[str, Any],
    run_input: dict[str, Any] | None = None,
    thread_key: str | None = None,
    delivery: dict[str, Any] | None = None,
    num_seeds: int | None = None,
    num_drafts: int | None = None,
    num_workers: int | None = None,
) -> dict[str, Any]:
    """Build a ``bfts_root`` POST body with research defaults and Slack context.

    Merges ``thread_key`` / ``delivery`` from ``run_input`` (workflow parent
    or API-enriched body) so sandbox ``X-Centaur-Thread-Key`` propagation is
    not required for thread posts.
    """
    parent = dict(run_input or {})
    out: dict[str, Any] = {
        "idea": idea,
        "num_seeds": (
            num_seeds if num_seeds is not None else DEFAULT_RESEARCH_NUM_SEEDS
        ),
        "num_drafts": (
            num_drafts if num_drafts is not None else DEFAULT_RESEARCH_NUM_DRAFTS
        ),
        "num_workers": (
            num_workers if num_workers is not None else DEFAULT_RESEARCH_NUM_WORKERS
        ),
    }
    tk = (thread_key or parent.get("thread_key") or "").strip()
    if tk:
        out["thread_key"] = tk
    if delivery is not None:
        out["delivery"] = delivery
    elif isinstance(parent.get("delivery"), dict):
        out["delivery"] = parent["delivery"]
    return out
