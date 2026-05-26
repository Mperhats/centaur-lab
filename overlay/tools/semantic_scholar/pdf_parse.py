"""Parse PDF bytes to Markdown/text via a 3-tier fallback chain.

We try parsers in descending order of structural fidelity, so the
caller gets the richest representation available without us paying the
cost of opening the PDF more than once per tier:

1. ``pymupdf4llm`` — produces Markdown, which preserves headings,
   tables, and reading order for downstream LLM consumption.
2. ``pymupdf`` — plain-text fallback when the Markdown converter
   chokes (scanned PDFs, exotic embedded fonts).
3. ``pypdf`` — pure-Python last resort that works in restricted
   environments where the MuPDF native extension can't load.

Between tiers we enforce a ``min_size`` floor so a parser that
silently returns an almost-empty string (a common failure mode for
image-only PDFs) doesn't masquerade as success.

Mirrors ``.scientist/ai_scientist/perform_llm_review.py::load_paper``
but operates on bytes (we stream PDFs over HTTP rather than write them
to a temp file).
"""

from __future__ import annotations

import io
import logging
from collections.abc import Callable

log = logging.getLogger(__name__)

DEFAULT_MIN_SIZE = 100


class PdfParseError(RuntimeError):
    """Raised when every parser tier fails or returns under ``min_size`` chars."""


def parse_pdf_to_markdown(
    data: bytes,
    min_size: int = DEFAULT_MIN_SIZE,
) -> tuple[str, str]:
    """Parse PDF ``data`` to text, returning ``(text, parser_used)``.

    ``parser_used`` is one of ``"pymupdf4llm"``, ``"pymupdf"``,
    ``"pypdf"``. Raises :class:`PdfParseError` if all three tiers fail
    or every produced text falls under ``min_size`` characters.
    """
    tiers: tuple[tuple[str, Callable[[bytes], str]], ...] = (
        ("pymupdf4llm", _pymupdf4llm_markdown),
        ("pymupdf", _pymupdf_text),
        ("pypdf", _pypdf_text),
    )

    last_error: PdfParseError | None = None
    for name, fn in tiers:
        try:
            text = fn(data)
        # Fallback chain intentionally broad: any tier exception (corrupt
        # PDF, missing native lib, encoding bug) must be swallowed so the
        # next tier gets a shot. BLE001 is not enabled in overlay/ruff.toml,
        # so no `noqa` is needed — the comment exists to flag intent for
        # future readers.
        except Exception as exc:
            log.warning(
                "pdf_parse_tier_failed",
                extra={"parser": name, "error": str(exc)},
            )
            last_error = PdfParseError(f"{name} raised: {exc}")
            continue

        if len(text or "") < min_size:
            log.info(
                "pdf_parse_tier_too_short",
                extra={"parser": name, "chars": len(text or "")},
            )
            last_error = PdfParseError(f"{name} returned {len(text or '')} chars (< {min_size})")
            continue

        return text, name

    raise PdfParseError(f"all parsers failed or produced < {min_size} chars: {last_error}")


def _pymupdf4llm_markdown(data: bytes) -> str:
    import pymupdf
    import pymupdf4llm

    doc = pymupdf.open(stream=data, filetype="pdf")
    try:
        return pymupdf4llm.to_markdown(doc)
    finally:
        doc.close()


def _pymupdf_text(data: bytes) -> str:
    import pymupdf

    doc = pymupdf.open(stream=data, filetype="pdf")
    try:
        return "".join(page.get_text() for page in doc)
    finally:
        doc.close()


def _pypdf_text(data: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    return "".join(page.extract_text() or "" for page in reader.pages)
