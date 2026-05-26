"""Cross-stage helpers for the ``pdf`` tool.

Anything used by both the ``fetch`` and ``parse`` stages — or by the
``client``/``cli`` glue around them — lives here. Stage-local
configuration (timeouts, parser thresholds, etc.) stays in the stage
file where it's consumed.
"""

from __future__ import annotations

import hashlib
from urllib.parse import unquote, urlparse


def force_pdf_mime(url: str, content_type: str | None) -> str:
    """Trust the URL extension over the HTTP ``Content-Type`` for PDFs.

    Academic publishers routinely serve PDFs as
    ``application/octet-stream`` or even ``text/html`` (when they 30x
    through a JavaScript redirector that resolves to a binary blob).
    The path extension is the more reliable signal — when it's
    ``.pdf``, pin the MIME to ``application/pdf`` regardless of what
    the server claimed. Otherwise fall back to the server's
    ``Content-Type``, stripped of parameters.
    """
    parsed = urlparse(url)
    path = parsed.path.lower()
    if path.endswith(".pdf"):
        return "application/pdf"

    if not content_type:
        return "application/octet-stream"
    return content_type.split(";", 1)[0].strip() or "application/octet-stream"


def derive_filename_from_url(url: str, *, default: str = "downloaded.pdf") -> str:
    """Pull a sensible local filename out of a PDF URL.

    Strips query/fragment, URL-decodes the basename, and returns it if
    it looks PDF-ish. Falls back to ``default`` when the URL ends in a
    slash or has no usable basename — happens with publisher landing
    pages that 30x to a content-disposition-named PDF (we can't see
    that filename until after the request, so just punt to default).
    """
    parsed = urlparse(url)
    basename = unquote(parsed.path.rsplit("/", 1)[-1])
    if not basename:
        return default
    if not basename.lower().endswith(".pdf"):
        return f"{basename}.pdf"
    return basename


def compute_pdf_sha256(data: bytes) -> str:
    """Hex SHA-256 digest of PDF bytes.

    Used by callers that need a stable content identifier for an
    archived PDF — idempotency keys for ``paper_archives`` upserts,
    dedup against a content-addressed store, change detection across
    re-fetches. Pure: same bytes in, same hex digest out.
    """
    return hashlib.sha256(data).hexdigest()


def truncate_utf8(text: str, max_bytes: int) -> tuple[str, bool]:
    """Truncate ``text`` to at most ``max_bytes`` UTF-8 bytes without splitting a codepoint.

    BM25 and JSONB column limits are stated in bytes, not characters, so
    a naive ``text[:n]`` slice would either over-truncate (multi-byte
    codepoints) or yield a string that re-encodes past the cap. We
    encode, slice, and decode with ``errors="ignore"`` so the last
    partial codepoint is dropped cleanly. Returns
    ``(truncated_text, was_truncated)``; ``was_truncated`` is ``True``
    only when the input actually exceeded the cap.
    """
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text, False
    return encoded[:max_bytes].decode("utf-8", errors="ignore"), True
