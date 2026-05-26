"""Cross-stage helpers for the ``pdf`` tool.

Anything used by both the ``fetch`` and ``parse`` stages — or by the
``client``/``cli`` glue around them — lives here. Stage-local
configuration (timeouts, parser thresholds, etc.) stays in the stage
file where it's consumed.
"""

from __future__ import annotations

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
