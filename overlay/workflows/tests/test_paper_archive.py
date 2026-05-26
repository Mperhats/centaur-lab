"""Unit tests for ``centaur_lab.paper_archive.archive_paper_to_pool``.

The pipeline isn't exercised end-to-end by the workflow or tool unit
suites — those mock at the handler/method boundary — so without these
tests typo-level regressions in the helper (e.g. passing a stale kwarg
to ``build_fulltext_document``) only surface in integration runs. The
mocks below cover the four interesting branches: completed, noop,
skipped, error.
"""

from __future__ import annotations

from typing import Any

import pytest
from semanticscholar.Paper import Paper

from centaur_lab import paper_archive
from centaur_lab.paper_document import _content_hash


def _paper(
    paper_id: str = "abc123",
    *,
    pdf_url: str | None = "https://example.com/paper.pdf",
) -> Paper:
    """Minimal S2-shaped paper sufficient for the archive helpers."""
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


class _FakeClient:
    """Stand-in exposing only ``get_paper`` — what the helper actually calls."""

    def __init__(self, paper: Paper | None = None, exc: BaseException | None = None) -> None:
        self._paper = paper
        self._exc = exc
        self.calls: list[str] = []

    def get_paper(self, paper_id: str) -> Paper:
        self.calls.append(paper_id)
        if self._exc is not None:
            raise self._exc
        assert self._paper is not None
        return self._paper


class _SequencedPool:
    """Pool mock with per-call fetchval results so the three sequential
    SELECTs (archive lookup → paper content_hash → fulltext content_hash
    → archive lookup again) can each return a different value.
    """

    def __init__(self, fetchval_returns: list[Any], execute_status: str = "INSERT 0 1") -> None:
        self._fetchval_returns = list(fetchval_returns)
        self._execute_status = execute_status
        self.fetchval_calls: list[tuple[str, tuple[Any, ...]]] = []
        self.execute_calls: list[tuple[str, tuple[Any, ...]]] = []

    async def fetchval(self, query: str, *args: Any) -> Any:
        self.fetchval_calls.append((query, args))
        if not self._fetchval_returns:
            return None
        return self._fetchval_returns.pop(0)

    async def execute(self, query: str, *args: Any) -> str:
        self.execute_calls.append((query, args))
        return self._execute_status


def _patch_pdf_pipeline(
    monkeypatch: pytest.MonkeyPatch,
    *,
    pdf_bytes: bytes = b"%PDF-1.4 fake body",
    mime: str = "application/pdf",
    parsed_text: str = "# Sample paper\n\nParsed body content.",
    parser_used: str = "pymupdf4llm",
    download_exc: BaseException | None = None,
    parse_exc: BaseException | None = None,
) -> dict[str, list[Any]]:
    """Replace the PDF I/O so tests don't touch the network or PyMuPDF."""
    calls: dict[str, list[Any]] = {"download": [], "parse": []}

    def _download(url: str, **kwargs: Any) -> tuple[bytes, str]:
        calls["download"].append((url, kwargs))
        if download_exc is not None:
            raise download_exc
        return pdf_bytes, mime

    def _parse(data: bytes, min_size: int) -> tuple[str, str]:
        calls["parse"].append((data, min_size))
        if parse_exc is not None:
            raise parse_exc
        return parsed_text, parser_used

    monkeypatch.setattr(paper_archive.pdf_fetch, "download_pdf", _download)
    monkeypatch.setattr(paper_archive.pdf_parse, "parse_pdf_to_markdown", _parse)
    return calls


@pytest.mark.asyncio
async def test_returns_error_when_paper_id_empty() -> None:
    """Whitespace-only ids bail before any client/db work."""
    client = _FakeClient()
    pool = _SequencedPool([])

    result = await paper_archive.archive_paper_to_pool(client, pool, "   ")

    assert result == {
        "status": "error",
        "paper_id": "   ",
        "error": "paper_id cannot be empty",
    }
    assert client.calls == []
    assert pool.fetchval_calls == []
    assert pool.execute_calls == []


