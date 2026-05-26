# Semantic Scholar Paper Archive Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the overlay `semantic_scholar` tool with a paper-archival pipeline that downloads PDFs from `openAccessPdf.url`, parses them to Markdown via `pymupdf4llm`/`pymupdf`/`pypdf` (3-tier fallback), persists raw bytes in a new `paper_archives` overlay table, and writes the parsed full-text as a `source_type="paper_fulltext"` row in `company_context_documents` linked to the existing paper metadata row.

**Architecture:** Additive — no breaking changes to the existing S2 client, `save_papers`, or `research_brief`. New work splits into pure I/O-free modules (parse) + thin HTTP module (fetch) + composition method on `SemanticScholarClient.archive_paper` + new `archive_papers` batch workflow + one migration. Pattern mirrors Centaur's `.centaur/tools/research/archiver/` (`fetch` → `parse` → orchestrator returns `{status, files, attachments, ...}`) and AI-Scientist-v2's `load_paper()` fallback chain (`pymupdf4llm.to_markdown` → `pymupdf.get_text` → `pypdf.extract_text`, with a 100-char min-size guard between tiers).

**Tech Stack:** Python 3.11, `httpx` (already in deps), `asyncpg` (already in deps), `pymupdf4llm` (new), `pymupdf` (new), `pypdf` (new). Centaur conventions: `secret()` for auth, `ctx._pool` for DB, `dict[str, Any]` payloads (no Pydantic), idempotent `content_hash`-based upsert.

**Branch:** Continue on the current feature branch (or cut `feat/s2-paper-archive`). Each task ends with a single commit.

---

## Research synthesis (from three parallel readonly research subagents, 2026-05-26)

### AI-Scientist-v2 (`.scientist/`) — `load_paper()` fallback chain

Upstream uses S2 **only** for metadata + BibTeX. PDF parsing applies to self-generated papers via `ai_scientist/perform_llm_review.py:257-288`:

```python
def load_paper(pdf_path, num_pages=None, min_size=100):
    try:
        text = pymupdf4llm.to_markdown(pdf_path)
        if len(text) < min_size:
            raise Exception("Text too short")
    except Exception:
        try:
            doc = pymupdf.open(pdf_path)
            text = "".join(page.get_text() for page in doc)
            if len(text) < min_size:
                raise Exception("Text too short")
        except Exception:
            reader = PdfReader(pdf_path)
            text = "".join(page.extract_text() for page in reader.pages)
            if len(text) < min_size:
                raise Exception("Text too short")
    return text
```

Conventions adopted from this code:
- 3-tier parser fallback (`pymupdf4llm` → `pymupdf` → `pypdf`)
- 100-char `min_size` guard between tiers
- Markdown output (via `pymupdf4llm`) — best for LLM consumption
- Cache `.txt` next to `.pdf` (we replace with DB row; same effect)
- Deps `pymupdf4llm`, `pymupdf`, `pypdf` (already on `.scientist/requirements.txt:7-8`)

### Centaur archiver (`.centaur/tools/research/archiver/`) — orchestration pattern

The canonical archive tool's shape we mimic structurally:
- `client.py` — public `ArchiverClient` + `_client()` factory; every public method auto-registers as a REST endpoint
- `download/` subpackage — source-specific fetch logic
- `ingest/parse.py` — extraction adapter
- `utils.py` — `FileRecord` dataclass + hashing
- Return-shape convention: `{"status": "ok"|"error"|"skipped", "files": [...], "attachments": [...], ...}`
- `pyproject.toml` `[tool.centaur] module = "client.py"` + typed secret declarations
- Tests use `unittest` + `unittest.mock.patch` to stub HTTP/SDK calls

**Departure from archiver:** archiver uses `save_attachment_from_path` (thread-scoped Centaur attachments table, requires `_tool_ctx.thread_key`). That works inside a tool call but not from a workflow handler. We store raw PDF bytes in a new **overlay-owned `paper_archives` table** so both tool methods and workflows can persist without thread context.

### Existing overlay (`overlay/tools/semantic_scholar/`) — extension points

Today's flow stops at metadata: `SemanticScholarClient.get_paper(paper_id)` → `build_paper_document(paper)` → `upsert_document(pool, doc)` writes `source_type="paper"` to `company_context_documents` with `body=<markdown header + abstract only>`. The S2 paper dict already carries `openAccessPdf={"url": ..., "status": "GREEN"}` (in `DEFAULT_PAPER_FIELDS`, `client.py:19`), and `metadata.openAccessPdf` is persisted as the URL string but never fetched.

Style conventions to honor:
- Untyped `dict[str, Any]` for S2 payloads; full type hints on every signature
- Lazy `secret()` resolution at request time (constructor stores `None`/empty, resolves per-call) — see `client.py:95-103`
- High-level agent methods (`search`, `research_brief`) return `{status, ...}` and never raise; low-level (`search_papers`, `get_paper`) raise `ValueError`/`RuntimeError`
- Persistence in `overlay/centaur_lab/` (NOT `shared.*` — that namespace is reserved upstream); workflows/tools import from `centaur_lab.*`
- Idempotency via `content_hash` over canonical JSON; `upsert_document` returns `inserted`/`updated`/`noop`
- Tests: `monkeypatch.setattr(SemanticScholarClient, "...", stub)` for HTTP; `MockPool` from `overlay/workflows/tests/_mocks.py` for DB unit tests; real Postgres + mocked HTTP for integration

---

## Spec decisions (locked in)

1. **No raw bytes in Centaur `attachments` table.** That requires `_tool_ctx.thread_key`, which workflows don't have. Use a new overlay table `paper_archives` keyed by `paper_id`.
2. **Two-row pattern in `company_context_documents`.** The existing metadata row (`source_type="paper"`, `parent_document_id=NULL`-or-brief) stays unchanged. A NEW row (`source_type="paper_fulltext"`, `parent_document_id=<paper metadata row's document_id>`) holds the parsed Markdown. Rationale: keeps the existing paper row's `content_hash` stable so `save_papers` doesn't re-update on every archive; lets BM25 index full-text separately; matches the chunked-rows pattern used by Slack upstream.
3. **Source URL priority:** `openAccessPdf.url` first. If absent and `externalIds.ArXiv` present, fall back to `https://arxiv.org/pdf/{arxivId}.pdf`. Otherwise return `status="skipped"`, `reason="no_pdf_url"`. No DOI resolver in v1.
4. **Size cap:** 50 MB hard limit on PDF download (configurable via constructor / env). Larger papers return `status="skipped"`, `reason="too_large"`.
5. **Idempotency:** `paper_archives.pdf_sha256` is the content hash. If the byte-identical PDF already exists for this `paper_id`, skip re-parse and reuse stored `parsed_text` — return `status="noop"`. Parse-only re-runs are out of scope for v1 (callers can re-invoke after manually deleting the row).
6. **No new agent-facing secret.** Reuses existing `SEMANTIC_SCHOLAR_API_KEY` and existing httpx infrastructure. PDF fetch is anonymous (no S2 auth header on `arxiv.org` / `*.semanticscholar.org` PDF URLs).
7. **HTTP redirects:** follow up to 5 (`httpx` default). User-Agent: `centaur-scientist/0.1 (+https://github.com/...)` so paywall hosts can identify the bot if needed.
8. **Parser fallback ordering:** `pymupdf4llm.to_markdown(bytes)` → `pymupdf.open(stream=bytes).get_text()` → `pypdf.PdfReader(BytesIO(bytes)).extract_text()`. 100-char min-size between tiers. Final fallback failure returns `status="error"` with the last exception's message.
9. **Parsed-text cap:** truncate `body` to 1 MiB UTF-8 (BM25 index efficiency); record `truncated=True` in `metadata`.

---

## File structure

All paths absolute from repo root. Submodule paths (`.centaur/`, `.scientist/`) are never edited.

| File | Status | Responsibility |
|------|--------|----------------|
| `overlay/services/api/db/migrations/20260526000001_add_paper_archives.sql` | **Create** | `paper_archives` table (raw PDF bytes + parsed text + metadata, keyed by `paper_id`) |
| `overlay/tools/semantic_scholar/pdf_parse.py` | **Create** | `parse_pdf_to_markdown(data: bytes, min_size: int = 100) -> tuple[str, str]` — pure function, 3-tier fallback, returns `(markdown, parser_used)` |
| `overlay/tools/semantic_scholar/pdf_fetch.py` | **Create** | `download_pdf(url: str, *, timeout: float, max_bytes: int, user_agent: str) -> tuple[bytes, str]` — streaming httpx download with size cap, returns `(bytes, mime_type)` |
| `overlay/centaur_lab/paper_fulltext.py` | **Create** | `build_fulltext_document(paper, parsed_text, *, parent_document_id, parser_used, truncated)` mirrors `build_paper_document` for `source_type="paper_fulltext"` rows; `_upsert_paper_archive(pool, row)` for the raw-bytes table |
| `overlay/tools/semantic_scholar/client.py` | **Modify** | Add `MAX_PDF_BYTES`, `PDF_USER_AGENT`, `ARCHIVE_PARSER_MIN_SIZE`, `FULLTEXT_BODY_MAX_BYTES` constants; add `archive_paper(paper_id, *, source_url=None, force=False) -> dict` agent method; add private `_archive_paper_async` for coroutine reuse from the workflow |
| `overlay/tools/semantic_scholar/cli.py` | **Modify** | Add `archive` Typer subcommand for local smokes |
| `overlay/tools/semantic_scholar/pyproject.toml` | **Modify** | Add `pymupdf4llm>=0.0.17`, `pymupdf>=1.24.0`, `pypdf>=4.0.0` to `dependencies`; same to `[dependency-groups].dev` for parity (test deps inherit from main deps anyway) |
| `overlay/tools/semantic_scholar/tests/test_pdf_parse.py` | **Create** | Unit tests for parser fallback chain using fixture PDFs and monkeypatched parsers |
| `overlay/tools/semantic_scholar/tests/test_pdf_fetch.py` | **Create** | Unit tests for `download_pdf` (success, size cap, 404, network error, mime_type sniffing) |
| `overlay/tools/semantic_scholar/tests/test_archive_paper.py` | **Create** | Unit tests for `SemanticScholarClient.archive_paper` (success, no-pdf-url, too-large, idempotent, error envelopes) |
| `overlay/tools/semantic_scholar/tests/integration/test_archive_paper_integration.py` | **Create** | Integration test against real Postgres (gated on `CENTAUR_TEST_DATABASE_URL`) with mocked PDF server |
| `overlay/tools/semantic_scholar/tests/fixtures/sample.pdf` | **Create** | 1-page test PDF (~2 KB) generated by pymupdf at fixture-build time (no checked-in binary if possible) |
| `overlay/workflows/archive_papers.py` | **Create** | Batch workflow: input `paper_ids: list[str]` → for each, call `SemanticScholarClient._archive_paper_async` in a thread; returns aggregated counts + per-paper results |
| `overlay/workflows/tests/test_archive_papers.py` | **Create** | Unit tests for the workflow handler |
| `overlay/workflows/tests/test_paper_fulltext.py` | **Create** | Unit tests for `build_fulltext_document` and `_upsert_paper_archive` against `MockPool` |
| `overlay/workflows/tests/integration/test_archive_papers_integration.py` | **Create** | Integration test for the workflow against real Postgres |
| `overlay/Justfile` | **Modify** | Add `smoke-s2-archive` and `smoke-archive-papers` recipes |
| `overlay/.agents/skills/academic-research/SKILL.md` | **Modify** | Document the new `archive_paper` agent method and `archive_papers` workflow |
| `overlay/Dockerfile` | **No change** | Static-file overlay; tool deps install at API discovery time from each `pyproject.toml`. `pymupdf` ships a manylinux wheel for Python 3.11 so no extra `apk add` needed. |

