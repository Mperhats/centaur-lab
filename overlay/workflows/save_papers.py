"""Workflow: persist Semantic Scholar papers as company context documents.

Given a list of Semantic Scholar paper IDs, fetch each paper's metadata
via the ``semantic_scholar`` tool client and project it into a
``source_type="paper"`` row in ``company_context_documents``. Always
follows up with a ``research_brief`` row linking the saved papers as
children: the per-paper rows are written twice (first without a parent,
then with the brief as the parent) and the compound-hash logic in
``_upsert_document`` makes the re-parenting an UPDATE rather than a
noop.

The PDF archival workflow lives separately in ``archive_papers.py``;
this handler never touches ``paper_archives`` because metadata-only
``save_papers`` runs are common (the agent batches off arXiv IDs
without downloading bytes).

Per-paper failures from the upstream API are logged and recorded in the
result payload, but do not abort the run; unexpected exceptions
propagate so the run is marked failed.

The upsert SQL — ``_upsert_document`` and its overlay-specific
compound-hash idempotency contract — plus the ``vm_metrics`` shim are
inlined as private helpers below. The same helpers exist verbatim in
``archive_papers.py`` and ``research_brief.py``; that duplication is
the upstream pattern (see
``.centaur/workflows/company_context_documents.py``).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from api.workflow_engine import WorkflowContext

from semanticscholar.Paper import Paper

from tools.semantic_scholar.client import SemanticScholarClient
from tools.semantic_scholar.projections.brief import build_brief_document, render_brief
from tools.semantic_scholar.projections.paper import build_paper_document

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


WORKFLOW_NAME = "save_papers"


@dataclass
class Input:
    """Runtime options for the ``save_papers`` workflow."""

    paper_ids: list[str]
    query: str | None = None


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


def _brief_query_for_save(paper_ids: list[str], explicit: str | None) -> str:
    """Build a stable fallback query for save_papers' implicit brief.

    Sorting the ids keeps the digest invariant to input order — re-running
    with the same set of papers (regardless of position) hits the same
    brief row, which is the whole point of the implicit brief.
    """
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
        _observe_doc_size(document)
        action = await _upsert_document(ctx._pool, document)
        _record_doc_change(document, action)
        results.append(
            {
                "paperId": paper_id,
                "document_id": document["document_id"],
                "status": action,
            }
        )

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
        markdown = render_brief(brief_query, None, saved_papers)
        brief_doc = build_brief_document(
            brief_query, None, len(saved_papers), saved_papers, markdown
        )
        _observe_doc_size(brief_doc)
        brief_action = await _upsert_document(ctx._pool, brief_doc)
        _record_doc_change(brief_doc, brief_action)

        # Re-parent each saved paper under the brief. The compound-hash
        # logic in ``_upsert_document`` turns this into an UPDATE for
        # papers whose intrinsic content is unchanged but whose parent
        # link changed (None → brief_doc["document_id"]).
        for paper in saved_papers:
            try:
                paper_doc = build_paper_document(
                    paper,
                    query=brief_query,
                    parent_document_id=brief_doc["document_id"],
                )
            except ValueError:
                continue
            _observe_doc_size(paper_doc)
            action = await _upsert_document(ctx._pool, paper_doc)
            _record_doc_change(paper_doc, action)

        payload.update(
            {
                "brief_document_id": brief_doc["document_id"],
                "brief_action": brief_action,
                "brief_query": brief_query,
            }
        )
        ctx.log(
            "save_papers_brief_persisted",
            brief_document_id=brief_doc["document_id"],
            brief_action=brief_action,
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
