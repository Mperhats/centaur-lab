"""Tests for the ``save_papers`` workflow handler.

After the tool-vs-workflow refactor, ``save_papers`` is self-contained
— it calls ``client.get_paper`` for each input, projects the paper via
``semantic_scholar.projections`` directly, and persists rows through
its own inlined ``_upsert_document`` helper. There is no ``client``
bundle method involved (saving metadata-only papers is the workflow's
unique responsibility; the tool has no analogous method).

The unit tests exercise the handler end-to-end against the in-memory
``MockPool`` and ``MockContext`` stand-ins. The hash assertions use
the module's own private ``_content_hash`` (not ``centaur_lab``'s
sibling) because both copies hash to the same bytes — they're meant
to drift only when the upstream canonical-JSON spec drifts.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
import save_papers
from semantic_scholar.projections import build_paper_document
from semanticscholar.Paper import Paper

from centaur_lab.testing import MockContext, MockPool


class MetricsRecorder:
    """Lightweight stand-in for the metrics shim used by tests.

    Records ``_observe_document_size`` and ``_record_document_change``
    invocations against the same recorder so tests can assert both the
    pre-upsert observation and the post-upsert change record without
    mocking the real Prometheus machinery (which isn't on sys.path
    during local runs anyway).
    """

    def __init__(self) -> None:
        self.observe_calls: list[dict[str, Any]] = []
        self.change_calls: list[tuple[dict[str, Any], str]] = []

    def observe(self, document: dict[str, Any]) -> None:
        self.observe_calls.append(document)

    def record(self, document: dict[str, Any], action: str) -> None:
        self.change_calls.append((document, action))


class MockS2Client:
    """Stand-in for ``SemanticScholarClient`` used inside the handler."""

    def __init__(
        self,
        papers_by_id: dict[str, Paper],
        *,
        fail_ids: tuple[str, ...] = (),
        raise_on: dict[str, BaseException] | None = None,
    ) -> None:
        self._papers = papers_by_id
        self._fail_ids = set(fail_ids)
        self._raise_on = raise_on or {}
        self.get_paper_calls: list[str] = []

    def get_paper(self, paper_id: str) -> Paper:
        self.get_paper_calls.append(paper_id)
        if paper_id in self._raise_on:
            raise self._raise_on[paper_id]
        if paper_id in self._fail_ids:
            raise RuntimeError(f"S2 API error for {paper_id}")
        return self._papers[paper_id]


def _paper(paper_id: str, *, title: str = "Sample Paper") -> Paper:
    """Minimal S2-shaped :class:`Paper` sufficient for ``build_paper_document``."""
    return Paper(
        {
            "paperId": paper_id,
            "title": title,
            "authors": [{"authorId": "a1", "name": "Test Author"}],
            "year": 2024,
            "abstract": f"Abstract for {paper_id}.",
            "citationCount": 1,
            "url": f"https://www.semanticscholar.org/paper/{paper_id}",
            "openAccessPdf": None,
            "venue": "Test Venue",
            "externalIds": {"DOI": f"10.0/{paper_id}"},
        }
    )


@pytest.mark.asyncio
async def test_handler_skips_when_paper_ids_empty() -> None:
    pool = MockPool()
    ctx = MockContext(pool)

    with patch("save_papers.SemanticScholarClient") as mock_cls:
        result = await save_papers.handler(save_papers.Input(paper_ids=[]), ctx)

    assert result == {"status": "skipped", "reason": "no_paper_ids"}
    assert mock_cls.call_count == 0
    assert pool.fetchval_calls == []
    assert pool.execute_calls == []
    assert any(event == "save_papers_skipped_empty" for event, _ in ctx.logs)


@pytest.mark.asyncio
async def test_handler_inserts_one_paper() -> None:
    pool = MockPool(existing_hash=None, execute_status="INSERT 0 1")
    ctx = MockContext(pool)
    mock = MockS2Client({"abc123": _paper("abc123")})

    with patch("save_papers.SemanticScholarClient") as mock_cls:
        mock_cls.return_value = mock
        result = await save_papers.handler(
            save_papers.Input(paper_ids=["abc123"]),
            ctx,
        )

    assert result["status"] == "completed"
    assert result["papers_inserted"] == 1
    assert result["papers_updated"] == 0
    assert result["papers_noop"] == 0
    assert result["papers_failed"] == 0
    assert result["brief_document_id"].startswith("semantic_scholar:research_brief:")
    assert result["brief_action"] == "inserted"
    assert len(result["results"]) == 1
    entry = result["results"][0]
    assert entry["status"] == "inserted"
    assert entry["document_id"] == "semantic_scholar:paper:abc123"
    assert entry["paperId"] == "abc123"


@pytest.mark.asyncio
async def test_handler_handles_partial_failure() -> None:
    pool = MockPool(existing_hash=None, execute_status="INSERT 0 1")
    ctx = MockContext(pool)
    mock = MockS2Client(
        {"ok1": _paper("ok1"), "ok2": _paper("ok2")},
        fail_ids=("bad",),
    )

    with patch("save_papers.SemanticScholarClient") as mock_cls:
        mock_cls.return_value = mock
        result = await save_papers.handler(
            save_papers.Input(paper_ids=["ok1", "bad", "ok2"]),
            ctx,
        )

    assert result["status"] == "completed"
    assert result["papers_inserted"] == 2
    assert result["papers_failed"] == 1
    failed = [r for r in result["results"] if r.get("status") == "failed"]
    assert len(failed) == 1
    assert failed[0]["paperId"] == "bad"
    assert "S2 API error for bad" in failed[0]["error"]
    assert any(event == "save_papers_paper_failed" for event, _ in ctx.logs)


@pytest.mark.asyncio
async def test_handler_passes_query_to_metadata() -> None:
    """Explicit ``query`` lands in the first per-paper upsert's metadata."""
    pool = MockPool(existing_hash=None, execute_status="INSERT 0 1")
    ctx = MockContext(pool)
    mock = MockS2Client({"x": _paper("x")})

    with patch("save_papers.SemanticScholarClient") as mock_cls:
        mock_cls.return_value = mock
        await save_papers.handler(
            save_papers.Input(paper_ids=["x"], query="active inference"),
            ctx,
        )

    # 1 initial paper upsert + 1 brief + 1 re-parent paper = 3 execute calls.
    assert len(pool.execute_calls) == 3
    _query, args = pool.execute_calls[0]
    metadata_json = args[15]
    assert isinstance(metadata_json, str)
    assert '"query":"active inference"' in metadata_json
    assert any(event == "save_papers_brief_persisted" for event, _ in ctx.logs)


