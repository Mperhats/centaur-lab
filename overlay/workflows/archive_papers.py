"""Workflow: batch-archive Semantic Scholar PDFs.

For each input paper id the workflow calls
``SemanticScholarClient.archive_paper`` (which returns a pure projection
bundle: a fetched + parsed PDF projected to ``paper_doc`` /
``fulltext_doc`` / ``archive_row`` dicts) and persists the three rows
under the workflow's own pool. The tool never touches Postgres; this
handler owns every DB write.

The upsert SQL — ``_upsert_document`` and ``_upsert_paper_archive`` —
plus its overlay-specific compound-hash idempotency contract and the
``vm_metrics`` shim are inlined as private helpers below. The same
helpers exist verbatim in ``save_papers.py`` and ``research_brief.py``;
the duplication is the upstream pattern (see
``.centaur/workflows/company_context_documents.py`` for the canonical
inline-helpers shape). When the duplication starts to cost more than
it saves, extract a sibling ``_shared`` module — not before.

Per-paper bundles with ``status != "ok"`` are appended to the result
payload but do not abort the batch. Programming errors (asyncpg pool
down, missing DATABASE_URL, migrations not applied) propagate so the
run is marked failed.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from api.workflow_engine import WorkflowContext

from semantic_scholar.client import SemanticScholarClient

# Inlined metrics shim — try the real ``api.vm_metrics`` import (works
# inside the API pod), fall back to no-op stubs (works in local pytest
# runs where ``api`` isn't on sys.path). The same two-line try/except
# block is duplicated verbatim in ``save_papers.py`` and
# ``research_brief.py`` so all three handlers report against the same
# Prometheus surface; that duplication is the upstream inline-helpers
# convention.
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


WORKFLOW_NAME = "archive_papers"


@dataclass
class Input:
    """Runtime options for the ``archive_papers`` workflow."""

    paper_ids: list[str]
    source_url_overrides: dict[str, str] = field(default_factory=dict)


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


# We intentionally do not import api.runtime_control.canonical_json here so
# this module stays unit-testable outside the API pod. The argument list
# below is kept byte-identical to upstream's ``canonical_json``: same
# separators, ``sort_keys``, ``ensure_ascii=False`` so non-ASCII
# titles/authors hash to literal Unicode bytes, and no ``default=`` so
# non-serializable values raise ``TypeError`` instead of being silently
# coerced. Cross-system content_hash identity depends on this byte
# equivalence.
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


async def _upsert_paper_archive(
    pool: Any,
    row: dict[str, Any],
) -> Literal["inserted", "updated", "noop"]:
    """Idempotently insert/update a ``paper_archives`` row.

    Idempotency contract: the ``(paper_id, pdf_sha256)`` pair is the
    no-change key. If a row already exists for ``paper_id`` and its
    ``pdf_sha256`` matches the new one, this returns ``"noop"``
    immediately without issuing an UPSERT.
    """
    existing_hash = await pool.fetchval(
        "SELECT pdf_sha256 FROM paper_archives WHERE paper_id = $1",
        row["paper_id"],
    )
    if existing_hash == row["pdf_sha256"]:
        return "noop"

    status = await pool.execute(
        "INSERT INTO paper_archives ("
        "paper_id, source_url, mime_type, size_bytes, pdf_sha256, pdf_bytes, "
        "parsed_text, parser_used, truncated, metadata, archived_at, updated_at"
        ") VALUES ("
        "$1, $2, $3, $4, $5, $6, $7, $8, $9, $10::jsonb, NOW(), NOW()"
        ") ON CONFLICT (paper_id) DO UPDATE SET "
        "source_url = EXCLUDED.source_url, "
        "mime_type = EXCLUDED.mime_type, "
        "size_bytes = EXCLUDED.size_bytes, "
        "pdf_sha256 = EXCLUDED.pdf_sha256, "
        "pdf_bytes = EXCLUDED.pdf_bytes, "
        "parsed_text = EXCLUDED.parsed_text, "
        "parser_used = EXCLUDED.parser_used, "
        "truncated = EXCLUDED.truncated, "
        "metadata = EXCLUDED.metadata, "
        "updated_at = NOW()",
        row["paper_id"],
        row["source_url"],
        row["mime_type"],
        row["size_bytes"],
        row["pdf_sha256"],
        row["pdf_bytes"],
        row["parsed_text"],
        row["parser_used"],
        row["truncated"],
        _canonical_json(row.get("metadata") or {}),
    )
    if not status.endswith(" 1"):
        return "noop"
    return "updated" if existing_hash else "inserted"


async def _archive_one(
    client: SemanticScholarClient,
    pool: Any,
    paper_id: str,
    *,
    source_url: str | None,
) -> dict[str, Any]:
    """Run the bundle → persist pipeline for a single paper.

    Trade-off documented inline: the legacy ``archive_paper_to_pool``
    short-circuited on a pre-parse ``(paper_id, pdf_sha256)`` lookup,
    so an already-archived paper could skip parsing entirely. The new
    bundle-shaped tool API hashes the PDF *inside* the tool, so the
    ``noop`` check moves here — we still avoid the three upserts on the
    idempotent path, but we pay one re-parse per noop. Preserving the
    pre-fetch-hash check would have leaked DB state into the tool
    boundary; explicit trade-off in favour of the cleaner boundary.
    """
    bundle = await client.archive_paper(paper_id, source_url=source_url)
    if bundle["status"] != "ok":
        return bundle

    existing = await pool.fetchval(
        "SELECT pdf_sha256 FROM paper_archives WHERE paper_id = $1",
        bundle["paper_id"],
    )
    if existing == bundle["pdf_sha256"]:
        return {
            "status": "noop",
            "paper_id": bundle["paper_id"],
            "source_url": bundle["source_url"],
            "pdf_sha256": bundle["pdf_sha256"],
            "archive_action": "noop",
        }

    _observe_doc_size(bundle["paper_doc"])
    paper_action = await _upsert_document(pool, bundle["paper_doc"])
    _record_doc_change(bundle["paper_doc"], paper_action)

    _observe_doc_size(bundle["fulltext_doc"])
    fulltext_action = await _upsert_document(pool, bundle["fulltext_doc"])
    _record_doc_change(bundle["fulltext_doc"], fulltext_action)

    archive_action = await _upsert_paper_archive(pool, bundle["archive_row"])

    return {
        "status": "completed",
        "paper_id": bundle["paper_id"],
        "source_url": bundle["source_url"],
        "pdf_sha256": bundle["pdf_sha256"],
        "parser_used": bundle["parser_used"],
        "size_bytes": bundle["size_bytes"],
        "paper_document_id": bundle["paper_doc"]["document_id"],
        "paper_action": paper_action,
        "fulltext_document_id": bundle["fulltext_doc"]["document_id"],
        "fulltext_action": fulltext_action,
        "archive_action": archive_action,
    }


async def handler(inp: Input, ctx: WorkflowContext) -> dict[str, Any]:
    """Batch-fetch + persist Semantic Scholar paper archives."""
    if not inp.paper_ids:
        ctx.log("archive_papers_skipped_empty")
        return {"status": "skipped", "reason": "no_paper_ids"}

    client = SemanticScholarClient()
    results: list[dict[str, Any]] = []
    for paper_id in inp.paper_ids:
        override = inp.source_url_overrides.get(paper_id)
        result = await _archive_one(client, ctx._pool, paper_id, source_url=override)
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
