"""Workflow: search Semantic Scholar, then archive every matched paper.

Pure orchestration on top of two existing primitives:

1. ``SemanticScholarClient.search_papers`` — live Graph API query that
   returns a ``list[Paper]``.
2. ``archive_papers`` workflow — owns every DB write
   (``paper_archives`` + ``company_context_documents``) and is
   idempotent on ``(paper_id, pdf_sha256)``.

This handler never opens a pool of its own and never touches Postgres
directly. The persistence layer is the child ``archive_papers`` run,
dispatched via ``ctx.run_workflow``; the parent-child lineage is
observable through ``GET /workflows/runs/<parent>/children``. The same
parent/child pattern is used by ``research_brief.py`` when
``archive=true`` — see the ``ctx.run_workflow`` block at
``research_brief.py:262-266``.

Kept intentionally separate from ``archive_papers`` so each workflow has
a single, declarative input shape (``query`` here, ``paper_ids``
there). Callers that already have concrete paper IDs go straight to
``archive_papers`` — this workflow is for the "find papers about X and
read them" flow where the agent does not yet know which IDs to fetch.

Soft-skips for empty query / non-positive limit / zero search hits;
real upstream failures propagate as the ``search`` call raising. The
child run's full payload (and ``run_id``) is returned in the response
so callers can drill into per-paper archive results.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from api.workflow_engine import WorkflowContext

from tools.semantic_scholar.client import SemanticScholarClient

WORKFLOW_NAME = "search_and_archive_papers"


@dataclass
class Input:
    """Runtime options for the ``search_and_archive_papers`` workflow."""

    query: str
    limit: int = 10
    year_from: int | None = None


async def handler(inp: Input, ctx: WorkflowContext) -> dict[str, Any]:
    """Search S2, then chain ``archive_papers`` over the matched IDs."""
    normalized_query = (inp.query or "").strip()
    if not normalized_query:
        ctx.log("search_and_archive_papers_skipped", reason="empty_query")
        return {"status": "skipped", "reason": "empty_query"}
    if inp.limit <= 0:
        ctx.log("search_and_archive_papers_skipped", reason="invalid_limit")
        return {"status": "skipped", "reason": "invalid_limit"}

    client = SemanticScholarClient()
    # Mirrors ``save_papers.py``'s convention of calling the sync client
    # methods directly from the async handler. ``search_papers`` raises
    # ``RuntimeError`` on upstream API failure; let it propagate so the
    # workflow run is marked failed (matches how ``research_brief``
    # surfaces a non-ok envelope from ``client.research_brief``).
    papers = client.search_papers(
        query=normalized_query,
        limit=inp.limit,
        year_from=inp.year_from,
    )

    paper_ids = [p.paperId for p in papers if getattr(p, "paperId", None)]

    ctx.log(
        "search_and_archive_papers_starting",
        query=normalized_query,
        limit=inp.limit,
        year_from=inp.year_from,
        results_count=len(papers),
        paper_ids_count=len(paper_ids),
    )

    if not paper_ids:
        ctx.log("search_and_archive_papers_skipped", reason="no_paper_ids")
        return {
            "status": "completed",
            "query": normalized_query,
            "limit": inp.limit,
            "year_from": inp.year_from,
            "results_count": len(papers),
            "archive_run_id": None,
            "archive": {"status": "skipped", "reason": "no_paper_ids"},
        }

    archive_run = await ctx.run_workflow(
        "archive",
        workflow_name="archive_papers",
        run_input={"paper_ids": paper_ids},
    )
    archive_output = (
        archive_run.get("output_json") if isinstance(archive_run, dict) else None
    )
    archive_run_id = archive_run.get("run_id") if isinstance(archive_run, dict) else None

    ctx.log(
        "search_and_archive_papers_completed",
        query=normalized_query,
        results_count=len(papers),
        archive_run_id=archive_run_id,
        archive_status=(archive_output or {}).get("status"),
    )

    return {
        "status": "completed",
        "query": normalized_query,
        "limit": inp.limit,
        "year_from": inp.year_from,
        "results_count": len(papers),
        "archive_run_id": archive_run_id,
        "archive": archive_output,
    }
