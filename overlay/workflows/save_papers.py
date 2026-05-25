"""Workflow: persist Semantic Scholar papers as company context documents.

Given a list of Semantic Scholar paper IDs, fetch each paper's metadata via
the ``semantic_scholar`` tool client and project it into a
``source_type="paper"`` row in ``company_context_documents``. The workflow is
on-demand only (no ``SCHEDULE``) and is the persistence step that other
research workflows (e.g. ``research_brief``) drive.

Per-paper failures from the upstream API are logged and recorded in the
result payload, but do not abort the run; unexpected exceptions propagate so
the run is marked failed.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

if TYPE_CHECKING:
    from api.workflow_engine import WorkflowContext

from _paper_document import build_paper_document, upsert_document

from tools.semantic_scholar.client import SemanticScholarClient

WORKFLOW_NAME = "save_papers"


@dataclass
class Input:
    """Runtime options for the ``save_papers`` workflow."""

    paper_ids: list[str]
    query: str | None = None


async def handler(inp: Input, ctx: WorkflowContext) -> dict[str, Any]:
    """Fetch each paper from Semantic Scholar and upsert it as a context document."""
    if not inp.paper_ids:
        ctx.log("save_papers_skipped_empty")
        return {"status": "skipped", "reason": "no_paper_ids"}

    client = SemanticScholarClient()
    results: list[dict[str, Any]] = []
    try:
        for paper_id in inp.paper_ids:
            try:
                paper = client.get_paper(paper_id)
            except RuntimeError as exc:
                error_message = str(exc)
                ctx.log(
                    "save_papers_paper_failed",
                    paper_id=paper_id,
                    error=error_message,
                )
                results.append(
                    {
                        "paperId": paper_id,
                        "status": "failed",
                        "error": error_message,
                    }
                )
                continue

            document = build_paper_document(paper, query=inp.query)
            action = await upsert_document(ctx._pool, document)
            results.append(
                {
                    "paperId": paper_id,
                    "document_id": document["document_id"],
                    "status": action,
                }
            )
    finally:
        client.close()

    papers_inserted = sum(1 for r in results if r.get("status") == "inserted")
    papers_updated = sum(1 for r in results if r.get("status") == "updated")
    papers_noop = sum(1 for r in results if r.get("status") == "noop")
    papers_failed = sum(1 for r in results if r.get("status") == "failed")

    ctx.log(
        "save_papers_completed",
        papers_inserted=papers_inserted,
        papers_updated=papers_updated,
        papers_noop=papers_noop,
        papers_failed=papers_failed,
    )

    return {
        "status": "completed",
        "papers_inserted": papers_inserted,
        "papers_updated": papers_updated,
        "papers_noop": papers_noop,
        "papers_failed": papers_failed,
        "results": results,
    }
