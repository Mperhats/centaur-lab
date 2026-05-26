"""Integration tests for save_papers — real DB, mocked S2 client.

Mirrors the shape of .centaur/services/api/tests/test_company_context_documents.py:
seed nothing (paper workflow doesn't depend on slack tables), call the workflow
handler with a FakeContext wrapping the real db_pool, then assert on the rows
that actually landed in company_context_documents.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

# Make the workflow modules importable. Mirrors the sys.path bootstrap in the
# unit-test conftest.
_WORKFLOWS_DIR = Path(__file__).resolve().parent.parent.parent
if str(_WORKFLOWS_DIR) not in sys.path:
    sys.path.insert(0, str(_WORKFLOWS_DIR))

from tests._fakes import FakeContext  # noqa: E402

_PAPER_173BA: dict[str, Any] = {
    "paperId": "173ba8ae4582b6f9f6919aa3f813579a5349f1f9",
    "title": "Attention Is All You Need",
    "abstract": "The dominant sequence transduction models...",
    "year": 2017,
    "authors": [
        {"authorId": "1", "name": "Ashish Vaswani"},
    ],
    "venue": "NeurIPS",
    "url": "https://example.com/paper",
    "externalIds": {"DOI": "10.5555/example"},
    "citationCount": 100000,
    "referenceCount": 50,
}


_PAPER_OTHER: dict[str, Any] = {
    "paperId": "ffffffffffffffffffffffffffffffffffffffff",
    "title": "Another Paper",
    "abstract": "Another abstract.",
    "year": 2024,
    "authors": [{"authorId": "2", "name": "Jane Doe"}],
    "venue": "arXiv",
    "url": "https://example.com/other",
    "externalIds": {},
    "citationCount": 1,
    "referenceCount": 5,
}


class _StubS2Client:
    """Stub Semantic Scholar client returning canned papers by id.

    Mirrors the unit-test stub pattern. ``get_paper`` returns a dict for known
    ids and raises for unknown ones (so error-path tests are explicit).
    """

    def __init__(self, papers_by_id: dict[str, dict[str, Any]]) -> None:
        self._papers = papers_by_id

    def get_paper(self, paper_id: str, fields: Any = None) -> dict[str, Any]:
        if paper_id not in self._papers:
            raise RuntimeError(f"unknown paper id in stub: {paper_id}")
        return dict(self._papers[paper_id])

    def close(self) -> None:
        pass


@pytest.mark.asyncio
async def test_save_papers_writes_paper_row_with_full_shape(
    db_pool: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    import save_papers

    monkeypatch.setattr(
        save_papers,
        "SemanticScholarClient",
        lambda: _StubS2Client({_PAPER_173BA["paperId"]: _PAPER_173BA}),
    )

    result = await save_papers.handler(
        save_papers.Input(paper_ids=[_PAPER_173BA["paperId"]], query="attention"),
        FakeContext(db_pool),
    )

    assert result["status"] == "completed"
    assert result["papers_inserted"] == 1
    assert result["papers_updated"] == 0
    assert result["papers_failed"] == 0

    rows = await db_pool.fetch(
        "SELECT document_id, source, source_type, title, body, url, "
        "author_name, content_hash, parent_document_id, metadata "
        "FROM company_context_documents",
    )
    assert len(rows) == 1
    row = rows[0]
    assert row["source"] == "semantic_scholar"
    assert row["source_type"] == "paper"
    assert row["document_id"] == f"semantic_scholar:paper:{_PAPER_173BA['paperId']}"
    assert "Attention Is All You Need" in row["title"]
    assert "Ashish Vaswani" in row["body"] or "Ashish Vaswani" in row["author_name"]
    assert row["url"] == _PAPER_173BA["url"]
    assert row["content_hash"]  # non-empty
    assert row["parent_document_id"] is None
    metadata = json.loads(row["metadata"])
    assert metadata["paperId"] == _PAPER_173BA["paperId"]
    assert metadata["year"] == 2017


@pytest.mark.asyncio
async def test_save_papers_is_idempotent_on_rerun(
    db_pool: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    import save_papers

    monkeypatch.setattr(
        save_papers,
        "SemanticScholarClient",
        lambda: _StubS2Client({_PAPER_173BA["paperId"]: _PAPER_173BA}),
    )
    inp = save_papers.Input(paper_ids=[_PAPER_173BA["paperId"]])

    first = await save_papers.handler(inp, FakeContext(db_pool))
    assert first["papers_inserted"] == 1
    assert first["papers_noop"] == 0

    second = await save_papers.handler(inp, FakeContext(db_pool))
    assert second["papers_inserted"] == 0
    assert second["papers_updated"] == 0
    assert second["papers_noop"] == 1

    count = await db_pool.fetchval("SELECT COUNT(*) FROM company_context_documents")
    assert count == 1


@pytest.mark.asyncio
async def test_save_papers_partial_failure_writes_successful_papers(
    db_pool: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    import save_papers

    monkeypatch.setattr(
        save_papers,
        "SemanticScholarClient",
        lambda: _StubS2Client({_PAPER_173BA["paperId"]: _PAPER_173BA}),
    )

    result = await save_papers.handler(
        save_papers.Input(
            paper_ids=[
                _PAPER_173BA["paperId"],
                "deadbeef" * 5,  # not in stub → raises
            ]
        ),
        FakeContext(db_pool),
    )

    assert result["papers_inserted"] == 1
    assert result["papers_failed"] == 1

    count = await db_pool.fetchval("SELECT COUNT(*) FROM company_context_documents")
    assert count == 1
