"""HTTPS streaming strategy for downloading a PDF.

Bounded-memory streaming is the whole point of this module: arbitrary
publisher hosts will happily serve gigabyte PDFs, HTML 404 bodies, or
paywall pages, and the API pod must not OOM on any of them. We pull
the body chunk-by-chunk and abort the moment ``total_bytes`` crosses
``max_bytes`` — never buffering the full response just to measure it.

The function returns ``(body_bytes, mime_type)``. ``mime_type`` is
sniffed via :func:`utils.force_pdf_mime` — see that helper for the
URL-extension-beats-Content-Type rationale.
"""

from __future__ import annotations

import logging
from typing import Final

import httpx

from pdf.utils import force_pdf_mime

log = logging.getLogger(__name__)

DEFAULT_USER_AGENT: Final[str] = "centaur-pdf/0.1"
DEFAULT_MAX_BYTES: Final[int] = 50 * 1024 * 1024
DEFAULT_TIMEOUT_S: Final[float] = 60.0


class PdfFetchError(RuntimeError):
    """Non-recoverable PDF fetch failure (HTTP error, network error, redirect loop)."""


class PdfTooLargeError(PdfFetchError):
    """Server response exceeded ``max_bytes`` before we stopped reading."""


def download_pdf(
    url: str,
    *,
    timeout: float = DEFAULT_TIMEOUT_S,
    max_bytes: int = DEFAULT_MAX_BYTES,
    user_agent: str = DEFAULT_USER_AGENT,
    transport: httpx.BaseTransport | None = None,
) -> tuple[bytes, str]:
    """Stream a PDF from ``url`` into memory, capped at ``max_bytes``.

    Args:
        url: HTTPS URL to a PDF (or arxiv abs page; many hosts redirect
            transparently — ``follow_redirects=True`` is on).
        timeout: Per-request timeout in seconds.
        max_bytes: Hard ceiling on response body; raises
            :class:`PdfTooLargeError` the first chunk that overflows.
        user_agent: Override the default UA. Some hosts (arxiv,
            elsevier) gate on UA — only override if you know what you're
            doing.
        transport: For tests: pass an ``httpx.MockTransport``.
            Production callers omit.

    Returns:
        ``(body_bytes, mime_type)``.

    Raises:
        PdfFetchError: HTTP 4xx/5xx, network error, redirect loop.
        PdfTooLargeError: Body exceeded ``max_bytes`` mid-stream.
    """
    headers = {"User-Agent": user_agent, "Accept": "application/pdf,*/*;q=0.5"}
    try:
        with (
            httpx.Client(
                timeout=timeout,
                follow_redirects=True,
                transport=transport,
            ) as client,
            client.stream("GET", url, headers=headers) as response,
        ):
            if response.status_code >= 400:
                body_snippet = response.read()[:200]
                raise PdfFetchError(
                    f"PDF fetch HTTP {response.status_code} for {url}: {body_snippet!r}"
                )

            buffer = bytearray()
            total = 0
            for chunk in response.iter_bytes():
                total += len(chunk)
                if total > max_bytes:
                    raise PdfTooLargeError(
                        f"PDF at {url} exceeded max_bytes={max_bytes} (read >= {total})"
                    )
                buffer.extend(chunk)

            content_type = response.headers.get("content-type")
    except httpx.RequestError as exc:
        raise PdfFetchError(f"PDF fetch network error for {url}: {exc}") from exc

    return bytes(buffer), force_pdf_mime(url, content_type)