@pytest.mark.asyncio
async def test_returns_error_when_get_paper_raises() -> None:
    """RuntimeError from the SDK surfaces as an envelope, not a raised exc."""
    client = _FakeClient(exc=RuntimeError("S2 down"))
    pool = _SequencedPool([])

    result = await paper_archive.archive_paper_to_pool(client, pool, "abc123")

    assert result == {"status": "error", "paper_id": "abc123", "error": "S2 down"}
    assert pool.fetchval_calls == []
    assert pool.execute_calls == []


@pytest.mark.asyncio
async def test_returns_skipped_when_no_pdf_url() -> None:
    """Papers without openAccessPdf or ArXiv id skip with a structured reason."""
    paper = Paper(
        {
            "paperId": "no-pdf",
            "title": "Closed-access",
            "openAccessPdf": None,
            "externalIds": {"DOI": "10.0/no-pdf"},
        }
    )
    client = _FakeClient(paper=paper)
    pool = _SequencedPool([])

    result = await paper_archive.archive_paper_to_pool(client, pool, "no-pdf")

    assert result == {
        "status": "skipped",
        "paper_id": "no-pdf",
        "reason": "no_pdf_url",
    }
    assert pool.fetchval_calls == []


@pytest.mark.asyncio
async def test_explicit_source_url_overrides_derived_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``source_url`` short-circuits ``derive_pdf_url`` (used by the workflow's overrides)."""
    paper = _paper("abc123", pdf_url=None)  # would otherwise return no_pdf_url
    client = _FakeClient(paper=paper)
    pool = _SequencedPool(fetchval_returns=[None, None, None])
    download_calls = _patch_pdf_pipeline(monkeypatch)["download"]

    result = await paper_archive.archive_paper_to_pool(
        client,
        pool,
        "abc123",
        source_url="https://override.example/x.pdf",
    )

    assert result["status"] == "completed"
    assert result["source_url"] == "https://override.example/x.pdf"
    assert download_calls[0][0] == "https://override.example/x.pdf"


@pytest.mark.asyncio
async def test_returns_skipped_when_pdf_too_large(monkeypatch: pytest.MonkeyPatch) -> None:
    """``PdfTooLargeError`` produces a structured skip; no parse, no writes."""
    client = _FakeClient(paper=_paper())
    pool = _SequencedPool([])
    _patch_pdf_pipeline(
        monkeypatch,
        download_exc=paper_archive.pdf_fetch.PdfTooLargeError(">50MiB"),
    )

    result = await paper_archive.archive_paper_to_pool(client, pool, "abc123")

    assert result["status"] == "skipped"
    assert result["reason"] == "too_large"
    assert pool.fetchval_calls == []
    assert pool.execute_calls == []


@pytest.mark.asyncio
async def test_returns_error_on_pdf_fetch_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """``PdfFetchError`` (non-too-large) lands as an error envelope."""
    client = _FakeClient(paper=_paper())
    pool = _SequencedPool([])
    _patch_pdf_pipeline(
        monkeypatch,
        download_exc=paper_archive.pdf_fetch.PdfFetchError("HTTP 503"),
    )

    result = await paper_archive.archive_paper_to_pool(client, pool, "abc123")

    assert result["status"] == "error"
    assert "HTTP 503" in result["error"]
    assert pool.execute_calls == []


@pytest.mark.asyncio
async def test_returns_noop_when_archive_hash_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same-sha PDF short-circuits before parsing — re-runs are cheap."""
    pdf_bytes = b"%PDF-1.4 fixed"
    existing_sha = paper_archive.compute_pdf_sha256(pdf_bytes)
    client = _FakeClient(paper=_paper())
    pool = _SequencedPool(fetchval_returns=[existing_sha])
    parse_calls = _patch_pdf_pipeline(monkeypatch, pdf_bytes=pdf_bytes)["parse"]

    result = await paper_archive.archive_paper_to_pool(client, pool, "abc123")

    assert result["status"] == "noop"
    assert result["archive_action"] == "noop"
    assert result["pdf_sha256"] == existing_sha
    assert parse_calls == [], "noop must not parse"
    assert pool.execute_calls == [], "noop must not write"


@pytest.mark.asyncio
async def test_returns_error_on_parse_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """``PdfParseError`` lands as an error envelope; nothing persisted."""
    client = _FakeClient(paper=_paper())
    pool = _SequencedPool(fetchval_returns=[None])
    _patch_pdf_pipeline(
        monkeypatch,
        parse_exc=paper_archive.pdf_parse.PdfParseError("all tiers failed"),
    )

    result = await paper_archive.archive_paper_to_pool(client, pool, "abc123")

    assert result["status"] == "error"
    assert "all tiers failed" in result["error"]
    assert pool.execute_calls == []


@pytest.mark.asyncio
async def test_completed_writes_three_rows_with_parent_link(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Happy path: persists paper doc → fulltext doc → archive in order.

    Also guards against the historical regression where
    ``build_fulltext_document`` was called with a stale ``parent_document_id``
    kwarg — the call would TypeError at runtime, so this asserting that
    completion is reached at all covers the bug.
    """
    client = _FakeClient(paper=_paper("abc123"))
    pool = _SequencedPool(fetchval_returns=[None, None, None])
    _patch_pdf_pipeline(monkeypatch)

    result = await paper_archive.archive_paper_to_pool(client, pool, "abc123")

    assert result["status"] == "completed"
    assert result["paper_id"] == "abc123"
    assert result["paper_action"] == "inserted"
    assert result["fulltext_action"] == "inserted"
    assert result["archive_action"] == "inserted"
    assert result["paper_document_id"] == "semantic_scholar:paper:abc123"
    assert result["fulltext_document_id"] == "semantic_scholar:paper_fulltext:abc123"
    assert result["parser_used"] == "pymupdf4llm"
    assert result["size_bytes"] > 0

    # Three writes, in pipeline order: paper doc, fulltext doc, archive.
    assert len(pool.execute_calls) == 3
    paper_sql, _paper_args = pool.execute_calls[0]
    fulltext_sql, fulltext_args = pool.execute_calls[1]
    archive_sql, _ = pool.execute_calls[2]
    assert "INTO company_context_documents" in paper_sql
    assert "INTO company_context_documents" in fulltext_sql
    assert "INTO paper_archives" in archive_sql

    # Fulltext row's parent column ($6) points at the metadata row's
    # document_id — the linkage check that catches a future drift to a
    # non-derived parent id.
    assert fulltext_args[5] == "semantic_scholar:paper:abc123"


@pytest.mark.asyncio
async def test_completed_skips_fulltext_upsert_when_content_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Re-parenting / content-changed semantics still let the archive write through.

    When the metadata doc's compound hash already matches (a previously
    saved paper), ``upsert_document`` returns noop on the metadata row,
    but the archive's pdf_sha256 changed (re-fetched/re-parsed body) so
    the archive row still gets written.
    """
    paper = _paper("abc123")
    from centaur_lab.paper_document import build_paper_document

    paper_doc = build_paper_document(paper)
    # The persisted hash combines intrinsic + parent (= None here).
    existing_doc_hash = _content_hash(paper_doc["content_hash"], None)

    client = _FakeClient(paper=paper)
    # fetchval sequence: archive lookup → paper doc → fulltext doc → archive lookup again.
    pool = _SequencedPool(
        fetchval_returns=[None, existing_doc_hash, None, None],
        execute_status="INSERT 0 1",
    )
    _patch_pdf_pipeline(monkeypatch)

    result = await paper_archive.archive_paper_to_pool(client, pool, "abc123")

    assert result["status"] == "completed"
    assert result["paper_action"] == "noop"
    assert result["fulltext_action"] == "inserted"
    assert result["archive_action"] == "inserted"
    # The metadata row's UPSERT is skipped, but fulltext + archive both execute.
    assert len(pool.execute_calls) == 2
