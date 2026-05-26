"""PdfClient — agent-facing facade over the ``fetch`` and ``parse`` stages.

Two methods are exposed to agents:

* :meth:`fetch_and_parse(url)` — the main path: stream a PDF from a URL
  and parse it to Markdown.
* :meth:`parse_file(path)` — the fallback path: parse a PDF already on
  disk (typically an operator-supplied attachment saved by the centaur
  attachment pipeline). Lets an agent recover when a URL is gated
  behind a paywall, JS-rendered landing page, or other "the bytes
  exist but ``httpx`` can't get them" failure mode.

Both methods translate the internal exception hierarchy
(:class:`PdfHttpError`, :class:`PdfNetworkError`, :class:`PdfTooLargeError`,
:class:`PdfNotPdfError`, :class:`PdfParseError`,
:class:`PdfInsufficientTextError`) into ``{"status": "error", ...}``
envelopes. The error envelope carries a structured ``reason`` code an
agent can branch on, plus a human-readable ``suggestion`` string that
tells the operator what to try next.

Following the upstream archiver pattern, the module exposes a
zero-argument :func:`_client` factory — the centaur runner instantiates
clients via this hook.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .fetch.http import (
    DEFAULT_MAX_BYTES,
    DEFAULT_TIMEOUT_S,
    DEFAULT_USER_AGENT,
    PdfFetchError,
    PdfHttpError,
    PdfNetworkError,
    PdfNotPdfError,
    PdfTooLargeError,
    download_pdf,
)
from .parse.markdown import (
    DEFAULT_MIN_TEXT_SIZE,
    PdfInsufficientTextError,
    PdfParseError,
    parse_pdf,
)

# Suggestion strings live as module-level constants (not formatted at
# raise time) so a) they're greppable, and b) they're cheap to A/B in
# one place when an agent loop reveals a more useful phrasing.

_SUGGEST_MANUAL_ATTACH = (
    "If this URL keeps failing, ask the operator to attach the PDF and call "
    "`parse_file(attachment_path)` instead."
)

_SUGGEST_HTTP_ERROR = (
    "Server returned an HTTP error. If the URL is paywalled or requires login, "
    "ask the operator to attach the PDF and call `parse_file(attachment_path)`."
)

_SUGGEST_TOO_LARGE = (
    "PDF exceeds the size cap. Increase `max_bytes` if appropriate, or split the document upstream."
)

_SUGGEST_NOT_A_PDF = (
    "Server returned non-PDF content — likely a paywall page or a JS-redirect "
    "landing page. Open the URL in a browser, save the PDF, and call "
    "`parse_file(attachment_path)`."
)

_SUGGEST_INSUFFICIENT_TEXT = (
    "The PDF likely contains scanned images without embedded text. Route to an "
    "OCR pipeline (the parser fallback chain only handles born-digital PDFs)."
)

_SUGGEST_PARSE_FAILED = (
    "All parser backends failed on this PDF. See `per_backend` for per-parser "
    "detail. The file may be corrupt or an unusual PDF variant."
)

_SUGGEST_FILE_NOT_FOUND = (
    "Path does not exist or is not a regular file. Verify the attachment path and retry."
)


class PdfClient:
    """Fetch and parse PDFs over public HTTPS, or parse local files."""

    def fetch_and_parse(
        self,
        url: str,
        *,
        timeout: float = DEFAULT_TIMEOUT_S,
        max_bytes: int = DEFAULT_MAX_BYTES,
        user_agent: str = DEFAULT_USER_AGENT,
        min_size: int = DEFAULT_MIN_TEXT_SIZE,
    ) -> dict[str, Any]:
        """Stream a PDF from ``url`` and parse it to Markdown.

        Args:
            url: HTTPS URL to a PDF. Redirects are followed.
            timeout: Per-request HTTP timeout in seconds.
            max_bytes: Hard cap on PDF size; larger bodies are aborted
                mid-stream and returned as a ``status="error"`` envelope.
            user_agent: HTTP ``User-Agent`` header. Most callers should
                leave this default.
            min_size: Minimum characters of extracted text to consider a
                parse successful. Parsers below this threshold fall
                through to the next stage in the chain.

        Returns:
            Success::

                {"status": "ok", "url", "size_bytes", "mime_type",
                 "markdown", "parser_used", "char_count"}

            Failure::

                {"status": "error", "stage": "fetch" | "parse",
                 "reason": "<code>", "error": "<message>",
                 "suggestion": "<operator-actionable hint>",
                 "url": "...", ...stage-specific extras...}

            Fetch reason codes: ``http_error`` (with ``status_code``),
            ``network_error``, ``too_large`` (with ``max_bytes``,
            ``received_bytes``), ``not_a_pdf`` (with ``mime_type``).

            Parse reason codes: ``all_backends_failed``,
            ``insufficient_text``. Both carry a ``per_backend`` dict
            mapping parser name → failure description.
        """
        try:
            data, mime_type = download_pdf(
                url,
                timeout=timeout,
                max_bytes=max_bytes,
                user_agent=user_agent,
            )
        except PdfFetchError as exc:
            return _fetch_error_envelope(exc, url=url, max_bytes=max_bytes)

        try:
            markdown, parser_used = parse_pdf(data, min_size=min_size)
        except PdfParseError as exc:
            return _parse_error_envelope(
                exc,
                extra={
                    "url": url,
                    "size_bytes": len(data),
                    "mime_type": mime_type,
                },
            )

        return {
            "status": "ok",
            "url": url,
            "size_bytes": len(data),
            "mime_type": mime_type,
            "markdown": markdown,
            "parser_used": parser_used,
            "char_count": len(markdown),
        }

    def parse_file(
        self,
        path: str,
        *,
        min_size: int = DEFAULT_MIN_TEXT_SIZE,
    ) -> dict[str, Any]:
        """Parse a local PDF file to Markdown.

        The fallback path when :meth:`fetch_and_parse` returns an error
        the operator can recover from manually (paywall, JS-redirect,
        publisher rate-limit). Saves the operator-supplied attachment
        via the existing centaur attachment pipeline, then call this.

        Args:
            path: Filesystem path to a PDF.
            min_size: Same semantics as :meth:`fetch_and_parse`.

        Returns:
            Success::

                {"status": "ok", "path", "size_bytes",
                 "markdown", "parser_used", "char_count"}

            Failure::

                {"status": "error", "stage": "load" | "parse",
                 "reason": "<code>", "error": "<message>",
                 "suggestion": "<operator-actionable hint>",
                 "path": "...", ...stage-specific extras...}

            Load reason codes: ``file_not_found``.
            Parse reason codes: same as :meth:`fetch_and_parse`.
        """
        file_path = Path(path)
        if not file_path.is_file():
            return {
                "status": "error",
                "stage": "load",
                "reason": "file_not_found",
                "error": f"Not a regular file: {file_path}",
                "suggestion": _SUGGEST_FILE_NOT_FOUND,
                "path": str(file_path),
            }

        data = file_path.read_bytes()

        try:
            markdown, parser_used = parse_pdf(data, min_size=min_size)
        except PdfParseError as exc:
            return _parse_error_envelope(
                exc,
                extra={
                    "path": str(file_path),
                    "size_bytes": len(data),
                },
            )

        return {
            "status": "ok",
            "path": str(file_path),
            "size_bytes": len(data),
            "markdown": markdown,
            "parser_used": parser_used,
            "char_count": len(markdown),
        }


def _fetch_error_envelope(exc: PdfFetchError, *, url: str, max_bytes: int) -> dict[str, Any]:
    """Translate a fetch exception into the agent-facing envelope.

    Pulls structured fields off the specific exception subclass when
    available, falls back to the base ``PdfFetchError`` shape otherwise.
    """
    envelope: dict[str, Any] = {
        "status": "error",
        "stage": "fetch",
        "reason": exc.reason,
        "error": str(exc),
        "url": url,
    }

    if isinstance(exc, PdfHttpError):
        envelope["status_code"] = exc.status_code
        envelope["suggestion"] = _SUGGEST_HTTP_ERROR
    elif isinstance(exc, PdfTooLargeError):
        envelope["max_bytes"] = exc.max_bytes
        envelope["received_bytes"] = exc.received_bytes
        envelope["suggestion"] = _SUGGEST_TOO_LARGE
    elif isinstance(exc, PdfNotPdfError):
        envelope["mime_type"] = exc.mime_type
        envelope["suggestion"] = _SUGGEST_NOT_A_PDF
    elif isinstance(exc, PdfNetworkError):
        envelope["suggestion"] = _SUGGEST_MANUAL_ATTACH
    else:
        envelope["max_bytes"] = max_bytes
        envelope["suggestion"] = _SUGGEST_MANUAL_ATTACH

    return envelope


def _parse_error_envelope(exc: PdfParseError, *, extra: dict[str, Any]) -> dict[str, Any]:
    """Translate a parse exception into the agent-facing envelope."""
    envelope: dict[str, Any] = {
        "status": "error",
        "stage": "parse",
        "reason": exc.reason,
        "error": str(exc),
        "per_backend": exc.per_backend,
        **extra,
    }
    if isinstance(exc, PdfInsufficientTextError):
        envelope["suggestion"] = _SUGGEST_INSUFFICIENT_TEXT
    else:
        envelope["suggestion"] = _SUGGEST_PARSE_FAILED
    return envelope


def _client() -> PdfClient:
    return PdfClient()
