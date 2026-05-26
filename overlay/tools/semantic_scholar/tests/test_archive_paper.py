"""Unit tests for ``SemanticScholarClient.archive_paper``.

The method is now pure with respect to Postgres — it fetches metadata,
streams the PDF, parses it, and returns a bundle of three DB-row dicts
(``paper_doc``, ``fulltext_doc``, ``archive_row``). No asyncpg, no
pool. The workflow handler in ``overlay/workflows/archive_papers.py``
owns persistence; this suite locks the bundle contract.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from pdf.fetch.http import (
    PdfHttpError,
    PdfNetworkError,
    PdfNotPdfError,
    PdfTooLargeError,
)
from pdf.parse.markdown import PdfInsufficientTextError, PdfParseError
from semanticscholar.Paper import Paper

import semantic_scholar.client as client_module
from semantic_scholar.client import SemanticScholarClient


def _paper(
    paper_id: str = "abc123",
    *,
    pdf_url: str | None = "https://example.com/paper.pdf",
) -> Paper:
    """Minimal S2-shaped paper sufficient for the archive bundle."""
    return Paper(
        {
            "paperId": paper_id,
            "title": "Sample Paper",
            "authors": [{"authorId": "a1", "name": "Test Author"}],
            "year": 2024,
            "abstract": "Sample abstract.",
            "citationCount": 1,
            "url": f"https://www.semanticscholar.org/paper/{paper_id}",
            "openAccessPdf": {"url": pdf_url} if pdf_url else None,
            "venue": "Test Venue",
            "externalIds": {"DOI": f"10.0/{paper_id}"},
        }
    )


def _run(coro):
    return asyncio.run(coro)


def _client() -> SemanticScholarClient:
    return SemanticScholarClient(api_key="")


def _install_get_paper(
    monkeypatch: pytest.MonkeyPatch,
    paper: Paper | None = None,
    *,
    exc: BaseException | None = None,
) -> list[str]:
    """Patch ``SemanticScholarClient.get_paper`` to return ``paper`` (or raise)."""
    calls: list[str] = []

    def _get_paper(self, paper_id, fields=None):  # type: ignore[no-untyped-def]
        calls.append(paper_id)
        if exc is not None:
            raise exc
        assert paper is not None
        return paper

    monkeypatch.setattr(SemanticScholarClient, "get_paper", _get_paper, raising=True)
    return calls


def _install_download(
    monkeypatch: pytest.MonkeyPatch,
    *,
    data: bytes = b"%PDF-1.4 fake body",
    mime: str = "application/pdf",
    exc: BaseException | None = None,
) -> list[tuple[str, dict[str, Any]]]:
    """Patch the symbol the client module uses for ``download_pdf``."""
    calls: list[tuple[str, dict[str, Any]]] = []

    def _download(url, **kwargs):  # type: ignore[no-untyped-def]
        calls.append((url, kwargs))
        if exc is not None:
            raise exc
        return data, mime

    monkeypatch.setattr(client_module, "download_pdf", _download)
    return calls


def _install_parse(
    monkeypatch: pytest.MonkeyPatch,
    *,
    text: str = "# Sample\n\nParsed body content with enough text.",
    parser: str = "pymupdf4llm",
    exc: BaseException | None = None,
) -> list[tuple[bytes, dict[str, Any]]]:
    """Patch the symbol the client module uses for ``parse_pdf``."""
    calls: list[tuple[bytes, dict[str, Any]]] = []

    def _parse(data, **kwargs):  # type: ignore[no-untyped-def]
        calls.append((data, kwargs))
        if exc is not None:
            raise exc
        return text, parser

    monkeypatch.setattr(client_module, "parse_pdf", _parse)
    return calls


def test_archive_paper_empty_id_returns_error() -> None:
    result = _run(_client().archive_paper("   "))

    assert result["status"] == "error"
    assert result["stage"] == "metadata"
    assert result["reason"] == "empty_paper_id"


def test_archive_paper_get_paper_failure_returns_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_get_paper(monkeypatch, exc=RuntimeError("S2 down"))

    result = _run(_client().archive_paper("abc"))

    assert result == {
        "status": "error",
        "paper_id": "abc",
        "stage": "metadata",
        "reason": "fetch_failed",
        "error": "S2 down",
    }


def test_archive_paper_no_pdf_url_returns_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    """Papers without openAccessPdf or ArXiv id skip with a structured reason."""
    _install_get_paper(monkeypatch, _paper("no-pdf", pdf_url=None))
    download_calls = _install_download(monkeypatch)

    result = _run(_client().archive_paper("no-pdf"))

    assert result == {"status": "skipped", "paper_id": "no-pdf", "reason": "no_pdf_url"}
    assert download_calls == [], "download must not run when no PDF URL"


def test_archive_paper_too_large_returns_skipped_with_byte_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_get_paper(monkeypatch, _paper())
    _install_download(
        monkeypatch,
        exc=PdfTooLargeError("https://x/y.pdf", 1024, 2048),
    )

    result = _run(_client().archive_paper("abc123"))

    assert result["status"] == "skipped"
    assert result["reason"] == "too_large"
    assert result["max_bytes"] == 1024
    assert result["received_bytes"] == 2048
    assert result["source_url"]


def test_archive_paper_http_error_returns_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_get_paper(monkeypatch, _paper())
    _install_download(
        monkeypatch,
        exc=PdfHttpError("https://x/y.pdf", 503, b"Service Unavailable"),
    )

    result = _run(_client().archive_paper("abc123"))

    assert result["status"] == "error"
    assert result["stage"] == "fetch"
    assert result["reason"] == "http_error"


def test_archive_paper_network_error_returns_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_get_paper(monkeypatch, _paper())
    _install_download(
        monkeypatch,
        exc=PdfNetworkError("https://x/y.pdf", RuntimeError("DNS down")),
    )

    result = _run(_client().archive_paper("abc123"))

    assert result["status"] == "error"
    assert result["stage"] == "fetch"
    assert result["reason"] == "network_error"


def test_archive_paper_not_a_pdf_returns_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Paywall HTML disguised as a PDF surfaces as ``reason='not_a_pdf'``."""
    _install_get_paper(monkeypatch, _paper())
    _install_download(
        monkeypatch,
        exc=PdfNotPdfError("https://x/y.pdf", "text/html", b"<html>"),
    )

    result = _run(_client().archive_paper("abc123"))

    assert result["status"] == "error"
    assert result["stage"] == "fetch"
    assert result["reason"] == "not_a_pdf"