---

## Task graph

```
A1  parse module (pure)
A2  fetch module (pure HTTP)
A3  paper_fulltext.py (DB helpers, depends on A1+A2 only at type level)
A4  migration 20260526000001_add_paper_archives.sql
A5  SemanticScholarClient.archive_paper (composes A1..A4)
A6  CLI subcommand archive
A7  archive_papers workflow (depends on A5)
A8  integration tests
A9  Justfile + skill doc updates
```

Tasks A1, A2, A4 are independent and can be done in parallel if desired. The rest are sequential.

---

## Task 1: PDF parsing module (`pdf_parse.py`)

**Files:**
- Create: `overlay/tools/semantic_scholar/pdf_parse.py`
- Test: `overlay/tools/semantic_scholar/tests/test_pdf_parse.py`
- Test: `overlay/tools/semantic_scholar/tests/fixtures/__init__.py` (generates sample.pdf at import time, no binary checked in)

### Step 1: Write the failing test

Create `overlay/tools/semantic_scholar/tests/fixtures/__init__.py`:

```python
"""PDF fixture generators (kept in-tree so we never check in binary blobs)."""

from __future__ import annotations

from io import BytesIO


def make_sample_pdf(text: str = "Centaur sample paper.\nSection 1: Hello.\n") -> bytes:
    """Generate a single-page PDF containing the given text via pymupdf."""
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), text, fontsize=12)
    buf = BytesIO()
    doc.save(buf)
    doc.close()
    return buf.getvalue()
```

Create `overlay/tools/semantic_scholar/tests/test_pdf_parse.py`:

```python
"""Tests for the 3-tier PDF parser fallback chain."""

from __future__ import annotations

import pytest

from semantic_scholar import pdf_parse
from semantic_scholar.tests.fixtures import make_sample_pdf


def test_parse_pdf_to_markdown_uses_pymupdf4llm_when_text_present() -> None:
    pdf_bytes = make_sample_pdf("Centaur paper. Method. Results. Conclusion.")
    text, parser_used = pdf_parse.parse_pdf_to_markdown(pdf_bytes)
    assert "Centaur" in text
    assert parser_used == "pymupdf4llm"


def test_parse_pdf_to_markdown_rejects_too_short(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        pdf_parse, "_pymupdf4llm_markdown", lambda data: "x"
    )
    monkeypatch.setattr(
        pdf_parse, "_pymupdf_text", lambda data: "y"
    )
    monkeypatch.setattr(
        pdf_parse, "_pypdf_text", lambda data: "z"
    )
    with pytest.raises(pdf_parse.PdfParseError) as exc:
        pdf_parse.parse_pdf_to_markdown(b"unused", min_size=100)
    assert "all parsers" in str(exc.value).lower()


def test_parse_pdf_to_markdown_falls_back_to_pymupdf(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom_4llm(data: bytes) -> str:
        raise RuntimeError("pymupdf4llm exploded")

    monkeypatch.setattr(pdf_parse, "_pymupdf4llm_markdown", _boom_4llm)
    monkeypatch.setattr(
        pdf_parse,
        "_pymupdf_text",
        lambda data: "Centaur paper " * 50,
    )
    text, parser_used = pdf_parse.parse_pdf_to_markdown(b"unused")
    assert parser_used == "pymupdf"
    assert "Centaur" in text


def test_parse_pdf_to_markdown_falls_back_to_pypdf(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        pdf_parse,
        "_pymupdf4llm_markdown",
        lambda data: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    monkeypatch.setattr(
        pdf_parse,
        "_pymupdf_text",
        lambda data: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    monkeypatch.setattr(
        pdf_parse, "_pypdf_text", lambda data: "Centaur paper " * 50
    )
    text, parser_used = pdf_parse.parse_pdf_to_markdown(b"unused")
    assert parser_used == "pypdf"
    assert "Centaur" in text


def test_parse_pdf_to_markdown_short_then_long_promotes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """pymupdf4llm returned <100 chars; pymupdf returned plenty; we keep pymupdf."""
    monkeypatch.setattr(pdf_parse, "_pymupdf4llm_markdown", lambda data: "tiny")
    monkeypatch.setattr(
        pdf_parse, "_pymupdf_text", lambda data: "Big result " * 100
    )
    text, parser_used = pdf_parse.parse_pdf_to_markdown(b"unused")
    assert parser_used == "pymupdf"
    assert len(text) >= 100
```

### Step 2: Run test to verify it fails

Run: `cd overlay/tools/semantic_scholar && uv run --python 3.11 pytest tests/test_pdf_parse.py -v`

Expected: FAIL — `ModuleNotFoundError: No module named 'semantic_scholar.pdf_parse'`.

### Step 3: Write the parser module

Create `overlay/tools/semantic_scholar/pdf_parse.py`:

```python
"""PDF → Markdown extraction with a 3-tier parser fallback.

Mirrors AI-Scientist-v2's ``load_paper`` (``ai_scientist/perform_llm_review.py:257-288``)
but operates on bytes (not paths) so the fetch layer can stream PDFs into
memory and we never touch local disk in the API pod. Each tier is a
private function so tests can monkeypatch them independently.

Ordering ``pymupdf4llm`` first follows upstream's preference for Markdown
output that preserves headings/lists, which LLMs index better than raw
text dumps. ``pymupdf`` gives reliable plain text when MD extraction
chokes on the layout; ``pypdf`` is the last-resort pure-Python parser.
"""

from __future__ import annotations

import io
import logging

log = logging.getLogger(__name__)

DEFAULT_MIN_SIZE = 100


class PdfParseError(RuntimeError):
    """All PDF parser tiers failed or produced output below ``min_size``."""


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


def parse_pdf_to_markdown(
    data: bytes, min_size: int = DEFAULT_MIN_SIZE
) -> tuple[str, str]:
    """Parse a PDF byte string into Markdown/plain text.

    Returns ``(text, parser_used)`` where ``parser_used`` is one of
    ``"pymupdf4llm"``, ``"pymupdf"``, ``"pypdf"``. Raises
    :class:`PdfParseError` if every tier either errors out or returns
    fewer than ``min_size`` characters.
    """
    last_error: Exception | None = None
    for name, fn in (
        ("pymupdf4llm", _pymupdf4llm_markdown),
        ("pymupdf", _pymupdf_text),
        ("pypdf", _pypdf_text),
    ):
        try:
            text = fn(data)
        except Exception as exc:  # noqa: BLE001 — fallback chain intentionally broad
            last_error = exc
            log.warning("pdf_parse_tier_failed", extra={"parser": name, "error": str(exc)})
            continue
        if text and len(text) >= min_size:
            return text, name
        last_error = PdfParseError(
            f"{name} produced {len(text) if text else 0} chars (< {min_size} min_size)"
        )
        log.info("pdf_parse_tier_too_short", extra={"parser": name, "chars": len(text or "")})

    raise PdfParseError(
        f"all parsers failed or produced < {min_size} chars: {last_error}"
    )
```

### Step 4: Run test to verify it passes

Run: `cd overlay/tools/semantic_scholar && uv sync --group dev --python 3.11 && uv run --python 3.11 pytest tests/test_pdf_parse.py -v`

Expected: 5 PASS.

(If `pymupdf`/`pymupdf4llm` are missing, sync first — Task 6 adds them; for local dev right now run `uv pip install pymupdf pymupdf4llm pypdf` inside the tool dir or jump to Task 6 before this step.)

### Step 5: Commit

```bash
git add overlay/tools/semantic_scholar/pdf_parse.py \
       overlay/tools/semantic_scholar/tests/test_pdf_parse.py \
       overlay/tools/semantic_scholar/tests/fixtures/__init__.py
git commit -m "feat(s2): add PDF→Markdown 3-tier parser fallback (pymupdf4llm/pymupdf/pypdf)"
```

---

## Task 2: PDF fetch module (`pdf_fetch.py`)

**Files:**
- Create: `overlay/tools/semantic_scholar/pdf_fetch.py`
- Test: `overlay/tools/semantic_scholar/tests/test_pdf_fetch.py`

### Step 1: Write the failing test

Create `overlay/tools/semantic_scholar/tests/test_pdf_fetch.py`:

