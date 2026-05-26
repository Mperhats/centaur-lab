"""Shared helpers for projecting Semantic Scholar papers into company_context_documents.

Lives under ``overlay/centaur_lab/`` (named to avoid colliding with
upstream's reserved ``shared.tools_runtime`` namespace) so both
``overlay/workflows/`` and ``overlay/tools/`` can import these helpers
without sys.path gymnastics or cross-package back-references.

``build_paper_document`` projects an upstream
:class:`semanticscholar.Paper.Paper` into the column shape expected by
``company_context_documents``; ``upsert_document`` applies that row
idempotently via ``content_hash`` so reruns over unchanged input no-op,
while re-parenting an otherwise-unchanged paper (e.g. one previously
saved by ``save_papers`` and later surfaced by ``research_brief``)
still updates the row.

The upstream ``Paper`` class returns ``None`` (not ``[]``/``{}``) for
missing fields and exposes ``openAccessPdf`` as a plain dict; every
attribute read below normalises those to the empty/default values the
projection logic was written against.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any, Literal

from semanticscholar.Paper import Paper


# We intentionally do not import api.runtime_control.canonical_json here so
# this module stays unit-testable outside the API pod. The argument list
# below is kept byte-identical to upstream's ``canonical_json``
# (``api.runtime_control.canonical_json``): same separators, ``sort_keys``,
# ``ensure_ascii=False`` so non-ASCII titles/authors hash to literal Unicode
# bytes rather than ``\\uXXXX`` escapes, and no ``default=`` so non-
# serializable values raise ``TypeError`` instead of being silently coerced.
# Cross-system content_hash identity depends on this byte equivalence.
def _canonical_json(value: Any) -> str:
    """Stable JSON form used for hashing and JSONB metadata serialization."""
    return json.dumps(value, separators=(",", ":"), sort_keys=True, ensure_ascii=False)


def _content_hash(*parts: Any) -> str:
    """Hash projected document content so future syncs can detect changes cheaply."""
    return hashlib.sha256(_canonical_json(parts).encode("utf-8")).hexdigest()


def build_paper_document(
    paper: Paper,
    *,
    query: str | None = None,
    parent_document_id: str | None = None,
) -> dict:
    """Project a Semantic Scholar :class:`Paper` into a company_context_documents row.

    Args:
        paper: A typed :class:`Paper` parsed from the Graph API response.
        query: Optional free-text query that produced this paper; persisted
            in `metadata.query` for downstream attribution.
        parent_document_id: Optional parent row id (e.g. the research-brief
            ``document_id`` for papers surfaced by ``research_brief``).
            Persisted directly into the returned dict's
            ``parent_document_id`` field; ``upsert_document`` reads it from
            there.

    Returns:
        A dict shaped for `upsert_document` / the canonical `_upsert_document`
        SQL in `company_context_documents.py`.

    Raises:
        ValueError: If `paper.paperId` is missing — we cannot synthesize a
            stable primary key without it.
    """
    if not paper.paperId:
        raise ValueError("paper.paperId is required to build a paper document.")
    paper_id_str = str(paper.paperId)

    title = paper.title or "Untitled"

    # ``paper.authors`` is ``None`` when the S2 response omitted the key.
    # Normalise to an empty list once so every downstream comprehension
    # can stay free of the None-guard.
    authors = paper.authors or []
    display_names = [a.name for a in authors if a.name]
    first_author = authors[0] if authors else None
    author_id = ""
    author_name = ""
    if first_author is not None:
        author_id = str(first_author.authorId) if first_author.authorId else ""
        author_name = str(first_author.name or "")

    year_int = paper.year
    venue = paper.venue
    citation_count = int(paper.citationCount or 0)

    external_ids = paper.externalIds or {}
    doi = external_ids.get("DOI")
    arxiv_id = external_ids.get("ArXiv")

    # ``paper.openAccessPdf`` is a plain dict from the upstream library
    # (not a typed object); ``None`` when the field is absent or null.
    open_access_pdf_url: str | None = None
    open_access_pdf = paper.openAccessPdf
    if open_access_pdf and open_access_pdf.get("url"):
        open_access_pdf_url = str(open_access_pdf["url"])

    canonical_s2_url = f"https://www.semanticscholar.org/paper/{paper_id_str}"
    url = paper.url or canonical_s2_url

    abstract = paper.abstract or "No abstract available."
    body = "\n".join(
        [
            f"# {title}",
            "",
            f"- Authors: {', '.join(display_names) if display_names else 'Unknown'}",
            f"- Year: {year_int if year_int is not None else 'Unknown'}",
            f"- Venue: {venue if venue else 'Unknown'}",
            f"- Citations: {citation_count}",
            f"- DOI: {doi if doi else 'n/a'}",
            f"- URL: {url}",
            "",
            "## Abstract",
            "",
            abstract,
        ]
    )

    occurred_at: datetime | None = (
        datetime(year_int, 1, 1, tzinfo=UTC) if year_int is not None else None
    )

    metadata_authors = [
        {
            "authorId": str(a.authorId) if a.authorId else None,
            "name": str(a.name or ""),
        }
        for a in authors
    ]

    # OVERLAY: include all metadata keys with explicit nulls (instead of
    # filtering ``None`` out) so JSONB key-presence checks behave the
    # same way for ``semantic_scholar`` rows as for Slack rows upstream.
    # Upstream's channel-day / thread projections list every key
    # unconditionally; dropping ``None`` keys here meant downstream
    # ``metadata ? 'doi'`` checks reported ``false`` rather than
    # ``true`` for papers without a DOI.
    metadata: dict[str, Any] = {
        "paperId": paper_id_str,
        "year": year_int,
        "venue": venue if venue else None,
        "citationCount": citation_count,
        "authors": metadata_authors,
        "doi": doi if doi else None,
        "arxivId": arxiv_id if arxiv_id else None,
        "openAccessPdf": open_access_pdf_url,
        "query": query,
    }

    # OVERLAY: ``source_updated_at`` is *sync time* (when we last
    # observed the row), not publication time. The Semantic Scholar
    # Graph API does not expose a per-paper update timestamp, so the
    # nearest analog is ``datetime.now(UTC)`` — taken at projection
    # time. Setting this to ``occurred_at`` (paper-publication year)
    # would report multi-year ETL lag to downstream freshness
    # dashboards. ``occurred_at`` itself stays anchored to the
    # publication year for chronological surfacing.
    return {
        "document_id": f"semantic_scholar:paper:{paper_id_str}",
        "source": "semantic_scholar",
        "source_type": "paper",
        "source_document_id": paper_id_str,
        "source_chunk_id": "",
        "parent_document_id": parent_document_id,
        "title": title,
        "body": body,
        "url": url,
        "author_id": author_id,
        "author_name": author_name,
        "access_scope": "company",
        "occurred_at": occurred_at,
        "source_updated_at": datetime.now(UTC),
        "content_hash": _content_hash(title, body, url, metadata),
        "metadata": metadata,
    }


async def upsert_document(
    pool: Any,
    document: dict,
) -> Literal["inserted", "updated", "noop"]:
    """Upsert a projected paper document and return inserted/updated/noop.

    Mirrors `_upsert_document` from the upstream `company_context_documents`
    workflow. Parent linkage is read from ``document["parent_document_id"]``;
    callers that want to link a child to a parent set that field at build
    time (e.g. ``build_paper_document(paper, parent_document_id=...)``).

    The persisted `content_hash` combines the document's intrinsic hash with
    its parent. Without this, re-parenting an otherwise-unchanged paper
    (e.g. previously saved by `save_papers`, then surfaced by
    `research_brief`) would silently no-op because the intrinsic hash hadn't
    changed — leaving the paper's `parent_document_id` stale.
    """
    effective_parent = document["parent_document_id"]
    # OVERLAY: compound hash (intrinsic + effective_parent) — diverges from
    # upstream's raw intrinsic-hash convention to make re-parenting trigger
    # UPDATE even when content is unchanged. See function docstring for why.
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
