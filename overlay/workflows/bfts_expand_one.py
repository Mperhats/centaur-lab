"""Workflow: expand one BFTS node — child of ``bfts_tree``.

Wraps the existing :func:`_bfts_expand.expand_node` pipeline (per-LLM-call
``ctx.step`` checkpoints, see ``_bfts_expand.py``) in its own workflow run
so the tree controller can fan out N expansions per iteration via
``ctx.start_workflow("bfts_expand_one", ..., eager_start=True)`` and then
``ctx.wait_for_workflow(...)`` on each child.

This is the "Design B-lite" model from
``docs/superpowers/bfts-research-suggestion.md`` analysis (kept the Sakana
selector + structured per-LLM-call expansion pipeline; only fanned out the
per-node expansion calls). Phase 4h.3 wires the fan-out in
``bfts_tree.handler``; until that lands this module exists as a callable
workflow with no callers.

Invariants the controller relies on:

* The child workflow PERSISTS the node row (``update_node_metric`` +
  optional ``mark_buggy_plots``) before returning, so the tree controller
  can re-query the DB to pick up the result without trusting the
  workflow's return-value envelope.
* On any expansion error the workflow propagates — no try/except, no
  workflow-level retry. The expand pipeline already has internal retry
  semantics for transient LLM / executor errors; a wrapper retry would
  burn LLM budget without diagnostic value.
* Each expansion runs in a controller-supplied per-node ``working_dir``
  (see ``_bfts_expand.ExpandContext.working_dir`` and Phase 4h.1's
  ``working_dir`` parameter on ``bfts_executor.exec_python``); the
  sandbox is shared per tree but the per-node subdirectory keeps
  ``runfile.py`` / ``experiment_data.npy`` / ``*.png`` from racing.

``SCHEDULE`` is the empty dict: this workflow is only invoked via
``ctx.start_workflow``, never on a timer. The workflow loader registers
the module by its top-level name (no leading underscore) and
``WORKFLOW_NAME``.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

if TYPE_CHECKING:
    from api.workflow_engine import WorkflowContext

from _bfts_config import (
    DEFAULT_PRIOR_ATTEMPTS_WINDOW,
    resolve_llm_api_key,
    resolve_llm_settings,
)
from _bfts_expand import ExpandContext, expand_node
from _bfts_state import (
    list_recent_node_summaries,
    mark_buggy_plots,
    update_node_metric,
)

WORKFLOW_NAME = "bfts_expand_one"
SCHEDULE: dict[str, Any] = {}


@dataclass(frozen=True)
class Input:
    """Per-child workflow input.

    All identifiers are controller-assigned (the parent
    ``bfts_tree.handler`` generates ``node_id`` via ``uuid4().hex`` and
    inserts the empty node row BEFORE starting this workflow, so the
    DB row exists by the time we update it).

    ``parent_node`` is the full row dict (``_bfts_state.list_nodes_for_run``
    shape) — passed by value rather than re-queried inside the child so
    the workflow input is self-contained and the debug/improve prompts
    have everything they need.

    Model overrides are optional; ``resolve_llm_settings`` falls back to
    ``BFTS_*`` env (from ``api.extraEnv``) then code defaults so the
    child inherits the parent tree's resolved configuration.
    """

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
    # F.2: forwarded by ``bfts_tree.handler``. None falls back to the
    # ``DEFAULT_PRIOR_ATTEMPTS_WINDOW`` constant so a standalone-launched
    # ``bfts_expand_one`` (tests, manual ``POST /api/workflows/...``) still
    # behaves sensibly. Set to 0 to disable memory injection.
    prior_attempts_window: int | None = None


async def handler(inp: Input, ctx: "WorkflowContext") -> dict[str, Any]:
    llm = resolve_llm_settings(
        draft_model=inp.draft_model,
        feedback_model=inp.feedback_model,
        vlm_model=inp.vlm_model,
        llm_api_key_secret=inp.llm_api_key_secret,
    )
    llm_api_key = resolve_llm_api_key(llm.llm_api_key_secret)
    pool = ctx._pool

    # F.2: load the prior-attempts memory window before constructing
    # ExpandContext. Wrapped in ``ctx.step`` so the row snapshot is
    # checkpointed alongside the other LLM-call steps and a workflow
    # restart sees the same memory the original run did.
    window = (
        inp.prior_attempts_window
        if inp.prior_attempts_window is not None
        else DEFAULT_PRIOR_ATTEMPTS_WINDOW
    )
    prior_attempts = await ctx.step(
        "load_prior_attempts",
        lambda: list_recent_node_summaries(
            pool,
            run_id=inp.run_id,
            limit=window,
            exclude_node_id=inp.node_id,
        ),
    )

    expand_ctx = ExpandContext(
        sandbox_id=inp.sandbox_id,
        parent_node=inp.parent_node,
        idea=inp.idea,
        llm_api_key=llm_api_key,
        node_id=inp.node_id,
        working_dir=inp.working_dir,
        draft_model=llm.draft_model,
        feedback_model=llm.feedback_model,
        vlm_model=llm.vlm_model,
        prior_attempts=prior_attempts or [],
    )

    # No outer ctx.step / try/except: expand_node has its own per-LLM-call
    # ctx.step checkpoints (draft_propose, draft_exec, bug_judge, …) so
    # mid-pipeline restarts resume cleanly. Failures propagate to the
    # workflow engine, which marks this run failed; the parent tree's
    # wait_for_workflow surfaces the failure.
    result = await expand_node(ctx=ctx, expand_ctx=expand_ctx)

    await ctx.step(
        "update_node",
        lambda: update_node_metric(
            pool,
            node_id=inp.node_id,
            term_out=result["term_out"],
            exec_time_seconds=result["exec_time_seconds"],
            exc_type=result["exc_type"],
            exc_info=result["exc_info"],
            exc_stack=result["exc_stack"],
            metric=result["metric"],
            is_buggy=result["is_buggy"],
            analysis=result["analysis"],
            plan=result["plan"],
            code=result["code"],
            # parse_*/plot_* are only populated on the good path.
            # ``update_node_metric`` uses COALESCE for the TEXT columns
            # so passing None preserves the (empty) value written by
            # insert_node; JSONB columns get SQL NULL, matching the
            # contract documented on update_node_metric.
            parse_metrics_code=result.get("parse_metrics_code"),
            parse_term_out=result.get("parse_term_out"),
            plot_code=result.get("plot_code"),
            plot_term_out=result.get("plot_term_out"),
        ),
    )
    if "is_buggy_plots" in result:
        await ctx.step(
            "mark_buggy_plots",
            lambda: mark_buggy_plots(
                pool,
                node_id=inp.node_id,
                is_buggy_plots=bool(result["is_buggy_plots"]),
                plot_analyses=result.get("plot_analyses"),
                vlm_feedback_summary=result.get("vlm_feedback_summary"),
            ),
        )

    return {
        "node_id": inp.node_id,
        "is_buggy": bool(result["is_buggy"]),
        "stage_name": result.get("stage_name"),
    }
