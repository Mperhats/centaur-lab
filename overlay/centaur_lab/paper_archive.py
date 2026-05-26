"""Shared archive-paper pipeline: fetch metadata, download PDF, parse, persist.

Lives outside ``tools/semantic_scholar`` so both the tool's agent-facing
``archive_paper`` method and the ``archive_papers`` workflow handler can
call into the same pipeline with their own pool, without the tool owning
DB state on behalf of callers that already have a pool.

The pipeline performs three idempotent writes per paper:

* ``paper_archives`` — raw PDF bytes + parser metadata (overlay-owned).
* ``company_context_documents`` — ``source_type="paper"`` metadata row.
* ``company_context_documents`` — ``source_type="paper_fulltext"`` row
  parented off the metadata row.

The ``pool`` argument accepts any object exposing the asyncpg pool/
connection async surface (``fetchval`` / ``execute``); the same call
shape works against ``asyncpg.Pool``, ``asyncpg.Connection``, and the
unit-test mocks in ``centaur_lab.testing``.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, Final

from centaur_lab.metrics import observe_document_size, record_document_change
from centaur_lab.paper_document import build_paper_document, upsert_document
from centaur_lab.paper_fulltext import (
    build_fulltext_document,
    compute_pdf_sha256,
    upsert_paper_archive,
)
from tools.semantic_scholar import pdf_fetch, pdf_parse

if TYPE_CHECKING:
    from tools.semantic_scholar.client import SemanticScholarClient

log = logging.getLogger(__name__)

MAX_PDF_BYTES: Final[int] = 50 * 1024 * 1024
PDF_DOWNLOAD_TIMEOUT_S: Final[float] = 60.0
PDF_USER_AGENT: Final[str] = "centaur-scientist/0.1 (paper-archive; +https://centaur.run)"
ARCHIVE_PARSER_MIN_SIZE: Final[int] = 100


async def archive_paper_to_pool(
    client: SemanticScholarClient,
    pool: Any,
    paper_id: str,
    *,
    source_url: str | None = None,
) -> dict[str, Any]:
    """Run the archive pipeline against an already-acquired pool.

    Returns the same envelope shape callers have always seen:
    ``{"status": "completed" | "skipped" | "noop" | "error", ...}``.
    Expected failure modes (HTTP error, parse error, oversized PDF, no
    PDF URL, empty paper_id) return an envelope; programming errors
    (pool down, missing migrations) propagate so the caller's wrapper
    can mark the run failed.
    """
    normalized_id = (paper_id or "").strip()
    if not normalized_id:
        return {"status": "error", "paper_id": paper_id, "error": "paper_id cannot be empty"}

    try:
        paper = await asyncio.to_thread(client.get_paper, normalized_id)
    except (ValueError, RuntimeError) as exc:
        return {"status": "error", "paper_id": normalized_id, "error": str(exc)}

    url = source_url or pdf_fetch.derive_pdf_url(paper)
    if not url:
        return {"status": "skipped", "paper_id": normalized_id, "reason": "no_pdf_url"}

    try:
        data, mime = await asyncio.to_thread(
            pdf_fetch.download_pdf,
            url,
            timeout=PDF_DOWNLOAD_TIMEOUT_S,
            max_bytes=MAX_PDF_BYTES,
            user_agent=PDF_USER_AGENT,
        )
    except pdf_fetch.PdfTooLargeError:
        return {
            "status": "skipped",
            "paper_id": normalized_id,
            "reason": "too_large",
            "source_url": url,
        }
    except pdf_fetch.PdfFetchError as exc:
        return {
            "status": "error",
            "paper_id": normalized_id,
            "source_url": url,
            "error": str(exc),
        }

    pdf_sha256 = compute_pdf_sha256(data)

    existing = await pool.fetchval(
        "SELECT pdf_sha256 FROM paper_archives WHERE paper_id = $1",
        normalized_id,
    )
    if existing == pdf_sha256:
        return {
            "status": "noop",
            "paper_id": normalized_id,
            "source_url": url,
            "archive_action": "noop",
            "pdf_sha256": pdf_sha256,
        }

    try:
        parsed_text, parser_used = await asyncio.to_thread(
            pdf_parse.parse_pdf_to_markdown,
            data,
            ARCHIVE_PARSER_MIN_SIZE,
        )
    except pdf_parse.PdfParseError as exc:
        return {
            "status": "error",
            "paper_id": normalized_id,
            "source_url": url,
            "error": str(exc),
        }

    paper_doc = build_paper_document(paper)
    observe_document_size(paper_doc)
    paper_action = await upsert_document(pool, paper_doc)
    record_document_change(paper_doc, paper_action)

    fulltext_doc = build_fulltext_document(
        paper,
        parsed_text=parsed_text,
        parser_used=parser_used,
        truncated=False,
        pdf_sha256=pdf_sha256,
        source_url=url,
    )
    observe_document_size(fulltext_doc)
    fulltext_action = await upsert_document(pool, fulltext_doc)
    record_document_change(fulltext_doc, fulltext_action)

    archive_action = await upsert_paper_archive(
        pool,
        {
            "paper_id": normalized_id,
            "source_url": url,
            "mime_type": mime,
            "size_bytes": len(data),
            "pdf_sha256": pdf_sha256,
            "pdf_bytes": data,
            "parsed_text": parsed_text,
            "parser_used": parser_used,
            "truncated": False,
            "metadata": {
                "paperId": normalized_id,
                "url": paper_doc["url"],
            },
        },
    )

    return {
        "status": "completed",
        "paper_id": normalized_id,
        "source_url": url,
        "parser_used": parser_used,
        "pdf_sha256": pdf_sha256,
        "size_bytes": len(data),
        "paper_document_id": paper_doc["document_id"],
        "paper_action": paper_action,
        "fulltext_document_id": fulltext_doc["document_id"],
        "fulltext_action": fulltext_action,
        "archive_action": archive_action,
    }
