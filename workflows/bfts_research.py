"""Workflow: ideation → ``bfts_root`` with research hyperparam defaults.

Single entry for Slack-driven science: synthesize an idea, persist seed
papers, and start BFTS with explicit ``num_seeds`` / ``num_drafts`` /
``num_workers`` so operators are not dependent on Helm ``BFTS_*`` env alone.
Does **not** wait for ``bfts_root`` to finish (hours); thread delivery is
handled by the child run.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from api.workflow_engine import WorkflowContext

from packages.bfts_sdk.research import build_bfts_run_input
from workflows.ideation import _child_workflow_output

WORKFLOW_NAME = "bfts_research"
SCHEDULE: dict[str, Any] = {}


@dataclass
class Input:
    topic: str
    thread_key: str | None = None
    delivery: dict[str, Any] | None = None
    num_seeds: int | None = None
    num_drafts: int | None = None
    num_workers: int | None = None
    seed_paper_limit: int | None = None
    critic_retries: int = 0
    draft_model: str | None = None
    llm_api_key_secret: str | None = None


async def handler(inp: Input, ctx: WorkflowContext) -> dict[str, Any]:
    if not inp.topic or not inp.topic.strip():
        raise ValueError("topic cannot be empty")

    ideation_input: dict[str, Any] = {"topic": inp.topic.strip()}
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
    ideation_result = await ctx.wait_for_workflow(
        "wait_ideation",
        run_id=ideation_child["run_id"],
    )
    ideation_output = _child_workflow_output(ideation_result)
    idea = ideation_output.get("idea")
    if not isinstance(idea, dict) or not idea.get("Title"):
        raise RuntimeError(
            "ideation child did not return a valid idea; "
            f"status={ideation_result.get('status') if isinstance(ideation_result, dict) else None}"
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
        num_seeds=bfts_run_input["num_seeds"],
        num_drafts=bfts_run_input["num_drafts"],
        num_workers=bfts_run_input["num_workers"],
    )

    return {
        "topic": inp.topic.strip(),
        "ideation_run_id": ideation_child["run_id"],
        "bfts_run_id": bfts_child["run_id"],
        "idea": idea,
        "seed_papers": ideation_output.get("seed_papers"),
        "papers_persisted": ideation_output.get("papers_persisted"),
        "bfts_run_input": bfts_run_input,
    }
