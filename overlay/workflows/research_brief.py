"""Workflow: build and persist a research brief from Semantic Scholar.

Searches the Graph API for ``query``, renders a Markdown lit review,
and writes the brief plus its citing papers to
``company_context_documents``. Idempotent on ``(query, year_from)``.

Calls the shared ``persist_research_brief_from_papers`` helper directly
with the workflow's pool (``ctx._pool``) instead of going through the
tool's ``research_brief`` envelope — workflows already own a pool and a
running loop, so the envelope-driven sync/async bridging the tool
method does for agent callers is pure overhead here.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from api.workflow_engine import WorkflowContext

from centaur_lab.brief import persist_research_brief_from_papers
from tools.semantic_scholar.client import (
    MAX_RESEARCH_BRIEF_LIMIT,
    SemanticScholarClient,
)

WORKFLOW_NAME = "research_brief"


@dataclass
class Input:
    """Runtime options for the ``research_brief`` workflow."""

    query: str
    limit: int = 5
    year_from: int | None = None


async def handler(inp: Input, ctx: WorkflowContext) -> dict[str, Any]:
    """Search → render → upsert. Returns the brief envelope minus markdown.

    Soft-skips (``{"status": "skipped", "reason": ...}``) for empty
    query or non-positive limit; everything else is wrapped in
    ``status="completed"``. The brief markdown is recoverable via
    ``brief_document_id`` so we don't carry it through
    ``workflow_runs.output_json`` (which would compound across reruns).
    """
    normalized_query = (inp.query or "").strip()
    if not normalized_query:
        ctx.log("research_brief_skipped", reason="empty_query")
        return {"status": "skipped", "reason": "empty_query"}
    if inp.limit <= 0:
        ctx.log("research_brief_skipped", reason="invalid_limit")
        return {"status": "skipped", "reason": "invalid_limit"}

    clamped_limit = min(inp.limit, MAX_RESEARCH_BRIEF_LIMIT)
    ctx.log(
        "research_brief_starting",
        query=normalized_query,
        limit=clamped_limit,
        year_from=inp.year_from,
    )

    client = SemanticScholarClient()
    # SDK is sync; bounce off-loop so concurrent workflow runs don't
    # serialize on its HTTP retries.
    papers = await asyncio.to_thread(
        client.search_papers,
        query=normalized_query,
        limit=clamped_limit,
        year_from=inp.year_from,
    )

    result = await persist_research_brief_from_papers(
        ctx._pool,
        query=normalized_query,
        papers=papers,
        year_from=inp.year_from,
        limit=clamped_limit,
    )

    ctx.log(
        "research_brief_completed",
        brief_document_id=result["brief_document_id"],
        brief_action=result["brief_action"],
        results_count=result["results_count"],
        papers_inserted=result["papers_inserted"],
        papers_updated=result["papers_updated"],
        papers_noop=result["papers_noop"],
    )

    return {
        "status": "completed",
        **{k: v for k, v in result.items() if k != "markdown"},
    }
