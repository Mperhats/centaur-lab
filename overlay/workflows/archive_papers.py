"""Workflow: batch-archive Semantic Scholar PDFs.

Iterates a list of paper IDs and calls
:meth:`SemanticScholarClient._archive_paper_async` on each — fetch
metadata, download the open-access PDF, parse it via the
``pymupdf4llm``/``pymupdf``/``pypdf`` fallback chain, and persist:

* raw bytes + parsed text + parser metadata in ``paper_archives``
* a ``source_type="paper"`` metadata row in ``company_context_documents``
* a ``source_type="paper_fulltext"`` row parented off the metadata row,
  also in ``company_context_documents``

Per-paper failures and skips (paywalled / oversized) land in the
result payload but never abort the batch. Programming errors
(asyncpg pool down, missing DATABASE_URL) propagate so the run is
marked failed.

Calls the coroutine sibling ``_archive_paper_async`` instead of the
public sync ``archive_paper`` because workflows already run inside
an asyncio loop — wrapping ``asyncio.run`` inside that loop would
raise ``RuntimeError: asyncio.run() cannot be called from a running
event loop``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from api.workflow_engine import WorkflowContext

from tools.semantic_scholar.client import SemanticScholarClient

WORKFLOW_NAME = "archive_papers"


@dataclass
class Input:
    """Runtime options for the ``archive_papers`` workflow."""

    paper_ids: list[str]
    source_url_overrides: dict[str, str] = field(default_factory=dict)


class _WorkflowPoolAdapter:
    """Async context manager that yields a pre-acquired pool unchanged.

    The workflow already owns ``ctx._pool``; the archive flow's default
    ``_acquire_pool_for_archive`` would open a fresh asyncpg connection
    per call. Overriding the bound method with this adapter on a
    per-batch basis lets the same pool back the noop check, three
    upserts, and (most importantly) the loop over multiple paper IDs
    without reopening connections.
    """

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    async def __aenter__(self) -> Any:
        return self._pool

    async def __aexit__(self, *exc: Any) -> None:
        return None


async def handler(inp: Input, ctx: WorkflowContext) -> dict[str, Any]:
    if not inp.paper_ids:
        ctx.log("archive_papers_skipped_empty")
        return {"status": "skipped", "reason": "no_paper_ids"}

    client = SemanticScholarClient()
    # Reuse the workflow's pool for every per-paper archive call. The
    # archive flow expects an async context manager from
    # ``_acquire_pool_for_archive``, so wrap the pool in a no-op adapter.
    client._acquire_pool_for_archive = lambda: _WorkflowPoolAdapter(ctx._pool)  # type: ignore[method-assign]

    results: list[dict[str, Any]] = []
    try:
        for paper_id in inp.paper_ids:
            override = inp.source_url_overrides.get(paper_id)
            result = await client._archive_paper_async(paper_id, source_url=override)
            results.append(result)
            ctx.log(
                "archive_papers_item",
                paper_id=paper_id,
                status=result.get("status"),
                parser_used=result.get("parser_used"),
                reason=result.get("reason"),
            )
    finally:
        client.close()

    archived = sum(1 for r in results if r.get("status") == "completed")
    noop = sum(1 for r in results if r.get("status") == "noop")
    skipped = sum(1 for r in results if r.get("status") == "skipped")
    failed = sum(1 for r in results if r.get("status") == "error")

    payload: dict[str, Any] = {
        "status": "completed",
        "papers_archived": archived,
        "papers_noop": noop,
        "papers_skipped": skipped,
        "papers_failed": failed,
        "results": results,
    }
    ctx.log(
        "archive_papers_completed",
        papers_archived=archived,
        papers_noop=noop,
        papers_skipped=skipped,
        papers_failed=failed,
    )
    return payload
