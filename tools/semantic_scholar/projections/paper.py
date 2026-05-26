"""Project a Semantic Scholar paper into a ``company_context_documents`` row.

``build_paper_document`` is pure — it takes a paper dict (as returned
by the Semantic Scholar Graph API or proxied via this tool's
``SemanticScholarClient``) and produces a row dict whose shape matches
the ``company_context_documents`` table.

Persistence lives in the call site (``tools/semantic_scholar/client.py``
for the ``research_brief`` tool method, ``workflows/save_papers.py``
for the durable workflow handler); both inline the ``_upsert_document``
SQL and the ``vm_metrics`` shim that mirrors upstream's
``company_context_documents`` convention.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from tools.semantic_scholar.utils import content_hash


def build_paper_document(paper: dict, *, query: str | None = None) -> dict:
    """Project a Semantic Scholar paper dict into a company_context_documents row.

    Args:
        paper: A paper dict as returned by the Semantic Scholar Graph API
            (`paperId`, `title`, `authors`, `year`, `abstract`, `citationCount`,
            `url`, `openAccessPdf`, `venue`, `externalIds`, ...).
        query: Optional free-text query that produced this paper; persisted
            in `metadata.query` for downstream attribution.

    Returns:
        A dict shaped for the ``_upsert_document`` SQL in
        ``tools/semantic_scholar/client.py`` /
        ``workflows/save_papers.py`` (mirrors the canonical
        ``_upsert_document`` from upstream's
        ``company_context_documents`` workflow).

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
        "content_hash": content_hash(title, body, url, metadata),
        "metadata": metadata,
    }