```python
"""Tests for ``pdf_fetch.download_pdf``."""

from __future__ import annotations

import httpx
import pytest

from semantic_scholar import pdf_fetch


def _make_transport(
    *,
    status: int = 200,
    body: bytes = b"%PDF-1.4 fake body",
    headers: dict[str, str] | None = None,
) -> httpx.MockTransport:
    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status,
            content=body,
            headers=headers or {"content-type": "application/pdf"},
        )

    return httpx.MockTransport(_handler)


def test_download_pdf_success() -> None:
    transport = _make_transport(body=b"%PDF-1.4 hello", headers={"content-type": "application/pdf"})
    data, mime = pdf_fetch.download_pdf(
        "https://example.invalid/p.pdf",
        transport=transport,
        timeout=5.0,
        max_bytes=1024,
        user_agent="test/1.0",
    )
    assert data == b"%PDF-1.4 hello"
    assert mime == "application/pdf"


def test_download_pdf_404_raises() -> None:
    transport = _make_transport(status=404, body=b"nope")
    with pytest.raises(pdf_fetch.PdfFetchError) as exc:
        pdf_fetch.download_pdf(
            "https://example.invalid/missing.pdf",
            transport=transport,
            timeout=5.0,
            max_bytes=1024,
            user_agent="test/1.0",
        )
    assert "404" in str(exc.value)


def test_download_pdf_exceeds_max_bytes() -> None:
    big = b"%PDF-1.4 " + (b"x" * 2000)
    transport = _make_transport(body=big)
    with pytest.raises(pdf_fetch.PdfTooLargeError) as exc:
        pdf_fetch.download_pdf(
            "https://example.invalid/big.pdf",
            transport=transport,
            timeout=5.0,
            max_bytes=512,
            user_agent="test/1.0",
        )
    assert "max_bytes" in str(exc.value)


def test_download_pdf_network_error() -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("dns failed")

    transport = httpx.MockTransport(_handler)
    with pytest.raises(pdf_fetch.PdfFetchError):
        pdf_fetch.download_pdf(
            "https://example.invalid/p.pdf",
            transport=transport,
            timeout=5.0,
            max_bytes=1024,
            user_agent="test/1.0",
        )


def test_download_pdf_falls_back_mime_when_header_missing() -> None:
    transport = _make_transport(headers={"content-type": "application/octet-stream"})
    data, mime = pdf_fetch.download_pdf(
        "https://example.invalid/p.pdf",
        transport=transport,
        timeout=5.0,
        max_bytes=1024,
        user_agent="test/1.0",
    )
    assert mime == "application/pdf"  # forced when path ends in .pdf and server lies
```

### Step 2: Run test to verify it fails

Run: `cd overlay/tools/semantic_scholar && uv run --python 3.11 pytest tests/test_pdf_fetch.py -v`

Expected: FAIL — `ModuleNotFoundError: No module named 'semantic_scholar.pdf_fetch'`.

### Step 3: Write the fetch module

Create `overlay/tools/semantic_scholar/pdf_fetch.py`:

```python
"""HTTP downloader for paper PDFs (open-access URL or arXiv fallback).

Kept separate from ``SemanticScholarClient`` because PDF hosts have
nothing to do with the Graph API auth headers and need a different
User-Agent / redirect policy. Streaming with a hard byte cap so a
runaway paywall page or HTML 404 body can't OOM the API pod.
"""

from __future__ import annotations

import logging
from typing import Final

import httpx

log = logging.getLogger(__name__)

DEFAULT_USER_AGENT: Final[str] = "centaur-scientist/0.1 (paper-archive)"
DEFAULT_MAX_BYTES: Final[int] = 50 * 1024 * 1024  # 50 MiB
DEFAULT_TIMEOUT_S: Final[float] = 60.0


class PdfFetchError(RuntimeError):
    """Generic non-recoverable PDF fetch failure (HTTP error, network error, redirect loop)."""


class PdfTooLargeError(PdfFetchError):
    """Server's response exceeded ``max_bytes`` before we stopped reading."""


def download_pdf(
    url: str,
    *,
    timeout: float = DEFAULT_TIMEOUT_S,
    max_bytes: int = DEFAULT_MAX_BYTES,
    user_agent: str = DEFAULT_USER_AGENT,
    transport: httpx.BaseTransport | None = None,
) -> tuple[bytes, str]:
    """Stream a PDF from ``url`` into memory with a size cap.

    Returns ``(bytes, mime_type)``. If the URL path ends in ``.pdf`` we
    coerce ``mime_type`` to ``application/pdf`` so a server that returns
    ``application/octet-stream`` doesn't trip downstream sniffing.

    Raises :class:`PdfTooLargeError` when the body exceeds ``max_bytes``,
    or :class:`PdfFetchError` for any other transport/HTTP failure.
    """
    headers = {"User-Agent": user_agent, "Accept": "application/pdf,*/*;q=0.5"}
    try:
        with httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            transport=transport,
        ) as client:
            with client.stream("GET", url, headers=headers) as response:
                if response.status_code >= 400:
                    body_snippet = response.read()[:200].decode("utf-8", errors="replace")
                    raise PdfFetchError(
                        f"PDF fetch HTTP {response.status_code} for {url}: {body_snippet!r}"
                    )
                chunks: list[bytes] = []
                total = 0
                for chunk in response.iter_bytes():
                    total += len(chunk)
                    if total > max_bytes:
                        raise PdfTooLargeError(
                            f"PDF at {url} exceeded max_bytes={max_bytes} (read >= {total})"
                        )
                    chunks.append(chunk)
                data = b"".join(chunks)
                mime = response.headers.get("content-type", "application/octet-stream").split(";")[0].strip()
    except httpx.RequestError as exc:
        raise PdfFetchError(f"PDF fetch network error for {url}: {exc}") from exc

    if url.lower().split("?", 1)[0].endswith(".pdf"):
        mime = "application/pdf"
    return data, mime


def derive_pdf_url(paper: dict) -> str | None:
    """Pick the best PDF URL for an S2 paper dict.

    Priority:
    1. ``openAccessPdf.url`` — when S2 hosts or links open access
    2. arXiv via ``externalIds.ArXiv`` — most common open-access fallback

    Returns ``None`` when no source is available (typical for paywalled
    papers; caller should record ``skipped/no_pdf_url``).
    """
    oa = paper.get("openAccessPdf")
    if isinstance(oa, dict):
        candidate = oa.get("url")
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    external = paper.get("externalIds")
    if isinstance(external, dict):
        arxiv = external.get("ArXiv")
        if isinstance(arxiv, str) and arxiv.strip():
            return f"https://arxiv.org/pdf/{arxiv.strip()}.pdf"
    return None
```

### Step 4: Run test to verify it passes

Run: `cd overlay/tools/semantic_scholar && uv run --python 3.11 pytest tests/test_pdf_fetch.py -v`

Expected: 5 PASS.

### Step 5: Commit

```bash
git add overlay/tools/semantic_scholar/pdf_fetch.py \
       overlay/tools/semantic_scholar/tests/test_pdf_fetch.py
git commit -m "feat(s2): add streaming PDF downloader with size cap + arxiv fallback URL"
```

---

## Task 3: Migration — `paper_archives` table

**Files:**
- Create: `overlay/services/api/db/migrations/20260526000001_add_paper_archives.sql`

### Step 1: Author the migration

Create `overlay/services/api/db/migrations/20260526000001_add_paper_archives.sql`:

```sql
-- migrate:up
-- Raw PDF storage and parsed text for Semantic Scholar papers.
-- Keyed by S2 paperId. Parsed text is also written as a
-- source_type="paper_fulltext" row in company_context_documents (linked
-- to the metadata row via parent_document_id) so BM25 indexes it; this
-- table is the source-of-truth for the original bytes + parse metadata.

CREATE TABLE paper_archives (
    paper_id        TEXT PRIMARY KEY,
    source_url      TEXT NOT NULL,
    mime_type       TEXT NOT NULL,
    size_bytes      BIGINT NOT NULL,
    pdf_sha256      TEXT NOT NULL,
    pdf_bytes       BYTEA NOT NULL,
    parsed_text     TEXT NOT NULL,
    parser_used     TEXT NOT NULL,
    truncated       BOOLEAN NOT NULL DEFAULT FALSE,
    metadata        JSONB NOT NULL DEFAULT '{}'::jsonb,
    archived_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX paper_archives_pdf_sha256_idx ON paper_archives (pdf_sha256);
CREATE INDEX paper_archives_archived_at_idx ON paper_archives (archived_at DESC);

-- migrate:down
DROP TABLE IF EXISTS paper_archives;
```

### Step 2: Apply locally to verify SQL

Run (against your port-forwarded centaur_test DB; see `db/README.md` for the port-forward + secret recipe):

```bash
./.centaur/contrib/scripts/dbmate --set overlay --migrations-dir overlay/services/api/db/migrations migrate
```

Expected: `Applying: 20260526000001_add_paper_archives.sql` then `psql ... \dt paper_archives` shows the table.

Run rollback round-trip:

```bash
./.centaur/contrib/scripts/dbmate --set overlay --migrations-dir overlay/services/api/db/migrations rollback
./.centaur/contrib/scripts/dbmate --set overlay --migrations-dir overlay/services/api/db/migrations migrate
```

Expected: both succeed; table re-appears.

### Step 3: Commit

```bash
git add overlay/services/api/db/migrations/20260526000001_add_paper_archives.sql
git commit -m "feat(db): add paper_archives table for raw PDF + parsed text storage"
```

---

## Task 4: `paper_fulltext.py` — document builder + archive DAO

**Files:**
- Create: `overlay/centaur_lab/paper_fulltext.py`
- Test: `overlay/workflows/tests/test_paper_fulltext.py`

### Step 1: Write the failing test

Create `overlay/workflows/tests/test_paper_fulltext.py`:

