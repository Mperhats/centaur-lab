"""Workflow: expand one BFTS node — child of ``bfts_tree``.

Wraps the existing :func:`packages.bfts_sdk.expand.expand_node` pipeline
(per-LLM-call ``ctx.step`` checkpoints) in its own workflow run so the
tree controller can fan out N expansions per iteration via
``ctx.start_workflow("bfts_expand_one", ..., eager_start=True)`` when
``BFTS_EXPAND_MODE=child`` (Phase 4).

Phase 5a inline mode runs the same pipeline inside ``bfts_tree`` via
:func:`packages.bfts_sdk.expand_runner.run_expand_for_node`; this workflow
remains for manual replay, tests, and ``BFTS_EXPAND_MODE=child`` rollback.

See ``docs/bfts-phase5-orchestration.md``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from api.workflow_engine import WorkflowContext

from packages.bfts_sdk.config import resolve_llm_api_key, resolve_llm_settings
from packages.bfts_sdk.expand_runner import run_expand_for_node
from packages.bfts_sdk.schema import assert_bfts_schema_present

WORKFLOW_NAME = "bfts_expand_one"
SCHEDULE: dict[str, Any] = {}


@dataclass(frozen=True)
class Input:
    """Per-child workflow input."""

    run_id: str
    node_id: str
    sandbox_id: str
    working_dir: str
    parent_node: dict[str, Any] | None = None
    idea: dict[str, Any] = field(default_factory=dict)
    llm_api_key_secret: str | None = None
    draft_model: str | None = None
    feedback_model: str | None = None
    vlm_model: str | None = None
    prior_attempts_window: int | None = None
    seed_override: int | None = None
    is_seed_node: bool = False


async def handler(inp: Input, ctx: WorkflowContext) -> dict[str, Any]:
    await ctx.step(
        "preflight_schema_check",
        lambda: assert_bfts_schema_present(ctx._pool),
    )

    llm = resolve_llm_settings(
        draft_model=inp.draft_model,
        feedback_model=inp.feedback_model,
        vlm_model=inp.vlm_model,
        llm_api_key_secret=inp.llm_api_key_secret,
    )
    llm_api_key = resolve_llm_api_key(llm.llm_api_key_secret)

    return await run_expand_for_node(
        ctx,
        ctx._pool,
        run_id=inp.run_id,
        node_id=inp.node_id,
        sandbox_id=inp.sandbox_id,
        working_dir=inp.working_dir,
        parent_node=inp.parent_node,
        idea=inp.idea,
        llm_api_key=llm_api_key,
        draft_model=llm.draft_model,
        feedback_model=llm.feedback_model,
        vlm_model=llm.vlm_model,
        prior_attempts_window=inp.prior_attempts_window,
        seed_override=inp.seed_override,
        inline=False,
    )
