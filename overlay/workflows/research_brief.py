"""Workflow: search Semantic Scholar and persist a research brief + its papers.

Given a free-text query, runs a Semantic Scholar paper search, renders a
Markdown brief, and persists both the brief itself (``source_type="research_brief"``)
and each underlying paper as ``source_type="paper"`` rows in
``company_context_documents``. Each paper row's ``parent_document_id`` is
stamped with the brief's ``document_id`` so callers can pivot from a paper
back to the brief that surfaced it. On-demand only (no ``SCHEDULE``).
"""

from __future__ import annotations

import hashlib
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

if TYPE_CHECKING:
    from api.workflow_engine import WorkflowContext

from _metrics import emit_document_metrics
from _paper_document import (
    _canonical_json,
    _content_hash,
    build_paper_document,
    upsert_document,
)

from tools.semantic_scholar.client import SemanticScholarClient

WORKFLOW_NAME = "research_brief"

MAX_LIMIT = 20
ABSTRACT_TRUNCATE = 500
TITLE_QUERY_TRUNCATE = 80
BRIEF_ID_HEX_LEN = 16
MAX_AUTHORS_INLINE = 3


@dataclass
class Input:
    """Runtime options for the ``research_brief`` workflow."""

    query: str
    limit: int = 5
    year_from: int | None = None


def _brief_id_for(query: str, year_from: int | None) -> str:
    """Stable, case-insensitive id suffix for the brief document.

    Date is intentionally excluded so re-running the same query updates the
    same row instead of accreting one brief per run. Reuses the helper
    module's ``_canonical_json`` so any future tweak to that canonicalization
    flows through here without silently drifting brief IDs.
    """
    canonical = _canonical_json([query.strip().lower(), year_from])
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:BRIEF_ID_HEX_LEN]


def _normalize_oneline(text: str) -> str:
    """Collapse all whitespace to single spaces; safe for Markdown headings."""
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
    if len(names) <= MAX_AUTHORS_INLINE:
        return ", ".join(names)
    head = ", ".join(names[:MAX_AUTHORS_INLINE])
    return f"{head} +{len(names) - MAX_AUTHORS_INLINE} more"


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
    if len(text) > ABSTRACT_TRUNCATE:
        return text[:ABSTRACT_TRUNCATE] + "..."
    return text


def _render_brief(query: str, year_from: int | None, papers: list[dict[str, Any]]) -> str:
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


def _build_brief_document(
    query: str,
    year_from: int | None,
    limit: int,
    papers: list[dict[str, Any]],
    markdown: str,
) -> dict[str, Any]:
    """Project the rendered brief into a ``company_context_documents`` row."""
    suffix = _brief_id_for(query, year_from)
    truncated_query = query[:TITLE_QUERY_TRUNCATE]
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
        "source_updated_at": None,
        "content_hash": _content_hash(title, markdown, "", metadata),
        "metadata": metadata,
    }


async def handler(inp: Input, ctx: WorkflowContext) -> dict[str, Any]:
    """Run a Semantic Scholar search and persist the brief + papers."""
    if inp.query.strip() == "":
        ctx.log("research_brief_skipped_empty_query")
        return {"status": "skipped", "reason": "empty_query"}

    if inp.limit <= 0:
        ctx.log("research_brief_skipped_invalid_limit", limit=inp.limit)
        return {"status": "skipped", "reason": "invalid_limit"}

    clamped_limit = min(inp.limit, MAX_LIMIT)
    if clamped_limit != inp.limit:
        ctx.log(
            "research_brief_limit_clamped",
            requested=inp.limit,
            used=clamped_limit,
        )

    client = SemanticScholarClient()
    try:
        papers = client.search_papers(
            query=inp.query,
            limit=clamped_limit,
            year_from=inp.year_from,
        )

        markdown = _render_brief(inp.query, inp.year_from, papers)
        brief_doc = _build_brief_document(
            inp.query,
            inp.year_from,
            clamped_limit,
            papers,
            markdown,
        )

        if not papers:
            ctx.log(
                "research_brief_no_results",
                query=inp.query,
                year_from=inp.year_from,
            )
            brief_action = await upsert_document(ctx._pool, brief_doc)
            emit_document_metrics(brief_doc, brief_action)
            return {
                "status": "completed",
                "brief_document_id": brief_doc["document_id"],
                "brief_action": brief_action,
                "results_count": 0,
                "papers_inserted": 0,
                "papers_updated": 0,
                "papers_noop": 0,
                "markdown": markdown,
            }

        brief_action = await upsert_document(ctx._pool, brief_doc)
        emit_document_metrics(brief_doc, brief_action)

        papers_inserted = 0
        papers_updated = 0
        papers_noop = 0
        for paper in papers:
            try:
                paper_doc = build_paper_document(paper, query=inp.query)
            except ValueError as exc:
                ctx.log(
                    "research_brief_paper_skipped",
                    error=str(exc),
                    paper_id=paper.get("paperId"),
                )
                continue
            action = await upsert_document(
                ctx._pool,
                paper_doc,
                parent_document_id=brief_doc["document_id"],
            )
            emit_document_metrics(paper_doc, action)
            if action == "inserted":
                papers_inserted += 1
            elif action == "updated":
                papers_updated += 1
            else:
                papers_noop += 1
    finally:
        client.close()

    ctx.log(
        "research_brief_completed",
        query=inp.query,
        year_from=inp.year_from,
        brief_action=brief_action,
        brief_document_id=brief_doc["document_id"],
        papers_inserted=papers_inserted,
        papers_updated=papers_updated,
        papers_noop=papers_noop,
    )

    return {
        "status": "completed",
        "brief_document_id": brief_doc["document_id"],
        "brief_action": brief_action,
        "results_count": len(papers),
        "papers_inserted": papers_inserted,
        "papers_updated": papers_updated,
        "papers_noop": papers_noop,
        "markdown": markdown,
    }