```python
"""Unit tests for ``centaur_lab.paper_fulltext``."""

from __future__ import annotations

from typing import Any

import pytest

from centaur_lab import paper_fulltext
from workflows.tests._mocks import EXECUTE_ARG_INDEX, MockPool


def _sample_paper() -> dict[str, Any]:
    return {
        "paperId": "abc123",
        "title": "Attention Is All You Need",
        "authors": [{"authorId": "1", "name": "Ashish Vaswani"}],
        "year": 2017,
        "abstract": "abstract here",
        "url": "https://example.invalid/abc123",
        "openAccessPdf": {"url": "https://example.invalid/abc123.pdf"},
    }


def test_build_fulltext_document_basic_shape() -> None:
    doc = paper_fulltext.build_fulltext_document(
        _sample_paper(),
        parsed_text="# Attention\n\nFull paper body here.",
        parent_document_id="semantic_scholar:paper:abc123",
        parser_used="pymupdf4llm",
        truncated=False,
        pdf_sha256="deadbeef",
    )
    assert doc["document_id"] == "semantic_scholar:paper_fulltext:abc123"
    assert doc["source_type"] == "paper_fulltext"
    assert doc["parent_document_id"] == "semantic_scholar:paper:abc123"
    assert doc["source_chunk_id"] == ""
    assert doc["body"].startswith("# Attention")
    assert doc["metadata"]["parserUsed"] == "pymupdf4llm"
    assert doc["metadata"]["pdfSha256"] == "deadbeef"
    assert doc["metadata"]["truncated"] is False
    assert doc["metadata"]["charCount"] == len(doc["body"])


def test_build_fulltext_document_truncates_long_body() -> None:
    body = "x" * (paper_fulltext.FULLTEXT_BODY_MAX_BYTES + 1024)
    doc = paper_fulltext.build_fulltext_document(
        _sample_paper(),
        parsed_text=body,
        parent_document_id="semantic_scholar:paper:abc123",
        parser_used="pymupdf",
        truncated=True,  # caller already detected
        pdf_sha256="cafe",
    )
    assert len(doc["body"].encode("utf-8")) <= paper_fulltext.FULLTEXT_BODY_MAX_BYTES
    assert doc["metadata"]["truncated"] is True


@pytest.mark.asyncio
async def test_upsert_paper_archive_inserts(monkeypatch: pytest.MonkeyPatch) -> None:
    pool = MockPool(fetchval_returns=[None], execute_returns=["INSERT 0 1"])
    action = await paper_fulltext.upsert_paper_archive(
        pool,
        {
            "paper_id": "abc123",
            "source_url": "https://example.invalid/abc123.pdf",
            "mime_type": "application/pdf",
            "size_bytes": 12345,
            "pdf_sha256": "deadbeef",
            "pdf_bytes": b"%PDF-1.4 ...",
            "parsed_text": "# Attention",
            "parser_used": "pymupdf4llm",
            "truncated": False,
            "metadata": {"query": "transformers"},
        },
    )
    assert action == "inserted"
    assert pool.execute_calls[0][EXECUTE_ARG_INDEX]  # first positional SQL is INSERT


@pytest.mark.asyncio
async def test_upsert_paper_archive_noop_when_hash_unchanged() -> None:
    pool = MockPool(fetchval_returns=["deadbeef"], execute_returns=[])
    action = await paper_fulltext.upsert_paper_archive(
        pool,
        {
            "paper_id": "abc123",
            "source_url": "u",
            "mime_type": "application/pdf",
            "size_bytes": 1,
            "pdf_sha256": "deadbeef",
            "pdf_bytes": b"",
            "parsed_text": "",
            "parser_used": "pymupdf4llm",
            "truncated": False,
            "metadata": {},
        },
    )
    assert action == "noop"
```

### Step 2: Run test to verify it fails

Run: `cd overlay/workflows && uv run --python 3.11 pytest tests/test_paper_fulltext.py -v`

Expected: FAIL — `ModuleNotFoundError: No module named 'centaur_lab.paper_fulltext'`.

### Step 3: Write the helper module

Create `overlay/centaur_lab/paper_fulltext.py`:

```python
"""Helpers for projecting parsed PDF text into the overlay's storage.

Two sinks:

1. ``upsert_paper_archive`` — raw PDF bytes + parsed text + parser
   metadata into the overlay-owned ``paper_archives`` table. This is
   the source-of-truth for re-parsing without re-fetching.
2. ``build_fulltext_document`` — projects the same parsed text into a
   ``source_type="paper_fulltext"`` row destined for
   ``company_context_documents`` (via the existing
   ``upsert_document`` helper). That row is what BM25 indexes so
   downstream search surfaces find papers by their body content,
   not just by abstract.

Keeping the two storage paths split lets us re-parse paper bodies
without touching the paper-metadata row, and lets us drop
``paper_archives`` for cost reasons later without losing the indexed
text in ``company_context_documents``.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any, Literal

from centaur_lab.paper_document import _canonical_json, _content_hash

FULLTEXT_BODY_MAX_BYTES = 1 * 1024 * 1024  # 1 MiB cap on parsed text persisted in body


def _safe_truncate_utf8(text: str, max_bytes: int) -> tuple[str, bool]:
    """Truncate ``text`` to fit ``max_bytes`` of UTF-8 without splitting codepoints.

    Returns ``(truncated_text, was_truncated)``.
    """
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text, False
    encoded = encoded[:max_bytes]
    return encoded.decode("utf-8", errors="ignore"), True


def build_fulltext_document(
    paper: dict[str, Any],
    *,
    parsed_text: str,
    parent_document_id: str,
    parser_used: str,
    truncated: bool,
    pdf_sha256: str,
    source_url: str | None = None,
) -> dict[str, Any]:
    """Project parsed PDF text into a ``paper_fulltext`` company_context_documents row.

    The row shares the paper's title/authors/url so search surfaces
    aren't degraded; the body is the parsed Markdown (truncated if
    over ``FULLTEXT_BODY_MAX_BYTES``). Linked to the existing paper
    metadata row via ``parent_document_id`` so the
    ``upsert_document`` compound-hash trick (see
    ``paper_document.upsert_document``) makes re-parenting trigger
    an UPDATE.
    """
    paper_id = str(paper.get("paperId") or "").strip()
    if not paper_id:
        raise ValueError("paper.paperId is required to build fulltext document")

    body, body_was_truncated = _safe_truncate_utf8(parsed_text, FULLTEXT_BODY_MAX_BYTES)
    effective_truncated = bool(truncated or body_was_truncated)

    title = str(paper.get("title") or "Untitled").strip() or "Untitled"
    authors = paper.get("authors") if isinstance(paper.get("authors"), list) else []
    first_author = authors[0] if authors else {}
    author_id = str(first_author.get("authorId") or "") if isinstance(first_author, dict) else ""
    author_name = str(first_author.get("name") or "") if isinstance(first_author, dict) else ""

    year_value = paper.get("year")
    year_int: int | None = int(year_value) if isinstance(year_value, (int, float)) else None
    occurred_at = datetime(year_int, 1, 1, tzinfo=UTC) if year_int is not None else None

    url = str(paper.get("url") or "").strip() or f"https://www.semanticscholar.org/paper/{paper_id}"

    metadata: dict[str, Any] = {
        "paperId": paper_id,
        "parserUsed": parser_used,
        "truncated": effective_truncated,
        "charCount": len(body),
        "pdfSha256": pdf_sha256,
        "sourceUrl": source_url,
    }

    return {
        "document_id": f"semantic_scholar:paper_fulltext:{paper_id}",
        "source": "semantic_scholar",
        "source_type": "paper_fulltext",
        "source_document_id": paper_id,
        "source_chunk_id": "",
        "parent_document_id": parent_document_id,
        "title": title,
        "body": body,
        "url": url,
        "author_id": author_id,
        "author_name": author_name,
        "access_scope": "company",
        "occurred_at": occurred_at,
        "source_updated_at": datetime.now(UTC),
        "content_hash": _content_hash(title, body, url, metadata),
        "metadata": metadata,
    }


def compute_pdf_sha256(data: bytes) -> str:
    """Hex SHA-256 of the raw PDF bytes."""
    return hashlib.sha256(data).hexdigest()


async def upsert_paper_archive(
    pool: Any,
    row: dict[str, Any],
) -> Literal["inserted", "updated", "noop"]:
    """Idempotently insert/update a ``paper_archives`` row.

    Idempotency key: ``(paper_id, pdf_sha256)``. If the existing row
    has the same ``pdf_sha256`` we return ``"noop"`` without touching
    the parsed text — re-parses with the same source PDF are caller-
    managed (delete the row to force).
    """
    existing_hash = await pool.fetchval(
        "SELECT pdf_sha256 FROM paper_archives WHERE paper_id = $1",
        row["paper_id"],
    )
    if existing_hash == row["pdf_sha256"]:
        return "noop"

    status = await pool.execute(
        "INSERT INTO paper_archives ("
        "paper_id, source_url, mime_type, size_bytes, pdf_sha256, pdf_bytes, "
        "parsed_text, parser_used, truncated, metadata, archived_at, updated_at"
        ") VALUES ("
        "$1, $2, $3, $4, $5, $6, $7, $8, $9, $10::jsonb, NOW(), NOW()"
        ") ON CONFLICT (paper_id) DO UPDATE SET "
        "source_url = EXCLUDED.source_url, "
        "mime_type = EXCLUDED.mime_type, "
        "size_bytes = EXCLUDED.size_bytes, "
        "pdf_sha256 = EXCLUDED.pdf_sha256, "
        "pdf_bytes = EXCLUDED.pdf_bytes, "
        "parsed_text = EXCLUDED.parsed_text, "
        "parser_used = EXCLUDED.parser_used, "
        "truncated = EXCLUDED.truncated, "
        "metadata = EXCLUDED.metadata, "
        "updated_at = NOW()",
        row["paper_id"],
        row["source_url"],
        row["mime_type"],
        row["size_bytes"],
        row["pdf_sha256"],
        row["pdf_bytes"],
        row["parsed_text"],
        row["parser_used"],
        row["truncated"],
        _canonical_json(row.get("metadata") or {}),
    )
    if not status.endswith(" 1"):
        return "noop"
    return "updated" if existing_hash else "inserted"
```

### Step 4: Run test to verify it passes

