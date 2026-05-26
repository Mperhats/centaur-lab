"""Unit tests for the ``research_brief`` workflow handler.

After the bundle refactor the handler is a thin persistence layer
around ``SemanticScholarClient.research_brief``: it receives a fully-
projected bundle (``brief_doc`` plus ``paper_docs`` already parented
under the brief) and persists each row via the inlined
``_upsert_document`` helper.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
import research_brief

from tests._helpers import MockContext, MockPool


def _ok_bundle(
    *,
    query: str = "active inference",
    paper_count: int = 2,
) -> dict[str, Any]:
    brief_doc_id = "semantic_scholar:research_brief:deadbeef"
    brief_doc = {
        "document_id": brief_doc_id,
        "source": "semantic_scholar",
        "source_type": "research_brief",
        "source_document_id": "deadbeef",
        "source_chunk_id": "",
        "parent_document_id": None,
        "title": f"Research Brief: {query}",
        "body": f"# {query}",
        "url": "",
        "author_id": "",
        "author_name": "",
        "access_scope": "company",
        "occurred_at": None,
        "source_updated_at": None,
        "content_hash": "hash-brief",
        "metadata": {"query": query, "year_from": None},
    }
    paper_docs = [
        {
            "document_id": f"semantic_scholar:paper:p{i}",
            "source": "semantic_scholar",
            "source_type": "paper",
            "source_document_id": f"p{i}",
            "source_chunk_id": "",
            "parent_document_id": brief_doc_id,
            "title": f"Paper {i}",
            "body": f"# Paper {i}",
            "url": f"https://www.semanticscholar.org/paper/p{i}",
            "author_id": "a1",
            "author_name": "Author One",
            "access_scope": "company",
            "occurred_at": None,
            "source_updated_at": None,
            "content_hash": f"hash-paper-{i}",
            "metadata": {"paperId": f"p{i}"},
        }
        for i in range(1, paper_count + 1)
    ]
    return {
        "status": "ok",
        "query": query,
        "year_from": None,
        "limit": paper_count,
        "results_count": paper_count,
        "markdown": f"# {query}",
        "brief_doc": brief_doc,
        "paper_docs": paper_docs,
    }


@pytest.mark.asyncio
async def test_handler_skips_when_query_empty() -> None:
    pool = MockPool()
    ctx = MockContext(pool)

    with patch("research_brief.SemanticScholarClient") as mock_cls:
        result = await research_brief.handler(research_brief.Input(query="   "), ctx)

    assert result == {"status": "skipped", "reason": "empty_query"}
    assert mock_cls.call_count == 0
    assert pool.execute_calls == []


@pytest.mark.asyncio
async def test_handler_skips_when_limit_non_positive() -> None:
    pool = MockPool()
    ctx = MockContext(pool)

    with patch("research_brief.SemanticScholarClient") as mock_cls:
        result = await research_brief.handler(
            research_brief.Input(query="anything", limit=0), ctx
        )

    assert result == {"status": "skipped", "reason": "invalid_limit"}
    assert mock_cls.call_count == 0
    assert pool.execute_calls == []


@pytest.mark.asyncio
async def test_handler_persists_brief_and_papers() -> None:
    """Bundle → 1 brief upsert + N paper upserts, all inserted."""
    pool = MockPool(existing_hash=None, execute_status="INSERT 0 1")
    ctx = MockContext(pool)
    bundle = _ok_bundle(paper_count=3)

    with patch("research_brief.SemanticScholarClient") as mock_cls:
        mock_cls.return_value.research_brief = AsyncMock(return_value=bundle)
        result = await research_brief.handler(
            research_brief.Input(query="active inference", limit=3),
            ctx,
        )

    assert result["status"] == "completed"
    assert result["brief_document_id"] == bundle["brief_doc"]["document_id"]
    assert result["brief_action"] == "inserted"
    assert result["results_count"] == 3
    assert result["papers_inserted"] == 3
    assert result["papers_updated"] == 0
    assert result["papers_noop"] == 0
    # 1 brief + 3 papers = 4 execute calls
    assert len(pool.execute_calls) == 4


@pytest.mark.asyncio
async def test_handler_propagates_error_bundle() -> None:
    """An error bundle from the client is returned verbatim — no DB writes."""
    pool = MockPool()
    ctx = MockContext(pool)
    bundle = {"status": "error", "query": "x", "error": "S2 down"}

    with patch("research_brief.SemanticScholarClient") as mock_cls:
        mock_cls.return_value.research_brief = AsyncMock(return_value=bundle)
        result = await research_brief.handler(
            research_brief.Input(query="x"),
            ctx,
        )

    assert result == bundle
    assert pool.execute_calls == []


@pytest.mark.asyncio
async def test_handler_brief_and_paper_noop_when_hashes_match() -> None:
    """If every document's compound hash already matches, all upserts noop."""
    bundle = _ok_bundle(paper_count=2)

    # Pre-seed the pool so every fetchval returns the matching compound hash.
    persisted = {
        bundle["brief_doc"]["document_id"]: research_brief._content_hash(
            bundle["brief_doc"]["content_hash"], None
        ),
    }
    for paper_doc in bundle["paper_docs"]:
        persisted[paper_doc["document_id"]] = research_brief._content_hash(
            paper_doc["content_hash"], bundle["brief_doc"]["document_id"]
        )

    class _SeededPool:
        def __init__(self) -> None:
            self.execute_calls: list[tuple[str, tuple[Any, ...]]] = []
            self.fetchval_calls: list[tuple[str, tuple[Any, ...]]] = []

        async def fetchval(self, query: str, *args: Any) -> Any:
            self.fetchval_calls.append((query, args))
            return persisted.get(str(args[0])) if args else None

        async def execute(self, query: str, *args: Any) -> str:
            self.execute_calls.append((query, args))
            return "INSERT 0 1"

    pool = _SeededPool()
    ctx = MockContext(pool)

    with patch("research_brief.SemanticScholarClient") as mock_cls:
        mock_cls.return_value.research_brief = AsyncMock(return_value=bundle)
        result = await research_brief.handler(
            research_brief.Input(query="x", limit=2),
            ctx,
        )

    assert result["brief_action"] == "noop"
    assert result["papers_noop"] == 2
    assert result["papers_inserted"] == 0
    assert pool.execute_calls == []


