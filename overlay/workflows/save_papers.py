"""Workflow: persist Semantic Scholar papers as company context documents.

Given a list of Semantic Scholar paper IDs, fetch each paper's metadata via
the ``semantic_scholar`` tool client and project it into a
``source_type="paper"`` row in ``company_context_documents``. Always follows
up with a ``research_brief`` row linking the saved papers as children.

Per-paper failures from the upstream API are logged and recorded in the
result payload, but do not abort the run; unexpected exceptions propagate so
the run is marked failed.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from api.workflow_engine import WorkflowContext

from semanticscholar.Paper import Paper

from centaur_lab.brief import persist_research_brief_from_papers
from centaur_lab.metrics import observe_document_size, record_document_change
from centaur_lab.paper_document import build_paper_document, upsert_document
from tools.semantic_scholar.client import SemanticScholarClient

WORKFLOW_NAME = "save_papers"


@dataclass
class Input:
    """Runtime options for the ``save_papers`` workflow."""

    paper_ids: list[str]
    query: str | None = None


def _brief_query_for_save(paper_ids: list[str], explicit: str | None) -> str:
    if explicit and explicit.strip():
        return explicit.strip()
    digest = hashlib.sha256(",".join(sorted(paper_ids)).encode()).hexdigest()[:12]
    return f"save_papers:{digest}"


async def handler(inp: Input, ctx: WorkflowContext) -> dict[str, Any]:
    """Fetch each paper from Semantic Scholar and upsert it as a context document."""
    if not inp.paper_ids:
        ctx.log("save_papers_skipped_empty")
        return {"status": "skipped", "reason": "no_paper_ids"}

    client = SemanticScholarClient()
    results: list[dict[str, Any]] = []
    saved_papers: list[Paper] = []
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

            saved_papers.append(paper)
            document = build_paper_document(paper, query=inp.query)
            observe_document_size(document)
            action = await upsert_document(ctx._pool, document)
            record_document_change(document, action)
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

    payload: dict[str, Any] = {
        "status": "completed",
        "papers_inserted": papers_inserted,
        "papers_updated": papers_updated,
        "papers_noop": papers_noop,
        "papers_failed": papers_failed,
        "results": results,
    }

    if saved_papers:
        brief_query = _brief_query_for_save(inp.paper_ids, inp.query)
        brief_result = await persist_research_brief_from_papers(
            ctx._pool,
            query=brief_query,
            papers=saved_papers,
        )
        payload.update(
            {
                "brief_document_id": brief_result["brief_document_id"],
                "brief_action": brief_result["brief_action"],
                "brief_query": brief_query,
            }
        )
        ctx.log(
            "save_papers_brief_persisted",
            brief_document_id=brief_result["brief_document_id"],
            brief_action=brief_result["brief_action"],
            brief_query=brief_query,
        )

    ctx.log(
        "save_papers_completed",
        papers_inserted=papers_inserted,
        papers_updated=papers_updated,
        papers_noop=papers_noop,
        papers_failed=papers_failed,
    )

    return payload