def test_archive_paper_parse_failure_returns_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_get_paper(monkeypatch, _paper())
    _install_download(monkeypatch)
    _install_parse(monkeypatch, exc=PdfParseError("all tiers failed"))

    result = _run(_client().archive_paper("abc123"))

    assert result["status"] == "error"
    assert result["stage"] == "parse"
    assert result["reason"] == "all_backends_failed"


def test_archive_paper_insufficient_text_returns_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """``PdfInsufficientTextError`` reports the OCR-routing reason code."""
    _install_get_paper(monkeypatch, _paper())
    _install_download(monkeypatch)
    _install_parse(monkeypatch, exc=PdfInsufficientTextError("scanned PDF"))

    result = _run(_client().archive_paper("abc123"))

    assert result["status"] == "error"
    assert result["stage"] == "parse"
    assert result["reason"] == "insufficient_text"


def test_archive_paper_explicit_source_url_overrides_derived(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``source_url`` overrides the openAccessPdf-derived URL."""
    paper = _paper("abc123", pdf_url=None)  # would otherwise return no_pdf_url
    _install_get_paper(monkeypatch, paper)
    download_calls = _install_download(monkeypatch)
    _install_parse(monkeypatch)

    result = _run(
        _client().archive_paper("abc123", source_url="https://override.example/x.pdf"),
    )

    assert result["status"] == "ok"
    assert result["source_url"] == "https://override.example/x.pdf"
    assert download_calls[0][0] == "https://override.example/x.pdf"


def test_archive_paper_happy_path_returns_three_projection_dicts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bundle contract: paper_doc + fulltext_doc + archive_row, all keyed off paper_id."""
    paper = _paper("abc123")
    _install_get_paper(monkeypatch, paper)
    pdf_bytes = b"%PDF-1.4 fake body"
    _install_download(monkeypatch, data=pdf_bytes, mime="application/pdf")
    _install_parse(monkeypatch, text="# Sample paper body" * 10, parser="pymupdf4llm")

    result = _run(_client().archive_paper("abc123"))

    assert result["status"] == "ok"
    assert result["paper_id"] == "abc123"
    assert result["size_bytes"] == len(pdf_bytes)
    assert result["mime_type"] == "application/pdf"
    assert result["parser_used"] == "pymupdf4llm"
    assert result["pdf_sha256"]
    assert result["source_url"] == "https://example.com/paper.pdf"

    paper_doc = result["paper_doc"]
    assert paper_doc["document_id"] == "semantic_scholar:paper:abc123"
    assert paper_doc["source_type"] == "paper"

    fulltext_doc = result["fulltext_doc"]
    assert fulltext_doc["document_id"] == "semantic_scholar:paper_fulltext:abc123"
    assert fulltext_doc["source_type"] == "paper_fulltext"
    assert fulltext_doc["parent_document_id"] == "semantic_scholar:paper:abc123"

    archive_row = result["archive_row"]
    assert archive_row["paper_id"] == "abc123"
    assert archive_row["pdf_bytes"] == pdf_bytes
    assert archive_row["pdf_sha256"] == result["pdf_sha256"]
    assert archive_row["mime_type"] == "application/pdf"


def test_archive_paper_does_not_touch_asyncpg(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sentinel: bundle method must never reach for asyncpg.

    If a future refactor re-introduces ``asyncpg.connect`` inside
    ``archive_paper``, this fails loudly. Mirrors the same sentinel for
    ``research_brief``.
    """
    import asyncpg

    def _explode(*_args, **_kwargs):
        raise AssertionError("archive_paper must not call asyncpg.connect")

    monkeypatch.setattr(asyncpg, "connect", _explode)
    _install_get_paper(monkeypatch, _paper())
    _install_download(monkeypatch)
    _install_parse(monkeypatch)

    result = _run(_client().archive_paper("abc123"))

    assert result["status"] == "ok"
