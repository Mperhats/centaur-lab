"""Project a fetched + parsed PDF into a ``paper_archives`` row dict.

Distinct from :mod:`semantic_scholar.projections.fulltext` because the
two emit *different* table shapes: this module's output goes into
``paper_archives`` (raw ``pdf_bytes`` + ``pdf_sha256`` + a small JSONB
metadata blob); ``fulltext`` projects into ``company_context_documents``
for the BM25 index. The workflow's archive pipeline calls both for the
same paper so the same PDF is both indexable and recoverable.

Pure function: no DB, no ``await``.
"""

from __future__ import annotations

from typing import Any

from semanticscholar.Paper import Paper


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