@pytest.mark.asyncio
async def test_handler_archive_defaults_off_no_child_workflow_dispatched() -> None:
    """``archive=False`` (the default) must not spawn ``archive_papers``."""
    pool = MockPool(existing_hash=None, execute_status="INSERT 0 1")
    ctx = MockContext(pool)
    bundle = _ok_bundle(paper_count=2)

    with patch("research_brief.SemanticScholarClient") as mock_cls:
        mock_cls.return_value.research_brief = AsyncMock(return_value=bundle)
        result = await research_brief.handler(
            research_brief.Input(query="x", limit=2),
            ctx,
        )

    assert ctx.run_workflow_calls == []
    assert "archive_run_id" not in result
    assert "archive" not in result


@pytest.mark.asyncio
async def test_handler_archive_true_dispatches_archive_papers_with_paper_ids() -> None:
    """``archive=True`` chains ``archive_papers`` with the brief's paperIds."""
    pool = MockPool(existing_hash=None, execute_status="INSERT 0 1")
    ctx = MockContext(
        pool,
        run_workflow_response={
            "run_id": "archive-run-7",
            "status": "completed",
            "output_json": {
                "status": "completed",
                "papers_archived": 3,
                "papers_skipped": 0,
                "papers_failed": 0,
            },
        },
    )
    bundle = _ok_bundle(paper_count=3)

    with patch("research_brief.SemanticScholarClient") as mock_cls:
        mock_cls.return_value.research_brief = AsyncMock(return_value=bundle)
        result = await research_brief.handler(
            research_brief.Input(query="x", limit=3, archive=True),
            ctx,
        )

    assert len(ctx.run_workflow_calls) == 1
    step_name, workflow_name, run_input = ctx.run_workflow_calls[0]
    assert workflow_name == "archive_papers"
    assert step_name == "archive"
    assert run_input == {"paper_ids": ["p1", "p2", "p3"]}
    assert result["archive_run_id"] == "archive-run-7"
    assert result["archive"]["status"] == "completed"
    assert result["archive"]["papers_archived"] == 3


@pytest.mark.asyncio
async def test_handler_archive_true_with_zero_papers_skips_child_dispatch() -> None:
    """No papers → no archive run (avoid cluttering parent-child view)."""
    pool = MockPool(existing_hash=None, execute_status="INSERT 0 1")
    ctx = MockContext(pool)
    bundle = _ok_bundle(paper_count=0)

    with patch("research_brief.SemanticScholarClient") as mock_cls:
        mock_cls.return_value.research_brief = AsyncMock(return_value=bundle)
        result = await research_brief.handler(
            research_brief.Input(query="x", limit=5, archive=True),
            ctx,
        )

    assert ctx.run_workflow_calls == []
    assert result["archive_run_id"] is None
    assert result["archive"] == {"status": "skipped", "reason": "no_paper_ids"}


@pytest.mark.asyncio
async def test_handler_archive_true_does_not_dispatch_on_client_error() -> None:
    """If the S2 client returns an error bundle, no archive run is spawned."""
    pool = MockPool()
    ctx = MockContext(pool)
    bundle = {"status": "error", "query": "x", "error": "S2 down"}

    with patch("research_brief.SemanticScholarClient") as mock_cls:
        mock_cls.return_value.research_brief = AsyncMock(return_value=bundle)
        result = await research_brief.handler(
            research_brief.Input(query="x", archive=True),
            ctx,
        )

    assert result == bundle
    assert ctx.run_workflow_calls == []
    assert pool.execute_calls == []


@pytest.mark.asyncio
async def test_handler_passes_query_limit_year_from_to_client() -> None:
    pool = MockPool(existing_hash=None, execute_status="INSERT 0 1")
    ctx = MockContext(pool)
    bundle = _ok_bundle(paper_count=0)
    captured: dict[str, Any] = {}

    async def _research_brief(*, query, limit, year_from):  # type: ignore[no-untyped-def]
        captured["query"] = query
        captured["limit"] = limit
        captured["year_from"] = year_from
        return bundle

    with patch("research_brief.SemanticScholarClient") as mock_cls:
        mock_cls.return_value.research_brief = _research_brief
        await research_brief.handler(
            research_brief.Input(query="  x ", limit=7, year_from=2020),
            ctx,
        )

    assert captured == {"query": "x", "limit": 7, "year_from": 2020}