Run: `cd overlay/workflows && uv run --python 3.11 pytest tests/test_paper_fulltext.py -v`

Expected: 4 PASS.

### Step 5: Commit

```bash
git add overlay/centaur_lab/paper_fulltext.py \
       overlay/workflows/tests/test_paper_fulltext.py
git commit -m "feat(centaur_lab): add paper_fulltext document builder + paper_archives DAO"
```

---

## Task 5: Update deps + Dockerfile sanity

**Files:**
- Modify: `overlay/tools/semantic_scholar/pyproject.toml`

### Step 1: Add deps

Edit `overlay/tools/semantic_scholar/pyproject.toml` — `dependencies` array:

Before:

```toml
dependencies = [
    "httpx>=0.27.0",
    "asyncpg>=0.30.0",
    "typer>=0.12.0",
    "rich>=13.0.0",
    "python-dotenv>=1.0.0",
]
```

After:

```toml
dependencies = [
    "httpx>=0.27.0",
    "asyncpg>=0.30.0",
    "typer>=0.12.0",
    "rich>=13.0.0",
    "python-dotenv>=1.0.0",
    "pymupdf>=1.24.0",
    "pymupdf4llm>=0.0.17",
    "pypdf>=4.0.0",
]
```

### Step 2: Verify install

Run:

```bash
cd overlay/tools/semantic_scholar
uv sync --group dev --python 3.11
uv run --python 3.11 python -c "import pymupdf, pymupdf4llm, pypdf; print(pymupdf.__doc__[:80])"
```

Expected: import succeeds; prints the pymupdf docstring head.

### Step 3: Re-run all parse/fetch tests under the synced env

Run:

```bash
cd overlay/tools/semantic_scholar && uv run --python 3.11 pytest tests/test_pdf_parse.py tests/test_pdf_fetch.py -v
```

Expected: 10 PASS total (5 + 5).

### Step 4: Confirm Dockerfile needs no changes

The overlay image (`overlay/Dockerfile`) is Alpine static-files only; deps install at API discovery time. `pymupdf>=1.24` ships manylinux wheels for Python 3.11 (no source build needed). No Dockerfile edits.

Quick sanity check that wheels exist (do this once locally, not in the build):

```bash
uv pip download --python 3.11 pymupdf==1.24.0 -d /tmp/whl-check && ls /tmp/whl-check
```

