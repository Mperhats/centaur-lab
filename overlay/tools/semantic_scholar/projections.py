"""Pure projections: typed Semantic Scholar shapes → DB-row dicts.

Every function in this module is a pure function: typed S2 inputs in,
plain ``dict`` (or ``str``) out. No DB, no asyncpg, no pool, no
``await`` — the actual SQL writes live inlined in each workflow
handler as ``_upsert_*`` private functions (matching the upstream
``company_context_documents.py`` pattern).

Three column shapes are produced here:

* ``company_context_documents`` rows — ``source_type``-specific
  projections via :func:`build_paper_document`,
  :func:`build_fulltext_document`, :func:`build_brief_document`.
* ``paper_archives`` rows — projected by
  :func:`build_paper_archive_row` from a fetched+parsed PDF plus its
  source ``Paper``.
* Rendered Markdown body for a research brief — via
  :func:`render_brief`; the persistence side is built around the
  rendered body in :func:`build_brief_document`.

These projections used to live next to their async-DB counterparts
under ``centaur_lab/`` (now removed). Splitting the pure halves into
this module lets the tool ship its agent-facing reads without forcing
the workflow layer to depend on a persistence library, and lets each
workflow own its inlined writes — matching upstream conventions.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any, Final

from pdf.utils import truncate_utf8
from semanticscholar.Author import Author
from semanticscholar.Paper import Paper

from semantic_scholar.utils import canonical_json, content_hash

FULLTEXT_BODY_MAX_BYTES: Final[int] = 1 * 1024 * 1024  # 1 MiB cap on indexed body bytes.

_BRIEF_ABSTRACT_TRUNCATE: Final[int] = 500
_BRIEF_TITLE_QUERY_TRUNCATE: Final[int] = 80
_BRIEF_ID_HEX_LEN: Final[int] = 16
_BRIEF_MAX_AUTHORS_INLINE: Final[int] = 3


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


def build_fulltext_document(
    paper: Paper,
    *,
    parsed_text: str,
    parser_used: str,
    truncated: bool,
    pdf_sha256: str,
    source_url: str | None = None,
) -> dict[str, Any]:
    """Project a parsed PDF into a ``source_type="paper_fulltext"`` document row.

    Mirrors :func:`build_paper_document` but emits the full-text
    companion row keyed off the same Semantic Scholar paper. The body
    is the parsed Markdown, capped at :data:`FULLTEXT_BODY_MAX_BYTES`
    UTF-8 bytes for BM25 index efficiency. The parent metadata row's
    ``document_id`` is derived from ``paper.paperId`` (the two share
    the same paper key by construction).

    Args:
        paper: A typed :class:`Paper` parsed from the Graph API response;
            only ``paperId``, ``title``, ``authors``, ``year``, and ``url``
            are read here.
        parsed_text: The Markdown produced by the PDF parser. Will be
            truncated in-place if it exceeds the byte cap.
        parser_used: Identifier for the parser that produced ``parsed_text``
            (e.g. ``"pymupdf4llm"``); persisted in metadata for debugging
            and for re-parse decisions.
        truncated: Caller's hint that ``parsed_text`` was already truncated
            upstream (e.g. by the parser itself). The metadata flag
            ORs this with whether *we* further truncated for the byte cap.
        pdf_sha256: Hex SHA-256 of the source PDF bytes; persisted in
            metadata so the document row can be matched back to its
            ``paper_archives`` entry.
        source_url: Optional URL the PDF was fetched from; persisted in
            metadata as ``sourceUrl`` (may be ``None``).

    Returns:
        A dict shaped for the canonical ``_upsert_document`` SQL.

    Raises:
        ValueError: If ``paper.paperId`` is missing or empty.
    """
    if not paper.paperId:
        raise ValueError("paper.paperId is required to build fulltext document")
    paper_id_str = str(paper.paperId)
    parent_document_id = f"semantic_scholar:paper:{paper_id_str}"

    title = paper.title or "Untitled"

    authors = paper.authors or []
    first_author = authors[0] if authors else None
    author_id = ""
    author_name = ""
    if first_author is not None:
        author_id = str(first_author.authorId) if first_author.authorId else ""
        author_name = str(first_author.name or "")

    year_int = paper.year

    url = paper.url or f"https://www.semanticscholar.org/paper/{paper_id_str}"

    body, body_was_truncated = truncate_utf8(parsed_text, FULLTEXT_BODY_MAX_BYTES)

    occurred_at: datetime | None = (
        datetime(year_int, 1, 1, tzinfo=UTC) if year_int is not None else None
    )

    metadata: dict[str, Any] = {
        "paperId": paper_id_str,
        "parserUsed": parser_used,
        "truncated": bool(truncated or body_was_truncated),
        "charCount": len(body),
        "pdfSha256": pdf_sha256,
        "sourceUrl": source_url,
    }

    return {
        "document_id": f"semantic_scholar:paper_fulltext:{paper_id_str}",
        "source": "semantic_scholar",
        "source_type": "paper_fulltext",
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


def build_paper_archive_row(
    paper: Paper,
    *,
    data: bytes,
    mime: str,
    pdf_sha256: str,
    parsed_text: str,
    parser_used: str,
    source_url: str,
    truncated: bool = False,
) -> dict[str, Any]:
    """Project a fetched+parsed PDF into a ``paper_archives`` row dict (no DB).

    The dict has the column names ``paper_archives`` expects; the
    workflow's inlined ``_upsert_paper_archive`` consumes it without
    any reshaping. ``paperId`` and the canonical paper URL go into the
    JSONB ``metadata`` column so the row remains self-describing even
    if the parent ``Paper`` is later evicted from
    ``company_context_documents``.

    Raises:
        ValueError: If ``paper.paperId`` is missing — no stable
            primary key would be available for the upsert.
    """
    if not paper.paperId:
        raise ValueError("paper.paperId is required to build a paper_archives row")
    paper_id_str = str(paper.paperId)
    canonical_url = paper.url or f"https://www.semanticscholar.org/paper/{paper_id_str}"

    return {
        "paper_id": paper_id_str,
        "source_url": source_url,
        "mime_type": mime,
        "size_bytes": len(data),
        "pdf_sha256": pdf_sha256,
        "pdf_bytes": data,
        "parsed_text": parsed_text,
        "parser_used": parser_used,
        "truncated": truncated,
        "metadata": {
            "paperId": paper_id_str,
            "url": canonical_url,
        },
    }


def brief_id_for(query: str, year_from: int | None) -> str:
    """Stable, case-insensitive id suffix for the brief document."""
    canonical = canonical_json([query.strip().lower(), year_from])
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:_BRIEF_ID_HEX_LEN]


def _normalize_oneline(text: str) -> str:
    return " ".join(text.split())


def _format_authors(authors: list[Author]) -> str:
    names = [a.name for a in authors if a.name]
    if not names:
        return "Unknown"
    if len(names) <= _BRIEF_MAX_AUTHORS_INLINE:
        return ", ".join(names)
    head = ", ".join(names[:_BRIEF_MAX_AUTHORS_INLINE])
    return f"{head} +{len(names) - _BRIEF_MAX_AUTHORS_INLINE} more"


def _paper_url(paper: Paper) -> str:
    if paper.url:
        return str(paper.url)
    if paper.paperId:
        return f"https://www.semanticscholar.org/paper/{paper.paperId}"
    return ""


def _format_abstract(paper: Paper) -> str:
    if not paper.abstract:
        return "No abstract available."
    if len(paper.abstract) > _BRIEF_ABSTRACT_TRUNCATE:
        return paper.abstract[:_BRIEF_ABSTRACT_TRUNCATE] + "..."
    return paper.abstract


def render_brief(query: str, year_from: int | None, papers: list[Paper]) -> str:
    """Render the brief Markdown. Pure; no I/O."""
    display_query = _normalize_oneline(query)
    year_label = str(year_from) if year_from is not None else "any"
    header = [
        f"# Research Brief: {display_query}",
        "",
        f"- Query: {display_query}",
        f"- Year filter: {year_label}",
        f"- Results: {len(papers)} papers",
        "",
        "---",
        "",
    ]

    if not papers:
        return "\n".join([*header, "No papers found for this query.", ""])

    lines: list[str] = [*header, "## Papers", ""]
    for index, paper in enumerate(papers, start=1):
        display_title = _normalize_oneline(paper.title or "Untitled")
        year_text = str(paper.year) if paper.year is not None else "Unknown"
        citations = int(paper.citationCount or 0)
        lines.extend(
            [
                f"### {index}. {display_title}",
                "",
                f"- Authors: {_format_authors(paper.authors)}",
                f"- Year: {year_text}",
                f"- Citations: {citations}",
                f"- URL: {_paper_url(paper)}",
                "",
                _format_abstract(paper),
                "",
            ]
        )
    return "\n".join(lines)


def build_brief_document(
    query: str,
    year_from: int | None,
    limit: int,
    papers: list[Paper],
    markdown: str,
) -> dict[str, Any]:
    """Project the rendered brief into a ``company_context_documents`` row."""
    suffix = brief_id_for(query, year_from)
    truncated_query = query[:_BRIEF_TITLE_QUERY_TRUNCATE]
    title = f"Research Brief: {truncated_query}"
    paper_ids = [str(p.paperId) for p in papers if p.paperId]
    metadata: dict[str, Any] = {
        "query": query,
        "year_from": year_from,
        "limit": limit,
        "results_count": len(papers),
        "paper_ids": paper_ids,
    }
    return {
        "document_id": f"semantic_scholar:research_brief:{suffix}",
        "source": "semantic_scholar",
        "source_type": "research_brief",
        "source_document_id": suffix,
        "source_chunk_id": "",
        "parent_document_id": None,
        "title": title,
        "body": markdown,
        "url": "",
        "author_id": "",
        "author_name": "",
        "access_scope": "company",
        "occurred_at": None,
        "source_updated_at": datetime.now(UTC),
        "content_hash": content_hash(title, markdown, "", metadata),
        "metadata": metadata,
    }
