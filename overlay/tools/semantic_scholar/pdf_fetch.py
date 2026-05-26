"""Stream PDF downloads from arbitrary publisher hosts with a hard byte cap.

Kept separate from :mod:`semantic_scholar.client` because PDF hosts
have nothing to do with the Graph API: they need a different
``User-Agent`` (arxiv.org and several publishers gate on UA), use a
different redirect policy, and must never see our Graph API key.

We stream the body chunk-by-chunk and stop the moment ``total_bytes``
crosses ``max_bytes`` — a runaway paywall page, HTML 404 body, or
malicious gigabyte response can't OOM the API pod.
"""

from __future__ import annotations

import logging
from typing import Final

import httpx
from semanticscholar.Paper import Paper

log = logging.getLogger(__name__)

DEFAULT_USER_AGENT: Final[str] = "centaur-scientist/0.1 (paper-archive)"
DEFAULT_MAX_BYTES: Final[int] = 50 * 1024 * 1024  # 50 MiB
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
    """Stream a PDF from ``url`` into memory with a size cap.

    Returns ``(body_bytes, mime_type)``. ``mime_type`` is taken from
    the response ``Content-Type`` header (stripped of parameters), but
    is forced to ``"application/pdf"`` when the URL path ends in
    ``.pdf`` — many academic hosts mislabel PDFs as
    ``application/octet-stream``.

    The ``transport`` kwarg is for tests (pass an ``httpx.MockTransport``);
    production callers should omit it.

    Raises :class:`PdfFetchError` on any HTTP / network failure, and
    :class:`PdfTooLargeError` (a subclass) if the body exceeds
    ``max_bytes``.
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

            mime = (
                response.headers.get("content-type", "application/octet-stream")
                .split(";")[0]
                .strip()
            )
    except httpx.RequestError as exc:
        raise PdfFetchError(f"PDF fetch network error for {url}: {exc}") from exc

    # Many academic hosts serve PDFs with `application/octet-stream` or
    # an HTML wrapper Content-Type; trust the URL extension instead so
    # downstream sniffing doesn't have to special-case each publisher.
    path = url.split("?", 1)[0].lower()
    if path.endswith(".pdf"):
        mime = "application/pdf"

    return bytes(buffer), mime


def derive_pdf_url(paper: Paper) -> str | None:
    """Pick the best PDF URL for a Semantic Scholar :class:`Paper`, or ``None``.

    Preference order:

    1. ``openAccessPdf["url"]`` (when it's a non-empty stripped string).
    2. ``https://arxiv.org/pdf/{externalIds.ArXiv}.pdf`` (when an
       ArXiv ID is present and non-empty).

    Returns ``None`` when neither field is usable.

    The upstream ``semanticscholar`` library exposes ``openAccessPdf``
    and ``externalIds`` as plain dicts and returns ``None`` (not
    ``{}``) when the API response omitted the field — every access
    below normalises that.
    """
    open_access_pdf = paper.openAccessPdf or {}
    open_access_url = open_access_pdf.get("url")
    if open_access_url:
        stripped = str(open_access_url).strip()
        if stripped:
            return stripped

    external_ids = paper.externalIds or {}
    arxiv_id = external_ids.get("ArXiv")
    if arxiv_id:
        stripped = str(arxiv_id).strip()
        if stripped:
            return f"https://arxiv.org/pdf/{stripped}.pdf"

    return None
