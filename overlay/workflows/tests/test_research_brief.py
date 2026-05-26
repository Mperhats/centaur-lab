"""Tests for the ``research_brief`` workflow handler and pure helpers."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import research_brief

from ._fakes import EXECUTE_ARG_INDEX, FakeContext, FakePool, MetricsRecorder


class FakeS2Client:
    """Stand-in for ``SemanticScholarClient`` recording calls and forcing failures."""

    def __init__(
        self,
        results: list[dict[str, Any]],
        *,
        raise_exc: BaseException | None = None,
    ) -> None:
        self._results = results
        self._raise_exc = raise_exc
        self.close_called = False
        self.search_calls: list[dict[str, Any]] = []

    def search_papers(
        self,
        query: str,
        limit: int = 10,
        year_from: int | None = None,
        fields: str | None = None,
    ) -> list[dict[str, Any]]:
        self.search_calls.append(
            {"query": query, "limit": limit, "year_from": year_from, "fields": fields}
        )
        if self._raise_exc is not None:
            raise self._raise_exc
        return self._results

    def close(self) -> None:
        self.close_called = True


def _paper(paper_id: str, *, title: str | None = None) -> dict[str, Any]:
    return {
        "paperId": paper_id,
        "title": title or f"Paper {paper_id}",
        "authors": [{"authorId": f"a-{paper_id}", "name": f"Author {paper_id}"}],
        "year": 2024,
        "abstract": f"Abstract for {paper_id}.",
        "citationCount": 7,
        "url": f"https://www.semanticscholar.org/paper/{paper_id}",
        "openAccessPdf": None,
        "venue": "Test Venue",
        "externalIds": {"DOI": f"10.0/{paper_id}"},
    }


@pytest.mark.asyncio
async def test_handler_skips_when_query_empty() -> None:
    pool = FakePool()
    ctx = FakeContext(pool)

    with patch("research_brief.SemanticScholarClient") as mock_cls:
        result = await research_brief.handler(research_brief.Input(query="   "), ctx)

    assert result == {"status": "skipped", "reason": "empty_query"}
    assert mock_cls.call_count == 0
    assert pool.fetchval_calls == []
    assert pool.execute_calls == []
    assert any(event == "research_brief_skipped_empty_query" for event, _ in ctx.logs)


@pytest.mark.asyncio
async def test_handler_clamps_limit_to_max_20() -> None:
    pool = FakePool()
    ctx = FakeContext(pool)
    fake = FakeS2Client(results=[])

    with patch("research_brief.SemanticScholarClient") as mock_cls:
        mock_cls.return_value = fake
        await research_brief.handler(
            research_brief.Input(query="x", limit=999),
            ctx,
        )

    assert len(fake.search_calls) == 1
    assert fake.search_calls[0]["limit"] == 20


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_limit", [0, -5])
async def test_handler_skips_when_limit_non_positive(bad_limit: int) -> None:
    pool = FakePool()
    ctx = FakeContext(pool)

    with patch("research_brief.SemanticScholarClient") as mock_cls:
        result = await research_brief.handler(
            research_brief.Input(query="x", limit=bad_limit),
            ctx,
        )

    assert result == {"status": "skipped", "reason": "invalid_limit"}
    assert mock_cls.call_count == 0
    assert pool.fetchval_calls == []
    assert pool.execute_calls == []
    assert any(event == "research_brief_skipped_invalid_limit" for event, _ in ctx.logs)


@pytest.mark.asyncio
async def test_handler_logs_when_limit_clamped() -> None:
    pool = FakePool()
    ctx = FakeContext(pool)
    fake = FakeS2Client(results=[])

    with patch("research_brief.SemanticScholarClient") as mock_cls:
        mock_cls.return_value = fake
        await research_brief.handler(
            research_brief.Input(query="x", limit=999),
            ctx,
        )

    assert len(fake.search_calls) == 1
    assert fake.search_calls[0]["limit"] == 20
    clamp_events = [
        kwargs for event, kwargs in ctx.logs if event == "research_brief_limit_clamped"
    ]
    assert len(clamp_events) == 1
    assert clamp_events[0]["requested"] == 999
    assert clamp_events[0]["used"] == 20


@pytest.mark.asyncio
async def test_handler_renders_no_results_brief_and_returns_zero_counts() -> None:
    pool = FakePool()
    ctx = FakeContext(pool)
    fake = FakeS2Client(results=[])

    with patch("research_brief.SemanticScholarClient") as mock_cls:
        mock_cls.return_value = fake
        result = await research_brief.handler(
            research_brief.Input(query="quantum gravity"),
            ctx,
        )

    assert result["status"] == "completed"
    assert result["results_count"] == 0
    assert result["papers_inserted"] == 0
    assert result["papers_updated"] == 0
    assert result["papers_noop"] == 0
    assert "No papers found for this query." in result["markdown"]
    # The brief itself is still upserted so reruns of an empty-result query
    # short-circuit to "noop" via content_hash.
    assert len(pool.execute_calls) == 1
    assert any(event == "research_brief_no_results" for event, _ in ctx.logs)
    assert fake.close_called is True


@pytest.mark.asyncio
async def test_handler_persists_brief_and_papers_with_parent_link() -> None:
    pool = FakePool()
    ctx = FakeContext(pool)
    papers = [_paper("p1"), _paper("p2"), _paper("p3")]
    fake = FakeS2Client(results=papers)

    with patch("research_brief.SemanticScholarClient") as mock_cls:
        mock_cls.return_value = fake
        result = await research_brief.handler(
            research_brief.Input(query="active inference"),
            ctx,
        )

    assert result["status"] == "completed"
    assert result["results_count"] == 3
    assert result["papers_inserted"] == 3

    assert len(pool.execute_calls) == 4
    brief_call = pool.execute_calls[0]
    brief_document_id = brief_call[1][EXECUTE_ARG_INDEX["document_id"]]
    assert brief_document_id == result["brief_document_id"]

    parent_idx = EXECUTE_ARG_INDEX["parent_document_id"]
    for paper_call in pool.execute_calls[1:]:
        assert paper_call[1][parent_idx] == brief_document_id


def test_handler_brief_document_id_stable_for_same_query() -> None:
    first = research_brief._brief_id_for("active inference world models", 2023)
    second = research_brief._brief_id_for("active inference world models", 2023)
    assert first == second


def test_handler_brief_document_id_changes_when_year_from_changes() -> None:
    a = research_brief._brief_id_for("graph neural networks", 2020)
    b = research_brief._brief_id_for("graph neural networks", 2023)
    assert a != b


def test_handler_brief_document_id_case_insensitive() -> None:
    upper = research_brief._brief_id_for("Active Inference", None)
    lower = research_brief._brief_id_for("active inference", None)
    assert upper == lower


def test_render_brief_markdown_shape() -> None:
    long_abstract = "x" * 600
    papers = [
        {
            "paperId": "first",
            "title": "First Title",
            "authors": [{"authorId": "1", "name": "Alice"}],
            "year": 2024,
            "abstract": long_abstract,
            "citationCount": 10,
            "url": "https://example.com/first",
        },
        {
            "paperId": "second",
            "title": "Second Title",
            "authors": [{"authorId": "2", "name": "Bob"}],
            "year": 2023,
            "abstract": "Short abstract.",
            "citationCount": 0,
            "url": "https://example.com/second",
        },
    ]

    md = research_brief._render_brief("test query", 2020, papers)

    assert "# Research Brief: test query" in md
    assert "### 1. First Title" in md
    assert "### 2. Second Title" in md
    assert "---" in md
    truncated = "x" * 500 + "..."
    assert truncated in md


@pytest.mark.asyncio
async def test_handler_passes_query_to_paper_metadata() -> None:
    pool = FakePool()
    ctx = FakeContext(pool)
    fake = FakeS2Client(results=[_paper("p1")])

    with patch("research_brief.SemanticScholarClient") as mock_cls:
        mock_cls.return_value = fake
        await research_brief.handler(
            research_brief.Input(query="topic X"),
            ctx,
        )

    assert len(pool.execute_calls) == 2
    paper_call = pool.execute_calls[1]
    metadata_json = paper_call[1][EXECUTE_ARG_INDEX["metadata"]]
    assert isinstance(metadata_json, str)
    payload = json.loads(metadata_json)
    assert payload["query"] == "topic X"


@pytest.mark.asyncio
async def test_handler_closes_client_when_search_raises() -> None:
    pool = FakePool()
    ctx = FakeContext(pool)
    fake = FakeS2Client(results=[], raise_exc=RuntimeError("S2 down"))

    with patch("research_brief.SemanticScholarClient") as mock_cls:
        mock_cls.return_value = fake
        with pytest.raises(RuntimeError, match="S2 down"):
            await research_brief.handler(
                research_brief.Input(query="anything"),
                ctx,
            )

    assert fake.close_called is True


@pytest.mark.asyncio
async def test_handler_emits_vm_metrics_for_brief_and_papers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pool = FakePool()
    ctx = FakeContext(pool)
    fake = FakeS2Client(results=[_paper("p1"), _paper("p2")])
    recorder = MetricsRecorder()
    monkeypatch.setattr(research_brief, "emit_document_metrics", recorder)

    with patch("research_brief.SemanticScholarClient") as mock_cls:
        mock_cls.return_value = fake
        await research_brief.handler(
            research_brief.Input(query="active inference"),
            ctx,
        )

    assert len(recorder.calls) == 3
    source_types = [doc["source_type"] for doc, _ in recorder.calls]
    assert source_types.count("research_brief") == 1
    assert source_types.count("paper") == 2


@pytest.mark.asyncio
async def test_handler_emits_vm_metrics_on_no_results_brief(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pool = FakePool()
    ctx = FakeContext(pool)
    fake = FakeS2Client(results=[])
    recorder = MetricsRecorder()
    monkeypatch.setattr(research_brief, "emit_document_metrics", recorder)

    with patch("research_brief.SemanticScholarClient") as mock_cls:
        mock_cls.return_value = fake
        await research_brief.handler(
            research_brief.Input(query="quantum gravity"),
            ctx,
        )

    assert len(recorder.calls) == 1
    assert recorder.calls[0][0]["source_type"] == "research_brief"
    assert recorder.calls[0][1] == "inserted"
