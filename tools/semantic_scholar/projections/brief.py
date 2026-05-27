"""Project a Semantic Scholar paper list into a research-brief row.

Two pure projection helpers:

* :func:`render_brief` — Markdown view over a paper list. Re-used by
  both the ``research_brief`` tool method (returned to the agent in the
  response) and ``save_papers`` (persisted as ``body``).
* :func:`build_brief_document` — assemble the ``company_context_documents``
  row dict for the brief.

Persistence lives in the call site
(``tools/semantic_scholar/client.research_brief`` and
``workflows/save_papers.handler``); both inline the ``_upsert_document``
SQL + ``vm_metrics`` shim that mirrors upstream's
``company_context_documents`` convention.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any

from tools.semantic_scholar.utils import canonical_json, content_hash

_BRIEF_ABSTRACT_TRUNCATE = 500
_SLACK_BRIEF_ABSTRACT_CHARS = 120
_SLACK_BRIEF_MAX_PAPERS = 6
_BRIEF_TITLE_QUERY_TRUNCATE = 80
_BRIEF_ID_HEX_LEN = 16
_BRIEF_MAX_AUTHORS_INLINE = 3


def brief_id_for(query: str, year_from: int | None) -> str:
    """Stable, case-insensitive id suffix for the brief document."""
    canonical = canonical_json([query.strip().lower(), year_from])
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:_BRIEF_ID_HEX_LEN]


def _normalize_oneline(text: str) -> str:
    return " ".join(text.split())


def _format_authors(authors: list[Any]) -> str:
    names: list[str] = []
    for entry in authors or []:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        if name:
            names.append(str(name))
    if not names:
        return "Unknown"
    if len(names) <= _BRIEF_MAX_AUTHORS_INLINE:
        return ", ".join(names)
    head = ", ".join(names[:_BRIEF_MAX_AUTHORS_INLINE])
    return f"{head} +{len(names) - _BRIEF_MAX_AUTHORS_INLINE} more"


def _paper_url(paper: dict[str, Any]) -> str:
    url = paper.get("url")
    if url:
        return str(url)
    paper_id = paper.get("paperId")
    if paper_id:
        return f"https://www.semanticscholar.org/paper/{paper_id}"
    return ""


def _format_abstract(paper: dict[str, Any]) -> str:
    abstract = paper.get("abstract")
    if not abstract:
        return "No abstract available."
    text = str(abstract)
    if len(text) > _BRIEF_ABSTRACT_TRUNCATE:
        return text[:_BRIEF_ABSTRACT_TRUNCATE] + "..."
    return text


def render_brief(query: str, year_from: int | None, papers: list[dict[str, Any]]) -> str:
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
        display_title = _normalize_oneline(str(paper.get("title") or "Untitled"))
        year = paper.get("year")
        year_text = str(year) if isinstance(year, int) else "Unknown"
        citations = int(paper.get("citationCount") or 0)
        authors_value = paper.get("authors")
        authors_list = authors_value if isinstance(authors_value, list) else []
        lines.extend(
            [
                f"### {index}. {display_title}",
                "",
                f"- Authors: {_format_authors(authors_list)}",
                f"- Year: {year_text}",
                f"- Citations: {citations}",
                f"- URL: {_paper_url(paper)}",
                "",
                _format_abstract(paper),
                "",
            ]
        )
    return "\n".join(lines)


def render_brief_compact(
    query: str,
    papers: list[dict[str, Any]],
    *,
    max_papers: int = _SLACK_BRIEF_MAX_PAPERS,
) -> str:
    """Short Slack-friendly lit review (title + numbered one-liners)."""
    display_query = _normalize_oneline(query)
    if not papers:
        return f"**Research brief** — {display_query}\n\n_No papers found._"

    lines = [
        f"**Research brief** — {display_query}",
        f"_{min(len(papers), max_papers)} of {len(papers)} top hits_",
        "",
    ]
    for index, paper in enumerate(papers[:max_papers], start=1):
        title = _normalize_oneline(str(paper.get("title") or "Untitled"))
        year = paper.get("year")
        year_text = f" ({year})" if isinstance(year, int) else ""
        abstract = (paper.get("abstract") or "").strip()
        if len(abstract) > _SLACK_BRIEF_ABSTRACT_CHARS:
            abstract = abstract[: _SLACK_BRIEF_ABSTRACT_CHARS - 1].rstrip() + "…"
        if not abstract:
            abstract = "_No abstract._"
        lines.append(f"{index}. **{title}**{year_text} — {abstract}")
    return "\n".join(lines)


def build_brief_document(
    query: str,
    year_from: int | None,
    limit: int,
    papers: list[dict[str, Any]],
    markdown: str,
) -> dict[str, Any]:
    """Project the rendered brief into a ``company_context_documents`` row."""
    suffix = brief_id_for(query, year_from)
    truncated_query = query[:_BRIEF_TITLE_QUERY_TRUNCATE]
    title = f"Research Brief: {truncated_query}"
    paper_ids = [str(p["paperId"]) for p in papers if p.get("paperId")]
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
