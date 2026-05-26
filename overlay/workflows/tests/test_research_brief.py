"""Delegation tests for the ``research_brief`` workflow wrapper.

The inline S2-search → render → upsert flow now lives on
``SemanticScholarClient.research_brief`` and is covered by its own
21-test unit suite under
``overlay/tools/semantic_scholar/tests/``. These tests only verify
the wrapper's three responsibilities:

1. Forwards ``Input`` fields to the tool method as kwargs.
2. Translates the tool's two input-validation ``"error"`` envelopes
   back to the workflow's pre-existing ``"skipped"`` contract.
3. Closes the client via the context-manager protocol whether the
   tool method returns success or error.
"""

from __future__ import annotations

from typing import Any

import pytest
import research_brief

from ._mocks import MockContext, MockPool


class MockSemanticScholarClient:
    """Minimal stand-in for ``SemanticScholarClient`` recording delegation.

    Implements the context-manager protocol plus the single
    ``research_brief`` method the workflow wrapper calls. Each instance
    captures the kwargs it was invoked with and tracks whether the
    context-manager exit ran, so tests can assert both the delegation
    contract and the close-on-error invariant.
    """

    def __init__(self, *, return_value: dict[str, Any]) -> None:
        self._return_value = return_value
        self.research_brief_calls: list[dict[str, Any]] = []
        self.entered = False
        self.exited = False

    def __enter__(self) -> MockSemanticScholarClient:
        self.entered = True
        return self

    def __exit__(self, *args: object) -> None:
        self.exited = True

    def research_brief(
        self,
        *,
        query: str,
        limit: int,
        year_from: int | None,
    ) -> dict[str, Any]:
        self.research_brief_calls.append(
            {"query": query, "limit": limit, "year_from": year_from}
        )
        return self._return_value


def _install_mock_client(
    monkeypatch: pytest.MonkeyPatch, mock: MockSemanticScholarClient
) -> None:
    """Swap ``research_brief.SemanticScholarClient`` with a factory for ``mock``."""
    monkeypatch.setattr(
        research_brief,
        "SemanticScholarClient",
        lambda: mock,
    )


@pytest.mark.asyncio
async def test_research_brief_delegates_to_tool_method(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Wrapper forwards Input fields and passes the success dict through, minus markdown.

    S8: the workflow handler strips ``markdown`` from the dict it
    returns so the persisted ``workflow_runs.output_json`` envelope
    stays compact (the brief body is recoverable via
    ``brief_document_id``). Direct callers of
    ``SemanticScholarClient.research_brief`` still get the markdown
    inline; only the workflow-handler return drops it.
    """
    tool_return = {
        "status": "completed",
        "brief_document_id": "semantic_scholar:research_brief:abc",
        "brief_action": "inserted",
        "results_count": 2,
        "papers_inserted": 2,
        "papers_updated": 0,
        "papers_noop": 0,
        "markdown": "# Research Brief: active inference\n",
    }
    mock = MockSemanticScholarClient(return_value=tool_return)
    _install_mock_client(monkeypatch, mock)
    ctx = MockContext(MockPool())

    result = await research_brief.handler(
        research_brief.Input(query="active inference", limit=3, year_from=2024),
        ctx,
    )

    expected_envelope = {k: v for k, v in tool_return.items() if k != "markdown"}
    assert result == expected_envelope
    assert "markdown" not in result
    assert mock.research_brief_calls == [
        {"query": "active inference", "limit": 3, "year_from": 2024}
    ]


@pytest.mark.asyncio
async def test_research_brief_translates_empty_query_error_to_skipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock = MockSemanticScholarClient(
        return_value={"status": "error", "error": "query cannot be empty"}
    )
    _install_mock_client(monkeypatch, mock)
    ctx = MockContext(MockPool())

    result = await research_brief.handler(
        research_brief.Input(query="   "),
        ctx,
    )

    assert result == {"status": "skipped", "reason": "empty_query"}


@pytest.mark.asyncio
async def test_research_brief_translates_invalid_limit_error_to_skipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock = MockSemanticScholarClient(
        return_value={"status": "error", "error": "limit must be positive"}
    )
    _install_mock_client(monkeypatch, mock)
    ctx = MockContext(MockPool())

    result = await research_brief.handler(
        research_brief.Input(query="x", limit=0),
        ctx,
    )

    assert result == {"status": "skipped", "reason": "invalid_limit"}


@pytest.mark.asyncio
async def test_research_brief_passes_through_other_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Errors outside the two soft-skip cases pass through unchanged."""
    envelope = {
        "status": "error",
        "error": "DATABASE_URL is required for semantic_scholar.research_brief",
    }
    mock = MockSemanticScholarClient(return_value=envelope)
    _install_mock_client(monkeypatch, mock)
    ctx = MockContext(MockPool())

    result = await research_brief.handler(
        research_brief.Input(query="anything"),
        ctx,
    )

    assert result == envelope


@pytest.mark.asyncio
async def test_research_brief_closes_client_on_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock = MockSemanticScholarClient(return_value={"status": "completed", "results_count": 0})
    _install_mock_client(monkeypatch, mock)
    ctx = MockContext(MockPool())

    await research_brief.handler(research_brief.Input(query="anything"), ctx)

    assert mock.entered is True
    assert mock.exited is True


@pytest.mark.asyncio
async def test_research_brief_closes_client_on_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock = MockSemanticScholarClient(return_value={"status": "error", "error": "S2 down"})
    _install_mock_client(monkeypatch, mock)
    ctx = MockContext(MockPool())

    await research_brief.handler(research_brief.Input(query="anything"), ctx)

    assert mock.entered is True
    assert mock.exited is True
