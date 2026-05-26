"""Project a typed Semantic Scholar :class:`Paper` into a paper-metadata row.

Produces the ``source_type="paper"`` shape of ``company_context_documents``
— author/year/venue/DOI/etc. summarised into a Markdown body, with the
raw fields preserved in a JSONB ``metadata`` column for downstream
filtering. PDF bytes and parsed full-text are *not* read here; those
live in the sibling :mod:`semantic_scholar.projections.fulltext` and
:mod:`semantic_scholar.projections.archive` modules.

Pure function: typed S2 input in, plain ``dict`` out. No DB, no
``await``. The workflow's inlined ``_upsert_document`` consumes the
dict shape directly.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from semanticscholar.Paper import Paper

from tools.semantic_scholar.utils import content_hash


def build_paper_document(
    paper: Paper,
    *,
    query: str | None = None,
    parent_document_id: str | None = None,
) -> dict[str, Any]:
    """Project a Semantic Scholar :class:`Paper` into a ``company_context_documents`` row.

    Args:
        paper: A typed :class:`Paper` parsed from the Graph API response.
        query: Optional free-text query that produced this paper; persisted
            in ``metadata.query`` for downstream attribution.
        parent_document_id: Optional parent row id (e.g. the research-brief
            ``document_id`` for papers surfaced by ``research_brief``).
            Persisted directly into the returned dict's
            ``parent_document_id`` field; the workflow's inlined
            ``_upsert_document`` reads it from there.

    Returns:
        A dict shaped for the canonical ``_upsert_document`` SQL in
        ``company_context_documents.py``.

    Raises:
        ValueError: If ``paper.paperId`` is missing — we cannot synthesize a
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
    # time. ``occurred_at`` itself stays anchored to the publication
    # year for chronological surfacing.
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
        "content_hash": content_hash(title, body, url, metadata),
        "metadata": metadata,
    }
