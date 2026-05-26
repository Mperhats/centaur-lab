"""Shared helpers for projecting parsed Semantic Scholar PDFs into the two sinks we own.

Semantic-Scholar full-text archival fans out into two storage sinks rather
than one:

1. ``paper_archives`` (overlay-owned) — the raw PDF bytes plus parse
   metadata. Source-of-truth for the original document. Lets us re-parse
   without re-fetching from the publisher (rate-limited, sometimes flaky)
   and lets us evolve the parser without mutating downstream rows.
2. ``company_context_documents`` (upstream-owned) with
   ``source_type="paper_fulltext"`` — the parsed Markdown body, projected
   via :func:`build_fulltext_document`. This is what BM25 indexes and what
   the rest of the platform searches over.

Splitting the two sinks lets us drop ``paper_archives`` later for cost
reasons (BYTEA columns are expensive at scale) without losing the
BM25-indexed text, and lets us re-parse a stored PDF into the
``paper_fulltext`` row without going back to the publisher.

The metadata-row companion (``source_type="paper"``) is built by
:func:`centaur_lab.paper_document.build_paper_document`; full-text rows
parent off of it via ``parent_document_id``.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any, Final, Literal

from centaur_lab.paper_document import _canonical_json, _content_hash

FULLTEXT_BODY_MAX_BYTES: Final[int] = 1 * 1024 * 1024  # 1 MiB cap on indexed body bytes.


def _safe_truncate_utf8(text: str, max_bytes: int) -> tuple[str, bool]:
    """Truncate ``text`` to ``max_bytes`` UTF-8 bytes without splitting a codepoint."""
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text, False
    return encoded[:max_bytes].decode("utf-8", errors="ignore"), True


def compute_pdf_sha256(data: bytes) -> str:
    """Hex SHA-256 digest of the given PDF bytes."""
    return hashlib.sha256(data).hexdigest()


def build_fulltext_document(
    paper: dict[str, Any],
    *,
    parsed_text: str,
    parent_document_id: str,
    parser_used: str,
    truncated: bool,
    pdf_sha256: str,
    source_url: str | None = None,
) -> dict[str, Any]:
    """Project a parsed PDF into a ``source_type="paper_fulltext"`` document row.

    Mirrors :func:`centaur_lab.paper_document.build_paper_document` but emits
    the full-text companion row keyed off the same Semantic Scholar paper.
    The body is the parsed Markdown, capped at :data:`FULLTEXT_BODY_MAX_BYTES`
    UTF-8 bytes for BM25 index efficiency.

    Args:
        paper: A paper dict as returned by the Semantic Scholar Graph API;
            only ``paperId``, ``title``, ``authors``, ``year``, and ``url``
            are read here.
        parsed_text: The Markdown produced by the PDF parser. Will be
            truncated in-place if it exceeds the byte cap.
        parent_document_id: The metadata row's ``document_id``
            (typically ``semantic_scholar:paper:{paperId}``); links the
            full-text row back to its metadata sibling.
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
        A dict shaped for the canonical ``_upsert_document`` SQL in
        ``company_context_documents.py`` (see also
        ``paper_document.upsert_document``).

    Raises:
        ValueError: If ``paper.paperId`` is missing or empty.
    """
    paper_id = paper.get("paperId")
    if not paper_id:
        raise ValueError("paper.paperId is required to build fulltext document")
    paper_id_str = str(paper_id)

    title = str(paper.get("title") or "Untitled")

    authors_raw = paper.get("authors") or []
    author_dicts = [a for a in authors_raw if isinstance(a, dict)]
    first_author = author_dicts[0] if author_dicts else None
    author_id = ""
    author_name = ""
    if first_author is not None:
        raw_author_id = first_author.get("authorId")
        author_id = str(raw_author_id) if raw_author_id else ""
        author_name = str(first_author.get("name") or "")

    year = paper.get("year")
    year_int = year if isinstance(year, int) else None

    url = paper.get("url") or f"https://www.semanticscholar.org/paper/{paper_id_str}"

    body, body_was_truncated = _safe_truncate_utf8(parsed_text, FULLTEXT_BODY_MAX_BYTES)

    occurred_at: datetime | None = (
        datetime(year_int, 1, 1, tzinfo=UTC) if year_int is not None else None
    )

    # OVERLAY: include all metadata keys with explicit nulls (instead of
    # filtering ``None`` out) so JSONB key-presence checks behave the
    # same way for ``paper_fulltext`` rows as for upstream rows.
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
        "content_hash": _content_hash(title, body, url, metadata),
        "metadata": metadata,
    }


async def upsert_paper_archive(
    pool: Any,
    row: dict[str, Any],
) -> Literal["inserted", "updated", "noop"]:
    """Idempotently insert/update a ``paper_archives`` row.

    Idempotency contract: the ``(paper_id, pdf_sha256)`` pair is the
    no-change key. If a row already exists for ``paper_id`` and its
    ``pdf_sha256`` matches the new one, this returns ``"noop"`` immediately
    without issuing an UPSERT — callers can rely on this to skip re-parsing
    or re-uploading unchanged PDFs.

    Args:
        pool: An ``asyncpg.Pool`` / ``asyncpg.Connection`` (or any object
            exposing the same async ``fetchval`` / ``execute`` surface,
            e.g. test mocks). Typed ``Any`` because the three real callers
            differ in concrete type.
        row: Dict with the ``paper_archives`` column values:
            ``paper_id``, ``source_url``, ``mime_type``, ``size_bytes``,
            ``pdf_sha256``, ``pdf_bytes``, ``parsed_text``, ``parser_used``,
            ``truncated``, ``metadata`` (a dict; serialized to JSONB via
            :func:`_canonical_json`).

    Returns:
        ``"inserted"`` if no prior row existed and the UPSERT inserted one;
        ``"updated"`` if a prior row existed and the UPSERT updated it;
        ``"noop"`` if the prior row's ``pdf_sha256`` already matched, or if
        the UPSERT's ``RETURNING`` row count came back as zero.
    """
    existing_hash = await pool.fetchval(
        "SELECT pdf_sha256 FROM paper_archives WHERE paper_id = $1",
        row["paper_id"],
    )
    if existing_hash == row["pdf_sha256"]:
        return "noop"

    status = await pool.execute(
        "INSERT INTO paper_archives ("
        "paper_id, source_url, mime_type, size_bytes, pdf_sha256, pdf_bytes, "
        "parsed_text, parser_used, truncated, metadata, archived_at, updated_at"
        ") VALUES ("
        "$1, $2, $3, $4, $5, $6, $7, $8, $9, $10::jsonb, NOW(), NOW()"
        ") ON CONFLICT (paper_id) DO UPDATE SET "
        "source_url = EXCLUDED.source_url, "
        "mime_type = EXCLUDED.mime_type, "
        "size_bytes = EXCLUDED.size_bytes, "
        "pdf_sha256 = EXCLUDED.pdf_sha256, "
        "pdf_bytes = EXCLUDED.pdf_bytes, "
        "parsed_text = EXCLUDED.parsed_text, "
        "parser_used = EXCLUDED.parser_used, "
        "truncated = EXCLUDED.truncated, "
        "metadata = EXCLUDED.metadata, "
        "updated_at = NOW()",
        row["paper_id"],
        row["source_url"],
        row["mime_type"],
        row["size_bytes"],
        row["pdf_sha256"],
        row["pdf_bytes"],
        row["parsed_text"],
        row["parser_used"],
        row["truncated"],
        _canonical_json(row.get("metadata") or {}),
    )
    if not status.endswith(" 1"):
        return "noop"
    return "updated" if existing_hash else "inserted"