@pytest.mark.asyncio
async def test_handler_returns_noop_for_unchanged_papers() -> None:
    """A paper whose persisted compound hash matches noops on the first upsert.

    The hash assertion uses the workflow module's private
    ``_content_hash`` so the test pins the actual hash function in use,
    not a parallel copy elsewhere — keeps drift in either direction
    visible.
    """
    paper = _paper("noop-id")
    document = build_paper_document(paper)
    persisted_hash = save_papers._content_hash(document["content_hash"], None)
    pool = MockPool(existing_hash=persisted_hash)
    ctx = MockContext(pool)
    mock = MockS2Client({"noop-id": paper})

    with patch("save_papers.SemanticScholarClient") as mock_cls:
        mock_cls.return_value = mock
        result = await save_papers.handler(
            save_papers.Input(paper_ids=["noop-id"]),
            ctx,
        )

    assert result["papers_noop"] == 1
    assert result["papers_inserted"] == 0
    assert "brief_document_id" in result
    assert result["brief_action"] in ("inserted", "updated", "noop")
    assert len(pool.execute_calls) >= 1


@pytest.mark.asyncio
async def test_handler_propagates_unexpected_exceptions() -> None:
    """Programming-error exceptions (i.e. not ``RuntimeError`` from the
    S2 API) propagate so the workflow run is marked failed; the
    handler doesn't swallow them into a ``"failed"`` result entry.
    """
    pool = MockPool()
    ctx = MockContext(pool)
    mock = MockS2Client(
        {},
        raise_on={"boom": ValueError("unexpected client failure")},
    )

    with patch("save_papers.SemanticScholarClient") as mock_cls:
        mock_cls.return_value = mock
        with pytest.raises(ValueError, match="unexpected client failure"):
            await save_papers.handler(
                save_papers.Input(paper_ids=["boom"]),
                ctx,
            )


@pytest.mark.asyncio
async def test_handler_emits_vm_metrics_per_upsert(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Observe + record fire once per upserted document (initial + brief + re-parent)."""
    pool = MockPool(existing_hash=None, execute_status="INSERT 0 1")
    ctx = MockContext(pool)
    mock = MockS2Client({"A": _paper("A"), "B": _paper("B")})
    recorder = MetricsRecorder()
    # Patch the document-shaped helpers (not the low-level
    # ``_observe_document_size`` shim) — the handler always goes through
    # ``_observe_doc_size`` / ``_record_doc_change``, so this is where
    # the document-level observability contract lives.
    monkeypatch.setattr(save_papers, "_observe_doc_size", recorder.observe)
    monkeypatch.setattr(save_papers, "_record_doc_change", recorder.record)

    with patch("save_papers.SemanticScholarClient") as mock_cls:
        mock_cls.return_value = mock
        await save_papers.handler(
            save_papers.Input(paper_ids=["A", "B"]),
            ctx,
        )

    # observe: initial paper upserts + brief + re-parent upserts
    assert len(recorder.observe_calls) == 5
    assert len(recorder.change_calls) == 5
    paper_observes = [d for d in recorder.observe_calls if d["source_type"] == "paper"]
    brief_observes = [d for d in recorder.observe_calls if d["source_type"] == "research_brief"]
    assert len(paper_observes) == 4
    assert len(brief_observes) == 1
    for document, action in recorder.change_calls:
        assert document["source"] == "semantic_scholar"
        assert action == "inserted"
