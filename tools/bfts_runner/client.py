"""Start ``bfts_research`` with Slack thread context from the sandbox JWT.

The deployed Centaur API image may not yet merge ``X-Centaur-Thread-Key``
into workflow bodies at ``POST /workflows/runs``. This tool runs inside the
API process, reads ``thread_key`` from sandbox claims (via
``centaur_sdk.current_thread_key``), and enqueues ``bfts_research`` with
``thread_key`` + ``delivery`` populated so Slack streaming works.
"""

from __future__ import annotations

from typing import Any

from centaur_sdk import current_thread_key
from packages.bfts_sdk.slack_delivery import build_bfts_research_run_input


class BftsRunnerClient:
    """Agent-callable entrypoints for BFTS orchestration."""

    async def start_research(
        self,
        topic: str,
        *,
        num_seeds: int | None = None,
        num_drafts: int | None = None,
        num_workers: int | None = None,
    ) -> dict[str, Any]:
        """Enqueue ``bfts_research`` with Slack delivery wired from this thread."""
        normalized = (topic or "").strip()
        if not normalized:
            return {"ok": False, "error": "topic cannot be empty"}

        try:
            thread_key = current_thread_key()
        except RuntimeError:
            thread_key = ""

        run_input = build_bfts_research_run_input(
            topic=normalized,
            thread_key=thread_key or None,
            num_seeds=num_seeds,
            num_drafts=num_drafts,
            num_workers=num_workers,
        )
        if not run_input.get("thread_key"):
            return {
                "ok": False,
                "error": (
                    "no Slack thread_key in sandbox context; "
                    "run from a Slack thread sandbox"
                ),
            }

        from api.agent import _get_pool
        from api.workflow_engine import create_workflow_run

        pool = _get_pool()
        if pool is None:
            return {"ok": False, "error": "database pool unavailable"}

        result = await create_workflow_run(
            pool,
            workflow_name="bfts_research",
            run_input=run_input,
            trigger_key=None,
            eager_start=True,
        )
        return {
            "ok": True,
            "run_id": result.get("run_id"),
            "idempotent": result.get("idempotent"),
            "thread_key": run_input.get("thread_key"),
            "delivery": run_input.get("delivery"),
        }


def _client() -> BftsRunnerClient:
    return BftsRunnerClient()
