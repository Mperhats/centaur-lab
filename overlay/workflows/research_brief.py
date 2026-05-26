"""Workflow: build and persist a research brief from Semantic Scholar.

Calls ``SemanticScholarClient.research_brief``, which searches the
Graph API, renders a Markdown lit review, and returns a projection
bundle (``brief_doc`` plus ``paper_docs`` each already parented under
the brief's ``document_id``). This handler owns every DB write: it
upserts the brief row, then upserts each paper row.

When ``Input.archive`` is true, the handler also chains the existing
``archive_papers`` workflow as a **child** via ``ctx.run_workflow``
after the brief is persisted, so deep-research turns can get the full
text of every matched paper indexed under one parent ``run_id``. The
two are intentionally kept as separate workflows — both remain
independently invocable, and the parent-child lineage is observable
via ``GET /workflows/runs/<brief>/children``.

The upsert SQL — ``_upsert_document`` with the overlay's compound-hash
idempotency contract — plus the ``vm_metrics`` shim are inlined as
private helpers below. The same helpers exist verbatim in
``archive_papers.py`` and ``save_papers.py``; that duplication is the
upstream pattern (see
``.centaur/workflows/company_context_documents.py``).

Idempotent on ``(query, year_from, archive)``: re-running with the
same inputs produces the same ``brief_doc["document_id"]`` and
matching content hashes, so every brief/paper upsert noops, and the
child ``archive_papers`` run is itself idempotent on
``(paper_id, pdf_sha256)``.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from api.workflow_engine import WorkflowContext

from tools.semantic_scholar.client import SemanticScholarClient

try:
    from api.vm_metrics import (
        observe_company_context_document_size as _observe_document_size,
    )
    from api.vm_metrics import (
        record_company_context_documents_changed as _record_document_change,
    )
except ImportError:

    def _observe_document_size(source: str, source_type: str, chars: int) -> None: ...

    def _record_document_change(
        source: str, source_type: str, action: str, count: int = 1
    ) -> None: ...


WORKFLOW_NAME = "research_brief"


@dataclass
class Input:
    """Runtime options for the ``research_brief`` workflow."""

    query: str
    limit: int = 5
    year_from: int | None = None
    archive: bool = False
    """When true, run ``archive_papers`` as a child workflow after the
    brief is persisted, indexing the full text of every matched paper
    into ``paper_archives`` and ``company_context_documents``. Off by
    default because PDF fetch+parse is bandwidth- and CPU-heavy — opt
    in only when the user has asked for substantive coverage."""


def _observe_doc_size(document: dict[str, Any]) -> None:
    _observe_document_size(
        str(document.get("source", "")),
        str(document.get("source_type", "")),
        len(str(document.get("body") or "")),
    )


def _record_doc_change(document: dict[str, Any], action: str) -> None:
    _record_document_change(
        str(document.get("source", "")),
        str(document.get("source_type", "")),
        action,
    )


def _canonical_json(value: Any) -> str:
    """Stable JSON form used for hashing and JSONB metadata serialization."""
    return json.dumps(value, separators=(",", ":"), sort_keys=True, ensure_ascii=False)


def _content_hash(*parts: Any) -> str:
    """Hash projected document content so future syncs can detect changes cheaply."""
    return hashlib.sha256(_canonical_json(parts).encode("utf-8")).hexdigest()


async def _upsert_document(
    pool: Any,
    document: dict[str, Any],
) -> Literal["inserted", "updated", "noop"]:
    """Upsert a projected document; return inserted/updated/noop.

    Mirrors ``_upsert_document`` from the upstream
    ``company_context_documents`` workflow, with one overlay-specific
    divergence: the persisted ``content_hash`` is a COMPOUND hash of
    ``(intrinsic_hash, effective_parent)``. Without the compound, a
    re-parenting upsert (e.g. a paper first saved without a parent, then
    surfaced via a research brief) would silently noop because the
    intrinsic content didn't change — leaving the row's
    ``parent_document_id`` stale.
    """
    effective_parent = document["parent_document_id"]
    effective_hash = _content_hash(document["content_hash"], effective_parent)

    existing_hash = await pool.fetchval(
        "SELECT content_hash FROM company_context_documents WHERE document_id = $1",
        document["document_id"],
    )
    if existing_hash == effective_hash:
        return "noop"

    status = await pool.execute(
        "INSERT INTO company_context_documents ("
        "document_id, source, source_type, source_document_id, source_chunk_id, "
        "parent_document_id, title, body, url, author_id, author_name, access_scope, "
        "occurred_at, source_updated_at, content_hash, metadata, updated_at"
        ") VALUES ("
        "$1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, "
        "$15, $16::jsonb, NOW()"
        ") ON CONFLICT (document_id) DO UPDATE SET "
        "source = EXCLUDED.source, "
        "source_type = EXCLUDED.source_type, "
        "source_document_id = EXCLUDED.source_document_id, "
        "source_chunk_id = EXCLUDED.source_chunk_id, "
        "parent_document_id = EXCLUDED.parent_document_id, "
        "title = EXCLUDED.title, "
        "body = EXCLUDED.body, "
        "url = EXCLUDED.url, "
        "author_id = EXCLUDED.author_id, "
        "author_name = EXCLUDED.author_name, "
        "access_scope = EXCLUDED.access_scope, "
        "occurred_at = EXCLUDED.occurred_at, "
        "source_updated_at = EXCLUDED.source_updated_at, "
        "content_hash = EXCLUDED.content_hash, "
        "metadata = EXCLUDED.metadata, "
        "updated_at = NOW() "
        "WHERE company_context_documents.content_hash IS DISTINCT FROM EXCLUDED.content_hash",
        document["document_id"],
        document["source"],
        document["source_type"],
        document["source_document_id"],
        document["source_chunk_id"],
        effective_parent,
        document["title"],
        document["body"],
        document["url"],
        document["author_id"],
        document["author_name"],
        document["access_scope"],
        document["occurred_at"],
        document["source_updated_at"],
        effective_hash,
        _canonical_json(document["metadata"]),
    )
    if not status.endswith(" 1"):
        return "noop"
    return "updated" if existing_hash else "inserted"


async def handler(inp: Input, ctx: WorkflowContext) -> dict[str, Any]:
    """Search → render → persist. Returns the brief envelope minus markdown.

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

    client = SemanticScholarClient()
    bundle = await client.research_brief(
        query=normalized_query,
        limit=inp.limit,
        year_from=inp.year_from,
    )
    if bundle["status"] != "ok":
        ctx.log("research_brief_failed", error=bundle.get("error"))
        return bundle

    ctx.log(
        "research_brief_starting",
        query=normalized_query,
        limit=bundle["limit"],
        year_from=inp.year_from,
        results_count=bundle["results_count"],
    )

    brief_doc = bundle["brief_doc"]
    _observe_doc_size(brief_doc)
    brief_action = await _upsert_document(ctx._pool, brief_doc)
    _record_doc_change(brief_doc, brief_action)

    papers_inserted = 0
    papers_updated = 0
    papers_noop = 0
    for paper_doc in bundle["paper_docs"]:
        _observe_doc_size(paper_doc)
        action = await _upsert_document(ctx._pool, paper_doc)
        _record_doc_change(paper_doc, action)
        if action == "inserted":
            papers_inserted += 1
        elif action == "updated":
            papers_updated += 1
        else:
            papers_noop += 1

    ctx.log(
        "research_brief_completed",
        brief_document_id=brief_doc["document_id"],
        brief_action=brief_action,
        results_count=bundle["results_count"],
        papers_inserted=papers_inserted,
        papers_updated=papers_updated,
        papers_noop=papers_noop,
    )

    result: dict[str, Any] = {
        "status": "completed",
        "brief_document_id": brief_doc["document_id"],
        "brief_action": brief_action,
        "results_count": bundle["results_count"],
        "papers_inserted": papers_inserted,
        "papers_updated": papers_updated,
        "papers_noop": papers_noop,
    }

    if inp.archive:
        # Skip the child dispatch when the brief surfaced no papers —
        # ``archive_papers`` would short-circuit on the empty list
        # anyway, but avoiding the spawn keeps the parent-child view
        # uncluttered (no zero-work archive run per empty query).
        paper_ids = [
            doc["source_document_id"]
            for doc in bundle["paper_docs"]
            if doc.get("source_document_id")
        ]
        if paper_ids:
            ctx.log("research_brief_archive_starting", paper_count=len(paper_ids))
            archive_run = await ctx.run_workflow(
                "archive",
                workflow_name="archive_papers",
                run_input={"paper_ids": paper_ids},
            )
            archive_output = archive_run.get("output_json") if isinstance(archive_run, dict) else None
            result["archive_run_id"] = (
                archive_run.get("run_id") if isinstance(archive_run, dict) else None
            )
            result["archive"] = archive_output
            ctx.log(
                "research_brief_archive_completed",
                archive_run_id=result["archive_run_id"],
                archive_status=(archive_output or {}).get("status"),
            )
        else:
            ctx.log("research_brief_archive_skipped", reason="no_paper_ids")
            result["archive_run_id"] = None
            result["archive"] = {"status": "skipped", "reason": "no_paper_ids"}

    return result
