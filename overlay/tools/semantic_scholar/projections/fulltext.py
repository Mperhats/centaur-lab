"""Project a parsed PDF into a paper-fulltext document row.

Produces the ``source_type="paper_fulltext"`` companion row that lives
alongside the paper-metadata row in ``company_context_documents``. The
body is the parsed Markdown produced by the PDF tool, capped at
:data:`FULLTEXT_BODY_MAX_BYTES` UTF-8 bytes so the BM25 index stays
within its per-document budget.

Pure function: typed S2 input plus a Markdown string in, plain ``dict``
out. The raw PDF bytes are projected separately by
:mod:`semantic_scholar.projections.archive` into the ``paper_archives``
row that pairs with this companion document.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Final

from pdf.utils import truncate_utf8
from semanticscholar.Paper import Paper

from semantic_scholar.utils import content_hash

FULLTEXT_BODY_MAX_BYTES: Final[int] = 1 * 1024 * 1024  # 1 MiB cap on indexed body bytes.


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

    Mirrors :func:`semantic_scholar.projections.paper.build_paper_document`
    but emits the full-text companion row keyed off the same Semantic
    Scholar paper. The body is the parsed Markdown, capped at
    :data:`FULLTEXT_BODY_MAX_BYTES` UTF-8 bytes for BM25 index
    efficiency. The parent metadata row's ``document_id`` is derived
    from ``paper.paperId`` (the two share the same paper key by
    construction).

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
