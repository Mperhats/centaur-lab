"""Shared helpers for projecting Semantic Scholar papers into company_context_documents.

Lives under ``overlay/centaur_lab/`` (named to avoid colliding with
upstream's reserved ``shared.tools_runtime`` namespace) so both
``overlay/workflows/`` and ``overlay/tools/`` can import these helpers
without sys.path gymnastics or cross-package back-references.

``build_paper_document`` projects a Semantic Scholar paper dict into the
column shape expected by ``company_context_documents``; ``upsert_document``
applies that row idempotently via ``content_hash`` so reruns over
unchanged input no-op, while re-parenting an otherwise-unchanged paper
(e.g. one previously saved by ``save_papers`` and later surfaced by
``research_brief``) still updates the row.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any, Literal


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


def build_paper_document(paper: dict, *, query: str | None = None) -> dict:
    """Project a Semantic Scholar paper dict into a company_context_documents row.

    Args:
        paper: A paper dict as returned by the Semantic Scholar Graph API
            (`paperId`, `title`, `authors`, `year`, `abstract`, `citationCount`,
            `url`, `openAccessPdf`, `venue`, `externalIds`, ...).
        query: Optional free-text query that produced this paper; persisted
            in `metadata.query` for downstream attribution.

    Returns:
        A dict shaped for `upsert_document` / the canonical `_upsert_document`
        SQL in `company_context_documents.py`.

    Raises:
        ValueError: If `paper` has no `paperId` — we cannot synthesize a stable
            primary key without it.
    """
    paper_id = paper.get("paperId")
    if not paper_id:
        raise ValueError("paper.paperId is required to build a paper document.")
    paper_id_str = str(paper_id)

    title = paper.get("title") or "Untitled"

    authors_raw = paper.get("authors") or []
    author_dicts = [a for a in authors_raw if isinstance(a, dict)]
    display_names = [str(a.get("name")) for a in author_dicts if a.get("name")]

    first_author = author_dicts[0] if author_dicts else None
    author_id = ""
    author_name = ""
    if first_author is not None:
        raw_author_id = first_author.get("authorId")
        author_id = str(raw_author_id) if raw_author_id else ""
        author_name = str(first_author.get("name") or "")

    year = paper.get("year")
    year_int = year if isinstance(year, int) else None
    venue = paper.get("venue")
    citation_count = int(paper.get("citationCount") or 0)

    external_ids_raw = paper.get("externalIds")
    external_ids = external_ids_raw if isinstance(external_ids_raw, dict) else {}
    doi = external_ids.get("DOI")
    arxiv_id = external_ids.get("ArXiv")

    open_access_pdf_raw = paper.get("openAccessPdf")
    open_access_pdf_url: str | None = None
    if isinstance(open_access_pdf_raw, dict):
        pdf_url = open_access_pdf_raw.get("url")
        open_access_pdf_url = str(pdf_url) if pdf_url else None

    canonical_s2_url = f"https://www.semanticscholar.org/paper/{paper_id_str}"
    url = paper.get("url") or canonical_s2_url

    abstract = paper.get("abstract") or "No abstract available."
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
            "authorId": (str(a["authorId"]) if a.get("authorId") else None),
            "name": str(a.get("name") or ""),
        }
        for a in author_dicts
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
        "parent_document_id": None,
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
    *,
    parent_document_id: str | None = None,
) -> Literal["inserted", "updated", "noop"]:
    """Upsert a projected paper document and return inserted/updated/noop.

    Mirrors `_upsert_document` from the upstream `company_context_documents`
    workflow. The `parent_document_id` kwarg overrides whatever is in
    `document` so callers can link papers to a brief after the fact (e.g. the
    research-brief workflow stamps each paper row with the brief's id).

    The persisted `content_hash` combines the document's intrinsic hash with
    the effective parent. Without this, re-parenting an otherwise-unchanged
    paper (e.g. previously saved by `save_papers`, then surfaced by
    `research_brief`) would silently no-op because the intrinsic hash hadn't
    changed — leaving the paper's `parent_document_id` stale.
    """
    effective_parent = (
        parent_document_id if parent_document_id is not None else document.get("parent_document_id")
    )
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
