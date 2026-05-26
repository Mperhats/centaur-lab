"""HTTPS streaming strategy for downloading a PDF.

Bounded-memory streaming is the whole point of this module: arbitrary
publisher hosts will happily serve gigabyte PDFs, HTML 404 bodies, or
paywall pages, and the API pod must not OOM on any of them. We pull
the body chunk-by-chunk and abort the moment ``total_bytes`` crosses
``max_bytes`` — never buffering the full response just to measure it.

The function returns ``(body_bytes, mime_type)``. ``mime_type`` is
sniffed via :func:`utils.force_pdf_mime` — see that helper for the
URL-extension-beats-Content-Type rationale.

Failure modes are surfaced as distinct exception subclasses so the
client envelope can carry a structured ``reason`` code an agent can
branch on (retry, escalate, ask operator for the file, etc.):

* :class:`PdfHttpError` — server returned 4xx/5xx
* :class:`PdfNetworkError` — network-level failure (DNS, TCP, TLS, timeout)
* :class:`PdfTooLargeError` — body exceeded ``max_bytes`` mid-stream
* :class:`PdfNotPdfError` — response body did not start with the PDF
  magic number ``%PDF`` (almost always means the server returned HTML —
  a paywall page, a JS-redirect landing page, or a 200-status error
  page)
"""

from __future__ import annotations

import logging
from typing import ClassVar, Final

import httpx

from tools.pdf.utils import force_pdf_mime

log = logging.getLogger(__name__)

DEFAULT_USER_AGENT: Final[str] = "centaur-pdf/0.1"
DEFAULT_MAX_BYTES: Final[int] = 50 * 1024 * 1024
DEFAULT_TIMEOUT_S: Final[float] = 60.0

# Every well-formed PDF starts with this byte sequence (file format
# version header). PDF/A, encrypted PDFs, linearized PDFs — all start
# with ``%PDF``. The check catches paywall HTML, JS-redirect landing
# pages, and 200-status error bodies disguised as PDFs.
_PDF_MAGIC: Final[bytes] = b"%PDF"


class PdfFetchError(RuntimeError):
    """Non-recoverable PDF fetch failure. Subclasses carry structured detail.

    Subclasses set a class-level ``reason`` string that the client
    envelope copies into the agent-facing ``reason`` field. Catching
    the base class is fine for "fetch failed, doesn't matter why";
    catch a subclass when you want to branch on a specific failure.
    """

    reason: ClassVar[str] = "unknown"


class PdfHttpError(PdfFetchError):
    """Server returned a 4xx/5xx HTTP status."""

    reason: ClassVar[str] = "http_error"

    def __init__(self, url: str, status_code: int, body_snippet: bytes) -> None:
        self.url = url
        self.status_code = status_code
        self.body_snippet = body_snippet
        super().__init__(f"PDF fetch HTTP {status_code} for {url}: {body_snippet!r}")


class PdfNetworkError(PdfFetchError):
    """Network-level failure: DNS, TCP, TLS, timeout, redirect loop."""

    reason: ClassVar[str] = "network_error"

    def __init__(self, url: str, original: Exception) -> None:
        self.url = url
        self.original = original
        super().__init__(f"PDF fetch network error for {url}: {original}")


class PdfTooLargeError(PdfFetchError):
    """Response body exceeded ``max_bytes`` before we stopped reading."""

    reason: ClassVar[str] = "too_large"

    def __init__(self, url: str, max_bytes: int, received_bytes: int) -> None:
        self.url = url
        self.max_bytes = max_bytes
        self.received_bytes = received_bytes
        super().__init__(f"PDF at {url} exceeded max_bytes={max_bytes} (read >= {received_bytes})")


class PdfNotPdfError(PdfFetchError):
    """Response body did not start with the ``%PDF`` magic number.

    Almost always means the server returned HTML — a paywall page, a
    JS-redirect landing page, or a 200-status error body. The agent's
    next step is usually "ask the operator to attach the PDF and
    re-enter via :meth:`PdfClient.parse_file`".
    """

    reason: ClassVar[str] = "not_a_pdf"

    def __init__(self, url: str, mime_type: str | None, body_snippet: bytes) -> None:
        self.url = url
        self.mime_type = mime_type
        self.body_snippet = body_snippet
        super().__init__(
            f"Response from {url} is not a PDF "
            f"(content-type={mime_type!r}, head={body_snippet[:50]!r})"
        )


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
            elsevier) gate on UA — only override if you know what
            you're doing.
        transport: For tests: pass an ``httpx.MockTransport``.
            Production callers omit.

    Returns:
        ``(body_bytes, mime_type)``.

    Raises:
        PdfHttpError: Server returned 4xx/5xx.
        PdfNetworkError: Network-level failure.
        PdfTooLargeError: Body exceeded ``max_bytes`` mid-stream.
        PdfNotPdfError: Body did not start with the ``%PDF`` magic.
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
                raise PdfHttpError(url, response.status_code, body_snippet)

            buffer = bytearray()
            total = 0
            for chunk in response.iter_bytes():
                total += len(chunk)
                if total > max_bytes:
                    raise PdfTooLargeError(url, max_bytes, total)
                buffer.extend(chunk)

            content_type = response.headers.get("content-type")
    except httpx.RequestError as exc:
        raise PdfNetworkError(url, exc) from exc

    # Magic-number check: arbitrary 2xx responses with HTML bodies
    # disguised as PDFs are the single most common "this is not a real
    # PDF" failure mode in academia (paywall walls, JS-redirect landing
    # pages). The check is cheap and catches all of them.
    if not buffer.startswith(_PDF_MAGIC):
        raise PdfNotPdfError(url, content_type, bytes(buffer[:200]))

    return bytes(buffer), force_pdf_mime(url, content_type)
