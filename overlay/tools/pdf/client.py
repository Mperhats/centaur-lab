"""PdfClient — agent-facing facade over the ``fetch`` and ``parse`` stages.

The single ``fetch_and_parse`` method is the tool surface exposed to
agents: accepts a URL, streams the PDF into memory under a hard size
cap, runs the parser fallback chain, and returns a flat envelope dict
suitable for the centaur tool protocol.

All exceptions are caught and translated to ``{"status": "error", ...}``
envelopes. A ``stage`` field identifies *where* the failure occurred
(``"fetch"`` or ``"parse"``) so an agent can decide whether to retry
the URL, try a different URL, or escalate.

Following the upstream archiver pattern, the module exposes a
zero-argument :func:`_client` factory — the centaur runner instantiates
clients via this hook.
"""

from __future__ import annotations

from typing import Any

from .fetch.http import (
    DEFAULT_MAX_BYTES,
    DEFAULT_TIMEOUT_S,
    DEFAULT_USER_AGENT,
    PdfFetchError,
    PdfTooLargeError,
    download_pdf,
)
from .parse.markdown import DEFAULT_MIN_TEXT_SIZE, PdfParseError, parse_pdf


class PdfClient:
    """Fetch and parse PDFs over public HTTPS."""

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
            On success::

                {
                    "status": "ok",
                    "url": "...",
                    "size_bytes": int,
                    "mime_type": "application/pdf",
                    "markdown": "...",
                    "parser_used": "pymupdf4llm" | "pymupdf" | "pypdf",
                    "char_count": int,
                }

            On failure::

                {
                    "status": "error",
                    "stage": "fetch" | "parse",
                    "error": "<human-readable message>",
                    "url": "...",
                }
        """
        try:
            data, mime_type = download_pdf(
                url,
                timeout=timeout,
                max_bytes=max_bytes,
                user_agent=user_agent,
            )
        except PdfTooLargeError as exc:
            return {
                "status": "error",
                "stage": "fetch",
                "error": str(exc),
                "url": url,
                "max_bytes": max_bytes,
            }
        except PdfFetchError as exc:
            return {
                "status": "error",
                "stage": "fetch",
                "error": str(exc),
                "url": url,
            }

        try:
            markdown, parser_used = parse_pdf(data, min_size=min_size)
        except PdfParseError as exc:
            return {
                "status": "error",
                "stage": "parse",
                "error": str(exc),
                "url": url,
                "size_bytes": len(data),
                "mime_type": mime_type,
            }

        return {
            "status": "ok",
            "url": url,
            "size_bytes": len(data),
            "mime_type": mime_type,
            "markdown": markdown,
            "parser_used": parser_used,
            "char_count": len(markdown),
        }


def _client() -> PdfClient:
    return PdfClient()
