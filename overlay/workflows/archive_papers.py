"""Workflow: batch-archive Semantic Scholar PDFs.

Calls :func:`centaur_lab.paper_archive.archive_paper_to_pool` for each
paper id, passing the workflow's pool (``ctx._pool``) directly. The
helper handles fetch → download → parse → persist; per-paper failures
land in the result payload but never abort the batch. Programming
errors (asyncpg pool down, missing DATABASE_URL, migrations not
applied) propagate so the run is marked failed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from api.workflow_engine import WorkflowContext

from centaur_lab.paper_archive import archive_paper_to_pool
from tools.semantic_scholar.client import SemanticScholarClient

WORKFLOW_NAME = "archive_papers"


@dataclass
class Input:
    """Runtime options for the ``archive_papers`` workflow."""

    paper_ids: list[str]
    source_url_overrides: dict[str, str] = field(default_factory=dict)


async def handler(inp: Input, ctx: WorkflowContext) -> dict[str, Any]:
    if not inp.paper_ids:
        ctx.log("archive_papers_skipped_empty")
        return {"status": "skipped", "reason": "no_paper_ids"}

    client = SemanticScholarClient()
    results: list[dict[str, Any]] = []
    for paper_id in inp.paper_ids:
        override = inp.source_url_overrides.get(paper_id)
        result = await archive_paper_to_pool(client, ctx._pool, paper_id, source_url=override)
        results.append(result)
        ctx.log(
            "archive_papers_item",
            paper_id=paper_id,
            status=result.get("status"),
            parser_used=result.get("parser_used"),
            reason=result.get("reason"),
        )

    archived = sum(1 for r in results if r.get("status") == "completed")
    noop = sum(1 for r in results if r.get("status") == "noop")
    skipped = sum(1 for r in results if r.get("status") == "skipped")
    failed = sum(1 for r in results if r.get("status") == "error")

    ctx.log(
        "archive_papers_completed",
        papers_archived=archived,
        papers_noop=noop,
        papers_skipped=skipped,
        papers_failed=failed,
    )
    return {
        "status": "completed",
        "papers_archived": archived,
        "papers_noop": noop,
        "papers_skipped": skipped,
        "papers_failed": failed,
        "results": results,
    }