Expected: a `pymupdf-1.24.0-*manylinux*_x86_64.whl` file appears. (Don't commit `/tmp/whl-check`.)

### Step 5: Commit

```bash
git add overlay/tools/semantic_scholar/pyproject.toml
git commit -m "build(s2): add pymupdf, pymupdf4llm, pypdf for PDF archival"
```

---

## Task 6: `SemanticScholarClient.archive_paper`

**Files:**
- Modify: `overlay/tools/semantic_scholar/client.py`
- Create: `overlay/tools/semantic_scholar/tests/test_archive_paper.py`

### Step 1: Write the failing test

Create `overlay/tools/semantic_scholar/tests/test_archive_paper.py`:

```python
"""Tests for ``SemanticScholarClient.archive_paper``."""

from __future__ import annotations

from typing import Any

import pytest

from semantic_scholar import pdf_fetch
from semantic_scholar.client import SemanticScholarClient


def _paper(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "paperId": "abc123",
        "title": "T",
        "authors": [{"name": "A"}],
        "year": 2020,
        "abstract": "abs",
        "url": "https://example.invalid/abc123",
        "openAccessPdf": {"url": "https://example.invalid/abc123.pdf", "status": "GREEN"},
        "citationCount": 1,
        "externalIds": {"DOI": "10.x/abc"},
    }
    base.update(overrides)
    return base


class _FakePool:
    def __init__(self) -> None:
        self.archive_hash: str | None = None
        self.execute_calls: list[tuple[str, tuple[Any, ...]]] = []
        self.fetchval_calls: list[tuple[str, tuple[Any, ...]]] = []

    async def fetchval(self, sql: str, *args: Any) -> Any:
        self.fetchval_calls.append((sql, args))
        if "paper_archives" in sql:
            return self.archive_hash
        if "company_context_documents" in sql:
            return None  # paper metadata row not yet present (or whatever)
        return None

    async def execute(self, sql: str, *args: Any) -> str:
        self.execute_calls.append((sql, args))
        return "INSERT 0 1"


@pytest.fixture
def mock_pool(monkeypatch: pytest.MonkeyPatch) -> _FakePool:
    pool = _FakePool()
    monkeypatch.setattr(
        SemanticScholarClient,
        "_acquire_pool_for_archive",
        lambda self: _FakePoolAsCM(pool),
    )
    return pool


class _FakePoolAsCM:
    def __init__(self, pool: _FakePool) -> None:
        self._pool = pool

    async def __aenter__(self) -> _FakePool:
        return self._pool

    async def __aexit__(self, *exc: Any) -> None:
        return None


def test_archive_paper_skips_when_no_pdf_url(monkeypatch: pytest.MonkeyPatch) -> None:
    client = SemanticScholarClient(api_key="")
    monkeypatch.setattr(
        SemanticScholarClient,
        "get_paper",
        lambda self, paper_id, **_kw: _paper(openAccessPdf=None, externalIds={}),
    )

    result = client.archive_paper("abc123")
    assert result == {
        "status": "skipped",
        "paper_id": "abc123",
        "reason": "no_pdf_url",
    }


def test_archive_paper_skips_when_too_large(
    monkeypatch: pytest.MonkeyPatch, mock_pool: _FakePool
) -> None:
    client = SemanticScholarClient(api_key="")
    monkeypatch.setattr(SemanticScholarClient, "get_paper", lambda self, pid, **_kw: _paper())

    def _boom(*args: Any, **kwargs: Any) -> tuple[bytes, str]:
        raise pdf_fetch.PdfTooLargeError("too big")

    monkeypatch.setattr(pdf_fetch, "download_pdf", _boom)

    result = client.archive_paper("abc123")
    assert result["status"] == "skipped"
    assert result["reason"] == "too_large"


def test_archive_paper_returns_error_on_parse_failure(
    monkeypatch: pytest.MonkeyPatch, mock_pool: _FakePool
) -> None:
    from semantic_scholar import pdf_parse

    client = SemanticScholarClient(api_key="")
    monkeypatch.setattr(SemanticScholarClient, "get_paper", lambda self, pid, **_kw: _paper())
    monkeypatch.setattr(
        pdf_fetch, "download_pdf", lambda *a, **kw: (b"%PDF-1.4 ...", "application/pdf")
    )

    def _bad_parse(data: bytes, **kw: Any) -> tuple[str, str]:
        raise pdf_parse.PdfParseError("nothing extractable")

    monkeypatch.setattr(pdf_parse, "parse_pdf_to_markdown", _bad_parse)

    result = client.archive_paper("abc123")
    assert result["status"] == "error"
    assert "nothing extractable" in result["error"]


def test_archive_paper_success_path(
    monkeypatch: pytest.MonkeyPatch, mock_pool: _FakePool
) -> None:
    from semantic_scholar import pdf_parse

    client = SemanticScholarClient(api_key="")
    monkeypatch.setattr(SemanticScholarClient, "get_paper", lambda self, pid, **_kw: _paper())
    monkeypatch.setattr(
        pdf_fetch, "download_pdf", lambda *a, **kw: (b"%PDF-1.4 ...", "application/pdf")
    )
    monkeypatch.setattr(
        pdf_parse, "parse_pdf_to_markdown", lambda data, **_kw: ("# Title\n\n" + ("body " * 50), "pymupdf4llm")
    )

    result = client.archive_paper("abc123")

    assert result["status"] == "completed"
    assert result["paper_id"] == "abc123"
    assert result["parser_used"] == "pymupdf4llm"
    assert result["fulltext_document_id"] == "semantic_scholar:paper_fulltext:abc123"
    assert result["archive_action"] == "inserted"
    assert result["fulltext_action"] in {"inserted", "updated"}


def test_archive_paper_noop_when_pdf_unchanged(
    monkeypatch: pytest.MonkeyPatch, mock_pool: _FakePool
) -> None:
    from semantic_scholar import pdf_parse
    import hashlib

    pdf_bytes = b"%PDF-1.4 deterministic content"
    expected_sha = hashlib.sha256(pdf_bytes).hexdigest()
    mock_pool.archive_hash = expected_sha

    client = SemanticScholarClient(api_key="")
    monkeypatch.setattr(SemanticScholarClient, "get_paper", lambda self, pid, **_kw: _paper())
    monkeypatch.setattr(
        pdf_fetch, "download_pdf", lambda *a, **kw: (pdf_bytes, "application/pdf")
    )
    # parser shouldn't be invoked on noop path, but stub anyway to be safe
    monkeypatch.setattr(
        pdf_parse, "parse_pdf_to_markdown", lambda data, **_kw: ("won't see this", "pymupdf4llm")
    )

    result = client.archive_paper("abc123")
    assert result["status"] == "noop"
    assert result["archive_action"] == "noop"
```

### Step 2: Run test to verify it fails

Run: `cd overlay/tools/semantic_scholar && uv run --python 3.11 pytest tests/test_archive_paper.py -v`

Expected: 5 FAIL — `AttributeError: 'SemanticScholarClient' object has no attribute 'archive_paper'`.

### Step 3: Add constants + `archive_paper` to the client

Edit `overlay/tools/semantic_scholar/client.py`. First, add new constants below the existing block at lines 19–36 (insert after `MAX_RESEARCH_BRIEF_LIMIT = 20`):

```python
# Defaults for the archive_paper pipeline. PDF fetch defaults are
# generous so paywall hosts can serve real papers; the per-paper cap
# (MAX_PDF_BYTES) is the hard upper bound that protects the API pod
# from runaway HTML 404 bodies disguised as PDFs.
MAX_PDF_BYTES = 50 * 1024 * 1024  # 50 MiB
PDF_DOWNLOAD_TIMEOUT_S = 60.0
PDF_USER_AGENT = "centaur-scientist/0.1 (paper-archive; +https://centaur.run)"
ARCHIVE_PARSER_MIN_SIZE = 100  # mirrors AI-Scientist-v2 load_paper min_size
```

Add new imports near the top:

```python
import asyncpg

from centaur_lab.paper_document import build_paper_document, upsert_document
from centaur_lab.paper_fulltext import (
    build_fulltext_document,
    compute_pdf_sha256,
    upsert_paper_archive,
)
from semantic_scholar import pdf_fetch, pdf_parse
```

(`asyncpg` is already imported on line 11; `centaur_lab.brief.persist_research_brief_from_papers` is already there; `centaur_sdk.secret` is already there. Just add the three new ones.)

Append a new method to `SemanticScholarClient` (after `research_brief`, before `_client()`):

```python
    def _acquire_pool_for_archive(self) -> Any:
        """Return an async context manager yielding an asyncpg pool-like object.

        Default impl opens a fresh single-connection asyncpg connection (so
        ``ctx._pool``-style ``fetchval`` / ``execute`` works) and closes it on
        exit. Tests override this with an in-memory pool. Mirrors the
        per-call connect pattern from ``research_brief`` to avoid surfacing
        a pool through the constructor.
        """
        client = self
        database_url = client._require_database_url()

        class _ConnAsPool:
            async def __aenter__(self) -> Any:
                self._conn = await asyncpg.connect(database_url, command_timeout=60)
                return self._conn

            async def __aexit__(self, *exc: Any) -> None:
                await self._conn.close()

        return _ConnAsPool()

    async def _archive_paper_async(
        self,
        paper_id: str,
        *,
        source_url: str | None = None,
    ) -> dict[str, Any]:
        """Coroutine sibling of ``archive_paper`` for workflow use.

        Workflows already run in an asyncio loop; calling the sync
        ``archive_paper`` (which uses ``asyncio.run`` internally) would
        crash with "asyncio.run cannot be called from a running event
        loop". This method does the same work directly in the caller's
        loop and accepts a caller-provided pool override via
        ``self._acquire_pool_for_archive``.
        """
        normalized_id = (paper_id or "").strip()
        if not normalized_id:
            return {"status": "error", "paper_id": paper_id, "error": "paper_id cannot be empty"}

        try:
            paper = await asyncio.to_thread(self.get_paper, normalized_id)
        except (ValueError, RuntimeError) as exc:
            return {"status": "error", "paper_id": normalized_id, "error": str(exc)}

        url = source_url or pdf_fetch.derive_pdf_url(paper)
        if not url:
            return {"status": "skipped", "paper_id": normalized_id, "reason": "no_pdf_url"}

        try:
            data, mime = await asyncio.to_thread(
                pdf_fetch.download_pdf,
                url,
                timeout=PDF_DOWNLOAD_TIMEOUT_S,
                max_bytes=MAX_PDF_BYTES,
                user_agent=PDF_USER_AGENT,
            )
        except pdf_fetch.PdfTooLargeError:
            return {
                "status": "skipped",
                "paper_id": normalized_id,
                "reason": "too_large",
                "source_url": url,
            }
        except pdf_fetch.PdfFetchError as exc:
            return {
                "status": "error",
                "paper_id": normalized_id,
                "source_url": url,
                "error": str(exc),
            }

        pdf_sha256 = compute_pdf_sha256(data)

        async with self._acquire_pool_for_archive() as pool:
            existing = await pool.fetchval(
                "SELECT pdf_sha256 FROM paper_archives WHERE paper_id = $1",
                normalized_id,
            )
            if existing == pdf_sha256:
                return {
                    "status": "noop",
                    "paper_id": normalized_id,
                    "source_url": url,
                    "archive_action": "noop",
                    "pdf_sha256": pdf_sha256,
                }

            try:
                parsed_text, parser_used = await asyncio.to_thread(
                    pdf_parse.parse_pdf_to_markdown,
                    data,
                    min_size=ARCHIVE_PARSER_MIN_SIZE,
                )
            except pdf_parse.PdfParseError as exc:
                return {
                    "status": "error",
                    "paper_id": normalized_id,
                    "source_url": url,
                    "error": str(exc),
                }

            paper_doc = build_paper_document(paper)
            paper_action = await upsert_document(pool, paper_doc)

            fulltext_doc = build_fulltext_document(
                paper,
                parsed_text=parsed_text,
                parent_document_id=paper_doc["document_id"],
                parser_used=parser_used,
                truncated=False,
                pdf_sha256=pdf_sha256,
                source_url=url,
            )
            fulltext_action = await upsert_document(pool, fulltext_doc)

            archive_action = await upsert_paper_archive(
                pool,
                {
                    "paper_id": normalized_id,
                    "source_url": url,
                    "mime_type": mime,
                    "size_bytes": len(data),
                    "pdf_sha256": pdf_sha256,
                    "pdf_bytes": data,
                    "parsed_text": parsed_text,
                    "parser_used": parser_used,
                    "truncated": fulltext_doc["metadata"]["truncated"],
                    "metadata": {
                        "paperId": normalized_id,
                        "url": paper_doc["url"],
                    },
                },
            )

        return {
            "status": "completed",
            "paper_id": normalized_id,
            "source_url": url,
            "parser_used": parser_used,
            "pdf_sha256": pdf_sha256,
            "size_bytes": len(data),
            "paper_document_id": paper_doc["document_id"],
            "paper_action": paper_action,
            "fulltext_document_id": fulltext_doc["document_id"],
            "fulltext_action": fulltext_action,
            "archive_action": archive_action,
        }

    def archive_paper(
        self,
        paper_id: str,
        source_url: str | None = None,
    ) -> dict[str, Any]:
        """Download, parse, and archive a paper's PDF (agent-facing tool method).

        Resolves the PDF URL from ``openAccessPdf.url`` (or arXiv fallback),
        downloads with a 50 MiB cap, parses via the ``pymupdf4llm`` →
        ``pymupdf`` → ``pypdf`` chain, and persists:

        * raw bytes + parsed text + parser metadata in ``paper_archives``
        * a ``source_type="paper_fulltext"`` row in
          ``company_context_documents`` (linked to the paper metadata row)

        Returns ``{"status": "completed" | "skipped" | "noop" | "error",
        ...}``. Never raises.
        """
        try:
            return asyncio.run(self._archive_paper_async(paper_id, source_url=source_url))
        except Exception as exc:  # noqa: BLE001 — agent-facing wrapper never raises
            log.warning("archive_paper_failed", exc_info=True)
            return {"status": "error", "paper_id": paper_id, "error": str(exc)}
```

### Step 4: Run test to verify it passes

Run: `cd overlay/tools/semantic_scholar && uv run --python 3.11 pytest tests/test_archive_paper.py -v`

Expected: 5 PASS.

### Step 5: Run the full tool suite to check we didn't break anything

Run: `just overlay::test-tools` (or `cd overlay/tools/semantic_scholar && uv run --python 3.11 pytest tests/ --ignore=tests/integration -v`)

Expected: all existing tests still pass.

### Step 6: Commit

```bash
git add overlay/tools/semantic_scholar/client.py \
       overlay/tools/semantic_scholar/tests/test_archive_paper.py
git commit -m "feat(s2): add SemanticScholarClient.archive_paper agent tool method"
```

---

## Task 7: CLI subcommand `archive`

**Files:**
- Modify: `overlay/tools/semantic_scholar/cli.py`

### Step 1: Add the Typer subcommand

Open `overlay/tools/semantic_scholar/cli.py`. After the existing `research-brief` command, append:

```python
@app.command("archive")
def archive(
    paper_id: str = typer.Argument(..., help="Semantic Scholar paperId (or DOI:..., arXiv:...)"),
    source_url: str | None = typer.Option(
        None,
        "--source-url",
        help="Override PDF URL (defaults to openAccessPdf.url → arxiv fallback).",
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit raw JSON instead of a summary table."),
) -> None:
    """Download, parse, and archive a paper PDF into paper_archives + company_context_documents.

    Requires DATABASE_URL pointed at a Centaur DB with the
    20260526000001_add_paper_archives migration applied. ``just db::port-forward``
    plus ``just db::fetch-secret`` gives you a local DSN.
    """
    client = _make_client()
    result = client.archive_paper(paper_id, source_url=source_url)

    if json_output:
        console.print_json(json.dumps(result))
        return

    status = result.get("status", "unknown")
    color = {"completed": "green", "noop": "yellow", "skipped": "yellow", "error": "red"}.get(
        status, "white"
    )
    console.print(f"[{color}]status={status}[/]")
    for key in (
        "paper_id",
        "source_url",
        "parser_used",
        "pdf_sha256",
        "size_bytes",
        "paper_document_id",
        "paper_action",
        "fulltext_document_id",
        "fulltext_action",
        "archive_action",
        "reason",
        "error",
    ):
        if key in result:
            console.print(f"  {key} = {result[key]}")
```

### Step 2: Smoke test the CLI

Run (against a port-forwarded centaur_test DB; set `DATABASE_URL` first):

```bash
cd overlay/tools/semantic_scholar
uv run python cli.py archive 173ba8ae4582b6f9f6919aa3f813579a5349f1f9 --json
```

Expected: prints a JSON object with `"status": "completed"` (or `"skipped"` with `reason: "no_pdf_url"` if the paper has no open-access PDF).

### Step 3: Commit

```bash
git add overlay/tools/semantic_scholar/cli.py
git commit -m "feat(s2-cli): add 'archive' subcommand for local PDF archival smokes"
```

---

## Task 8: `archive_papers` workflow

**Files:**
- Create: `overlay/workflows/archive_papers.py`
- Create: `overlay/workflows/tests/test_archive_papers.py`

### Step 1: Write the failing test

Create `overlay/workflows/tests/test_archive_papers.py`:

```python
"""Unit tests for the ``archive_papers`` workflow handler."""

from __future__ import annotations

from typing import Any

import pytest

from workflows import archive_papers as wf
from workflows.tests._mocks import MockContext


@pytest.mark.asyncio
async def test_archive_papers_skips_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = MockContext()
    result = await wf.handler(wf.Input(paper_ids=[]), ctx)
    assert result == {"status": "skipped", "reason": "no_paper_ids"}
    assert ctx.logs[0][0] == "archive_papers_skipped_empty"


@pytest.mark.asyncio
async def test_archive_papers_aggregates_per_paper_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = MockContext()
    seen: list[str] = []

    async def _stub_archive(self: Any, paper_id: str, *, source_url: str | None = None) -> dict[str, Any]:
        seen.append(paper_id)
        if paper_id == "ok":
            return {"status": "completed", "paper_id": paper_id, "archive_action": "inserted"}
        if paper_id == "skip":
            return {"status": "skipped", "paper_id": paper_id, "reason": "no_pdf_url"}
        return {"status": "error", "paper_id": paper_id, "error": "boom"}

    monkeypatch.setattr(
        "tools.semantic_scholar.client.SemanticScholarClient._archive_paper_async",
        _stub_archive,
    )

    out = await wf.handler(wf.Input(paper_ids=["ok", "skip", "fail"]), ctx)
    assert out["status"] == "completed"
    assert out["papers_archived"] == 1
    assert out["papers_skipped"] == 1
    assert out["papers_failed"] == 1
    assert seen == ["ok", "skip", "fail"]
```

### Step 2: Run test to verify it fails

Run: `cd overlay/workflows && uv run --python 3.11 pytest tests/test_archive_papers.py -v`

Expected: FAIL — `ModuleNotFoundError: No module named 'workflows.archive_papers'`.

### Step 3: Implement the workflow

Create `overlay/workflows/archive_papers.py`:

```python
"""Workflow: batch-archive Semantic Scholar PDFs.

Given a list of paper IDs, fetch each paper, download its open-access
PDF, parse it via the ``pymupdf4llm``/``pymupdf``/``pypdf`` fallback
chain, and persist:

* raw bytes + parsed text + parser metadata in ``paper_archives``
* a ``source_type="paper_fulltext"`` row in ``company_context_documents``
  linked to the paper metadata row

Per-paper failures and skips (paywalled / oversized) are logged in the
result payload but do not abort the run. Unexpected exceptions
propagate so the run is marked failed.

Uses :meth:`SemanticScholarClient._archive_paper_async` (the coroutine
sibling of the public agent method) so we don't double-wrap an
``asyncio.run`` inside the workflow's event loop.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from api.workflow_engine import WorkflowContext

from tools.semantic_scholar.client import SemanticScholarClient

WORKFLOW_NAME = "archive_papers"


@dataclass
class Input:
    """Runtime options for the ``archive_papers`` workflow."""

    paper_ids: list[str]
    source_url_overrides: dict[str, str] = field(default_factory=dict)


class _WorkflowPoolAdapter:
    """Adapter that exposes ``ctx._pool`` as the async context manager
    the SemanticScholarClient archive path expects.

    The default ``_acquire_pool_for_archive`` opens a fresh connection
    per call; inside a workflow we already have a pool on ``ctx._pool``
    and want to reuse it so multi-paper batches don't reopen
    connections per item.
    """

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    async def __aenter__(self) -> Any:
        return self._pool

    async def __aexit__(self, *exc: Any) -> None:
        return None


async def handler(inp: Input, ctx: WorkflowContext) -> dict[str, Any]:
    if not inp.paper_ids:
        ctx.log("archive_papers_skipped_empty")
        return {"status": "skipped", "reason": "no_paper_ids"}

    client = SemanticScholarClient()
    client._acquire_pool_for_archive = lambda: _WorkflowPoolAdapter(ctx._pool)  # type: ignore[method-assign]

    results: list[dict[str, Any]] = []
    try:
        for paper_id in inp.paper_ids:
            override = inp.source_url_overrides.get(paper_id)
            result = await client._archive_paper_async(paper_id, source_url=override)
            results.append(result)
            ctx.log(
                "archive_papers_item",
                paper_id=paper_id,
                status=result.get("status"),
                parser_used=result.get("parser_used"),
                reason=result.get("reason"),
            )
    finally:
        client.close()

    archived = sum(1 for r in results if r.get("status") == "completed")
    noop = sum(1 for r in results if r.get("status") == "noop")
    skipped = sum(1 for r in results if r.get("status") == "skipped")
    failed = sum(1 for r in results if r.get("status") == "error")

    payload = {
        "status": "completed",
        "papers_archived": archived,
        "papers_noop": noop,
        "papers_skipped": skipped,
        "papers_failed": failed,
        "results": results,
    }
    ctx.log(
        "archive_papers_completed",
        papers_archived=archived,
        papers_noop=noop,
        papers_skipped=skipped,
        papers_failed=failed,
    )
    return payload
```

### Step 4: Run test to verify it passes

Run: `cd overlay/workflows && uv run --python 3.11 pytest tests/test_archive_papers.py -v`

Expected: 2 PASS.

### Step 5: Run the full workflow suite

Run: `just overlay::test-workflows`

Expected: all existing workflow tests still pass.

### Step 6: Commit

```bash
git add overlay/workflows/archive_papers.py \
       overlay/workflows/tests/test_archive_papers.py
git commit -m "feat(workflows): add archive_papers batch workflow for S2 PDF archival"
```

---

## Task 9: Integration tests

**Files:**
- Create: `overlay/tools/semantic_scholar/tests/integration/test_archive_paper_integration.py`
- Create: `overlay/workflows/tests/integration/test_archive_papers_integration.py`

### Step 1: Tool-level integration test (real DB + mocked HTTP)

Create `overlay/tools/semantic_scholar/tests/integration/test_archive_paper_integration.py`:

```python
"""Integration test: archive_paper end-to-end against real Postgres.

Gated on ``CENTAUR_TEST_DATABASE_URL`` (see conftest.py). HTTP to
Semantic Scholar and the PDF host are both mocked so the test is
deterministic; only the DB is real.
"""

from __future__ import annotations

import asyncio
from typing import Any

import asyncpg
import pytest

from semantic_scholar import pdf_fetch, pdf_parse
from semantic_scholar.client import SemanticScholarClient


@pytest.mark.asyncio
async def test_archive_paper_persists_three_rows(
    integration_db_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paper = {
        "paperId": "integration_abc",
        "title": "Integration Paper",
        "authors": [{"authorId": "a1", "name": "Alice"}],
        "year": 2024,
        "abstract": "abs",
        "url": "https://example.invalid/integration_abc",
        "openAccessPdf": {"url": "https://example.invalid/integration_abc.pdf"},
        "externalIds": {"DOI": "10.x/int"},
    }
    monkeypatch.setattr(SemanticScholarClient, "get_paper", lambda self, pid, **kw: paper)
    monkeypatch.setattr(
        pdf_fetch, "download_pdf", lambda *a, **kw: (b"%PDF-1.4 integration body", "application/pdf")
    )
    monkeypatch.setattr(
        pdf_parse,
        "parse_pdf_to_markdown",
        lambda data, **kw: ("# Integration\n\n" + ("text " * 80), "pymupdf4llm"),
    )

    client = SemanticScholarClient(database_url=integration_db_url)
    result = await client._archive_paper_async("integration_abc")

    assert result["status"] == "completed"

    pool = await asyncpg.create_pool(integration_db_url, min_size=1, max_size=2)
    try:
        archive_row = await pool.fetchrow(
            "SELECT paper_id, parser_used, parsed_text, pdf_sha256, size_bytes "
            "FROM paper_archives WHERE paper_id = $1",
            "integration_abc",
        )
        assert archive_row is not None
        assert archive_row["parser_used"] == "pymupdf4llm"
        assert archive_row["parsed_text"].startswith("# Integration")

        paper_row = await pool.fetchrow(
            "SELECT document_id, source_type FROM company_context_documents "
            "WHERE document_id = 'semantic_scholar:paper:integration_abc'"
        )
        assert paper_row is not None
        assert paper_row["source_type"] == "paper"

        fulltext_row = await pool.fetchrow(
            "SELECT document_id, source_type, parent_document_id, body "
            "FROM company_context_documents "
            "WHERE document_id = 'semantic_scholar:paper_fulltext:integration_abc'"
        )
        assert fulltext_row is not None
        assert fulltext_row["source_type"] == "paper_fulltext"
        assert fulltext_row["parent_document_id"] == "semantic_scholar:paper:integration_abc"
        assert fulltext_row["body"].startswith("# Integration")
    finally:
        await pool.execute(
            "DELETE FROM company_context_documents "
            "WHERE document_id LIKE 'semantic_scholar:%:integration_abc'"
        )
        await pool.execute("DELETE FROM paper_archives WHERE paper_id = 'integration_abc'")
        await pool.close()
```

### Step 2: Confirm conftest provides `integration_db_url`

The existing `overlay/tools/semantic_scholar/tests/integration/conftest.py` already defines an `integration_db_url` fixture gated on `CENTAUR_TEST_DATABASE_URL`. No edits needed — verify it exists; if it does not, add the standard fixture (copy from `overlay/workflows/tests/integration/conftest.py`).

### Step 3: Run integration tests locally

Set up port-forward + secret per `db/README.md`, then:

```bash
export CENTAUR_TEST_DATABASE_URL="postgres://...centaur_test..."
just overlay::test-tools-integration
```

Expected: the new test plus existing integration tests pass.

### Step 4: Workflow-level integration test

Create `overlay/workflows/tests/integration/test_archive_papers_integration.py` (uses the same pattern; mocks `_archive_paper_async` to a fast inline path or reuses the tool-level mocks via shared fixtures — keep it simple, just verify the workflow aggregates correctly over a 2-paper batch and rolls up counts).

```python
"""Integration test for archive_papers workflow against real Postgres."""

from __future__ import annotations

import asyncpg
import pytest

from workflows import archive_papers as wf
from workflows.tests.integration._utils import IntegrationContext


@pytest.mark.asyncio
async def test_archive_papers_aggregates_against_real_pool(
    integration_db_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pool = await asyncpg.create_pool(integration_db_url, min_size=1, max_size=2)
    try:
        ctx = IntegrationContext(pool)

        async def _stub_archive(self, paper_id, *, source_url=None):
            return {"status": "completed", "paper_id": paper_id, "archive_action": "inserted"}

        monkeypatch.setattr(
            "tools.semantic_scholar.client.SemanticScholarClient._archive_paper_async",
            _stub_archive,
        )

        out = await wf.handler(wf.Input(paper_ids=["a", "b"]), ctx)
        assert out["papers_archived"] == 2
    finally:
        await pool.close()
```

(If `IntegrationContext` does not exist, create it as a minimal class exposing `_pool` and `log` — patterned after `overlay/workflows/tests/_mocks.MockContext` but binding `_pool` to a real asyncpg pool. Confirm whether the existing `overlay/workflows/tests/integration/conftest.py` already defines one; if so, import it.)

### Step 5: Commit

```bash
git add overlay/tools/semantic_scholar/tests/integration/test_archive_paper_integration.py \
       overlay/workflows/tests/integration/test_archive_papers_integration.py
git commit -m "test(s2): add integration tests for archive_paper + archive_papers"
```

---

## Task 10: Justfile recipes + skill doc

**Files:**
- Modify: `overlay/Justfile`
- Modify: `overlay/.agents/skills/academic-research/SKILL.md`

### Step 1: Add Justfile recipes

Open `overlay/Justfile`. After `smoke-research-brief` (around line 212), append:

```just
# Smoke-test the archive_paper tool method via the CLI subcommand.
# Downloads the open-access PDF for a paper, parses with pymupdf4llm
# (falling back to pymupdf and pypdf), and persists raw bytes + parsed
# Markdown into paper_archives + a paper_fulltext company_context_documents
# row. Requires DATABASE_URL pointed at a centaur_test DB with the
# 20260526000001_add_paper_archives migration applied.
[group('dev')]
smoke-s2-archive paper_id="173ba8ae4582b6f9f6919aa3f813579a5349f1f9":
    cd tools/semantic_scholar && uv run python cli.py archive {{ paper_id }} --json

# Smoke-test the DEPLOYED archive_papers workflow. Mirrors smoke-save-papers
# auth pattern; relies on the chart-injected $SLACKBOT_API_KEY.
[group('cluster')]
smoke-archive-papers paper_id="173ba8ae4582b6f9f6919aa3f813579a5349f1f9":
    kubectl exec -n {{namespace}} deploy/{{release}}-centaur-api -- sh -c \
      'curl -sS -X POST http://localhost:8000/workflows/runs \
        -H "X-Api-Key: $SLACKBOT_API_KEY" \
        -H "Content-Type: application/json" \
        -d "{\"workflow_name\":\"archive_papers\",\"input\":{\"paper_ids\":[\"{{ paper_id }}\"]}}"' \
      | jq .
```

### Step 2: Update the academic-research skill

Open `overlay/.agents/skills/academic-research/SKILL.md`. Add a new section after the existing tool/workflow listings (locate "When to use research_brief" or the closest analog) that documents:

- `semantic_scholar.archive_paper(paper_id)` — when an agent wants full-text content rather than just abstract
- `archive_papers` workflow — when archiving a batch from a brief
- The two-row pattern: search hits on `source_type="paper_fulltext"` rows expose paper bodies; `source_type="paper"` rows remain for metadata-only queries

Concrete diff: append the following section (exact wording can vary):

```markdown
## Archiving full-text PDFs

For research that requires the paper body (not just abstract), use the
`semantic_scholar.archive_paper` tool method (single paper) or the
`archive_papers` workflow (batch). Behavior:

1. Resolves the PDF URL from `openAccessPdf.url`, falling back to
   `https://arxiv.org/pdf/{externalIds.ArXiv}.pdf` when present.
2. Downloads with a 50 MiB hard cap; paywalled / oversized papers
   return `{"status": "skipped", "reason": "no_pdf_url" | "too_large"}`.
3. Parses via `pymupdf4llm` → `pymupdf` → `pypdf` (3-tier fallback).
4. Persists raw bytes + parsed text in `paper_archives` (keyed by
   paperId) and writes a `source_type="paper_fulltext"` row in
   `company_context_documents` linked to the paper metadata row.

When ranking or filtering search results, the `paper_fulltext` rows
make `body` searchable via BM25. The original `paper` rows remain
unchanged so abstract-level queries keep their existing recall.

Examples::

    call semantic_scholar archive_paper '{"paper_id":"abc123"}'

    POST /workflows/runs {"workflow_name":"archive_papers",
                          "input":{"paper_ids":["abc123","def456"]}}
```

### Step 3: Commit

```bash
git add overlay/Justfile overlay/.agents/skills/academic-research/SKILL.md
git commit -m "docs(s2): document archive_paper + archive_papers in skill + Justfile recipes"
```

---

## Task 11: End-to-end smoke + verification

**Files:** none

### Step 1: Run the entire overlay test surface

Run (from repo root):

```bash
just overlay::test-tools
just overlay::test-workflows
```

Expected: 0 failures across both suites. Confirm the new test files appear in the test count.

### Step 2: Run integration tests against port-forwarded DB

Run:

```bash
just db::port-forward &  # if not already running
export CENTAUR_TEST_DATABASE_URL="$(just --quiet db::test-dsn)"
just overlay::test-tools-integration
just overlay::test-workflows-integration
```

Expected: green.

### Step 3: Smoke against a real paper

Pick a known open-access paper (the workflow default `173ba8ae4582b6f9f6919aa3f813579a5349f1f9` works — it's the active-inference robot paper used in `smoke-save-papers`).

```bash
export DATABASE_URL="$CENTAUR_TEST_DATABASE_URL"
just overlay::smoke-s2-archive
```

Expected output (truncated):

```
status=completed
  paper_id = 173ba8ae...
  source_url = https://...pdf
  parser_used = pymupdf4llm
  pdf_sha256 = <64 hex>
  size_bytes = <N>
  paper_document_id = semantic_scholar:paper:173ba8ae...
  paper_action = inserted | updated | noop
  fulltext_document_id = semantic_scholar:paper_fulltext:173ba8ae...
  fulltext_action = inserted
  archive_action = inserted
```

Re-run the same command; expected: `status=noop`, `archive_action=noop` (idempotency confirmed).

### Step 4: Smoke deployed workflow (optional, requires cluster)

Run:

```bash
just overlay::smoke-archive-papers
```

Expected: JSON response with `"status": "completed"` and per-paper results.

### Step 5: Final commit (only if anything tweaked during smokes)

If smokes pass cleanly with no fixes, no commit needed. If you had to adjust anything, commit it:

```bash
git add .
git commit -m "fix(s2): <specific smoke fix>"
```

---

## Self-Review

This plan's self-review pass against the research findings:

**1. Spec coverage** — every requirement from the user's prompt has at least one task:

| Requirement | Task(s) |
|-------------|---------|
| "Modeled off of the archive tool" (`.centaur/tools/research/archiver/`) | Tasks 1, 2 (split fetch/parse modules) + Task 6 (`client.archive_paper` orchestration mirrors `archiver.extract_source`) + return-shape convention `{status, ...}` throughout |
| Use AI-Scientist-v2 PDF formatting (`.scientist/`) | Task 1 (pymupdf4llm → pymupdf → pypdf fallback chain with 100-char min_size — copied directly from `perform_llm_review.load_paper`) |
| Extend our overlay (`overlay/tools/semantic_scholar/`) | Task 6 adds `archive_paper` to existing `SemanticScholarClient`; Task 5 adds the 3 new deps without breaking existing ones |
| "Download and store documents" | Task 3 migration + Task 4 `paper_archives` DAO stores raw bytes; Task 4 `build_fulltext_document` stores parsed text in `company_context_documents` |
| Deps needed | Task 5 adds `pymupdf>=1.24`, `pymupdf4llm>=0.0.17`, `pypdf>=4.0.0` |

**2. Placeholder scan** — no TBDs, no "implement later". Every step that changes code shows the code or the exact diff. Type names (e.g. `PdfParseError`, `PdfFetchError`, `PdfTooLargeError`, `build_fulltext_document`, `compute_pdf_sha256`, `upsert_paper_archive`, `_archive_paper_async`, `_acquire_pool_for_archive`) are consistent across all tasks.

**3. Type consistency** — confirmed:
- `parse_pdf_to_markdown(data: bytes, min_size: int = 100) -> tuple[str, str]` in Task 1 → consumed in Task 6 with `(parsed_text, parser_used) = await asyncio.to_thread(pdf_parse.parse_pdf_to_markdown, data, min_size=ARCHIVE_PARSER_MIN_SIZE)`
- `download_pdf(...) -> tuple[bytes, str]` in Task 2 → consumed in Task 6 with `(data, mime) = await asyncio.to_thread(pdf_fetch.download_pdf, ...)`
- `derive_pdf_url(paper: dict) -> str | None` in Task 2 → consumed in Task 6's `_archive_paper_async`
- `build_fulltext_document(paper, *, parsed_text, parent_document_id, parser_used, truncated, pdf_sha256, source_url=None)` in Task 4 → consumed identically in Task 6
- `upsert_paper_archive(pool, row)` in Task 4 → consumed identically in Task 6
- Return envelopes everywhere use the literal strings `"completed"`, `"noop"`, `"skipped"`, `"error"` — no drift

**4. Open risks (documented, not gaps)**:
- pymupdf may need source build on some platforms. Mitigation: Task 5 step 4 verifies wheel availability. If a non-glibc Linux variant later breaks the build, switch to `pypdf` as the only parser (the fallback chain already handles this).
- `paper_archives.pdf_bytes BYTEA` rows can grow large at scale (50 MiB × N papers). For v1 this is acceptable; if storage becomes a concern, a follow-up plan can offload to S3 with a `pdf_url` column. Out of scope here.
- Source URL coverage is limited to open-access. Paywalled papers stay `skipped` — explicit by spec decision #3.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-26-semantic-scholar-paper-archive.md`. Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session using `superpowers:executing-plans`, batch execution with checkpoints.

Which approach?
