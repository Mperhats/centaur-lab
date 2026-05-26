"""Render a research-brief Markdown body and project it into a document row.

Two public entry points work together:

* :func:`render_brief` produces the rendered Markdown body from a list
  of :class:`Paper` results. It does no DB work and emits no metadata.
* :func:`build_brief_document` wraps that body in a ``company_context_documents``
  row shaped for the canonical ``_upsert_document`` SQL, with a stable
  ``document_id`` derived from :func:`brief_id_for`.

The two are split because the workflow handler logs and persists each
brief independently of rendering — keeping the renderer pure makes it
trivially testable without standing up the document shape.

Pure functions: no DB, no ``await``.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any, Final

from semanticscholar.Author import Author
from semanticscholar.Paper import Paper

from tools.semantic_scholar.utils import canonical_json, content_hash

_BRIEF_ABSTRACT_TRUNCATE: Final[int] = 500
_BRIEF_TITLE_QUERY_TRUNCATE: Final[int] = 80
_BRIEF_ID_HEX_LEN: Final[int] = 16
_BRIEF_MAX_AUTHORS_INLINE: Final[int] = 3


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
