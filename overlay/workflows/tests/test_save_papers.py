"""Tests for the ``save_papers`` workflow handler."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
import save_papers

from centaur_lab.paper_document import _content_hash, build_paper_document
from centaur_lab.paper_models import Paper
from centaur_lab.testing import MockContext, MockPool


class MetricsRecorder:
    """Lightweight stand-in for the metrics shim used by tests.

    Records ``observe_document_size`` and ``record_document_change``
    invocations against the same recorder so tests can assert both the
    pre-upsert observation and the post-upsert change record without
    mocking the real Prometheus machinery (which isn't on sys.path
    during local runs anyway). ``observe_calls`` captures pre-upsert
    size observations; ``change_calls`` captures post-upsert
    ``(document, action)`` pairs. ``calls`` is preserved as an alias for
    ``change_calls`` to keep older assertions on
    ``MetricsRecorder.calls`` working without renames.
    """

    def __init__(self) -> None:
        self.observe_calls: list[dict[str, Any]] = []
        self.change_calls: list[tuple[dict[str, Any], str]] = []

    @property
    def calls(self) -> list[tuple[dict[str, Any], str]]:
        return self.change_calls

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
        self.close_called = False
        self.get_paper_calls: list[str] = []

    def get_paper(self, paper_id: str) -> Paper:
        self.get_paper_calls.append(paper_id)
        if paper_id in self._raise_on:
            raise self._raise_on[paper_id]
        if paper_id in self._fail_ids:
            raise RuntimeError(f"S2 API error for {paper_id}")
        return self._papers[paper_id]

    def close(self) -> None:
        self.close_called = True

    def __enter__(self) -> MockS2Client:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


def _paper(paper_id: str, *, title: str = "Sample Paper") -> Paper:
    """Minimal S2-shaped :class:`Paper` sufficient for ``build_paper_document``."""
    return Paper.model_validate(
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
    assert mock.close_called is True


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
    assert mock.close_called is True
    assert any(event == "save_papers_paper_failed" for event, _ in ctx.logs)


@pytest.mark.asyncio
async def test_handler_passes_query_to_metadata() -> None:
    pool = MockPool(existing_hash=None, execute_status="INSERT 0 1")
    ctx = MockContext(pool)
    mock = MockS2Client({"x": _paper("x")})

    with patch("save_papers.SemanticScholarClient") as mock_cls:
        mock_cls.return_value = mock
        await save_papers.handler(
            save_papers.Input(paper_ids=["x"], query="active inference"),
            ctx,
        )

    assert len(pool.execute_calls) == 3
    _query, args = pool.execute_calls[0]
    metadata_json = args[15]
    assert isinstance(metadata_json, str)
    assert '"query":"active inference"' in metadata_json
    assert any(event == "save_papers_brief_persisted" for event, _ in ctx.logs)


@pytest.mark.asyncio
async def test_handler_returns_noop_for_unchanged_papers() -> None:
    paper = _paper("noop-id")
    document = build_paper_document(paper)
    # save_papers calls upsert_document without a parent_document_id kwarg, so
    # the persisted hash combines the intrinsic content_hash with None.
    persisted_hash = _content_hash(document["content_hash"], None)
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
async def test_handler_closes_client_on_exception() -> None:
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

    assert mock.close_called is True


@pytest.mark.asyncio
async def test_handler_emits_vm_metrics_per_upsert(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pool = MockPool(existing_hash=None, execute_status="INSERT 0 1")
    ctx = MockContext(pool)
    mock = MockS2Client({"A": _paper("A"), "B": _paper("B")})
    recorder = MetricsRecorder()
    monkeypatch.setattr(save_papers, "observe_document_size", recorder.observe)
    monkeypatch.setattr(save_papers, "record_document_change", recorder.record)
    import centaur_lab.brief as brief_module

    monkeypatch.setattr(brief_module, "observe_document_size", recorder.observe)
    monkeypatch.setattr(brief_module, "record_document_change", recorder.record)

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
