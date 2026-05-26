"""Research brief rendering and persistence for company_context_documents."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any

from semanticscholar.Author import Author
from semanticscholar.Paper import Paper

from centaur_lab.metrics import observe_document_size, record_document_change
from centaur_lab.paper_document import (
    _canonical_json,
    _content_hash,
    build_paper_document,
    upsert_document,
)

_BRIEF_ABSTRACT_TRUNCATE = 500
_BRIEF_TITLE_QUERY_TRUNCATE = 80
_BRIEF_ID_HEX_LEN = 16
_BRIEF_MAX_AUTHORS_INLINE = 3


def brief_id_for(query: str, year_from: int | None) -> str:
    """Stable, case-insensitive id suffix for the brief document."""
    canonical = _canonical_json([query.strip().lower(), year_from])
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
        "content_hash": _content_hash(title, markdown, "", metadata),
        "metadata": metadata,
    }


async def persist_research_brief_from_papers(
    pool: Any,
    *,
    query: str,
    papers: list[Paper],
    year_from: int | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """Upsert a research brief and link each paper row to it."""
    effective_limit = limit if limit is not None else len(papers)
    markdown = render_brief(query, year_from, papers)
    brief_doc = build_brief_document(query, year_from, effective_limit, papers, markdown)

    observe_document_size(brief_doc)
    brief_action = await upsert_document(pool, brief_doc)
    record_document_change(brief_doc, brief_action)

    papers_inserted = 0
    papers_updated = 0
    papers_noop = 0
    for paper in papers:
        try:
            paper_doc = build_paper_document(
                paper,
                query=query,
                parent_document_id=brief_doc["document_id"],
            )
        except ValueError:
            continue
        observe_document_size(paper_doc)
        action = await upsert_document(pool, paper_doc)
        record_document_change(paper_doc, action)
        if action == "inserted":
            papers_inserted += 1
        elif action == "updated":
            papers_updated += 1
        else:
            papers_noop += 1

    return {
        "brief_document_id": brief_doc["document_id"],
        "brief_action": brief_action,
        "results_count": len(papers),
        "papers_inserted": papers_inserted,
        "papers_updated": papers_updated,
        "papers_noop": papers_noop,
        "markdown": markdown,
    }
