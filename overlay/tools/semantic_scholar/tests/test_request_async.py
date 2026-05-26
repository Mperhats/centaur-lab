"""Tests for the async retry path: ``_request_async`` + ``_search_async``.

These tests pin the behavior added for review.md A5: the live top-up
inside ``_search_async`` must not block the asyncio event loop on retry
backoff. Two pieces of evidence:

1. ``_request_async`` directly: a mocked ``httpx.AsyncClient`` returns
   429 then 200; we patch both ``asyncio.sleep`` and ``time.sleep`` and
   assert the awaitable variant was awaited and the blocking variant
   was never touched.
2. ``_search_async`` end-to-end: same 429 → 200 response sequence, but
   driven through the public ``search`` entrypoint (which wraps
   ``_search_async`` in ``asyncio.run``); asserts the returned envelope
   contains the success payload and that ``time.sleep`` was never
   called.

Mocks live inline (no shared fixture file) so this file stays a
drop-in template for future async retry helpers that follow the same
sync/async dual-variant pattern.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import asyncpg
import httpx
import pytest

import semantic_scholar.client as s2_client
from semantic_scholar.client import SemanticScholarClient

# ---------------------------------------------------------------------------
# httpx.AsyncClient stand-in
# ---------------------------------------------------------------------------


class _MockResponse:
    """Minimal ``httpx.Response`` substitute supporting the fields
    ``_request`` / ``_request_async`` touch: ``status_code``, ``json``,
    ``raise_for_status``, ``request``, and ``text``."""

    def __init__(
        self,
        status_code: int,
        json_data: dict[str, Any] | None = None,
        text: str = "",
    ) -> None:
        self.status_code = status_code
        self._json = json_data if json_data is not None else {}
        self.text = text
        # Construct a real httpx.Request so ``HTTPStatusError`` carries a
        # well-formed request (matches what ``_request_async`` does when
        # synthesizing the transient 4xx/5xx error).
        self.request = httpx.Request("GET", "https://test.invalid/path")

    def json(self) -> dict[str, Any]:
        return self._json

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"status {self.status_code}",
                request=self.request,
                response=self,  # type: ignore[arg-type]
            )


class _MockAsyncClient:
    """``httpx.AsyncClient`` stand-in that pops responses off a list.

    Supports the ``async with`` protocol the production code uses; each
    ``get`` call records ``(url, params, headers)`` so tests can assert
    the request shape after the retry loop drains.
    """

    def __init__(self, responses: list[_MockResponse]) -> None:
        self._responses = list(responses)
        self.get_calls: list[dict[str, Any]] = []
        self.aenter_count = 0
        self.aexit_count = 0

    async def __aenter__(self) -> _MockAsyncClient:
        self.aenter_count += 1
        return self

    async def __aexit__(self, *_args: object) -> None:
        self.aexit_count += 1

    async def get(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> _MockResponse:
        self.get_calls.append({"url": url, "params": params, "headers": headers})
        if not self._responses:
            raise RuntimeError("MockAsyncClient: ran out of responses")
        return self._responses.pop(0)


def _install_mock_async_client(
    monkeypatch: pytest.MonkeyPatch, responses: list[_MockResponse]
) -> _MockAsyncClient:
    """Patch ``httpx.AsyncClient`` so ``_request_async`` constructs ours.

    Returns the singleton ``_MockAsyncClient`` so tests can inspect the
    recorded ``get_calls`` after the retry loop exits.
    """
    mock = _MockAsyncClient(responses)

    def _factory(**_kwargs: Any) -> _MockAsyncClient:
        return mock

    monkeypatch.setattr(s2_client.httpx, "AsyncClient", _factory)
    return mock


def _install_sleep_recorders(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[list[float], list[float]]:
    """Capture every ``asyncio.sleep`` and ``time.sleep`` call.

    The async mock yields to the loop (so retries actually progress)
    without burning wall-clock time. ``time.sleep`` is never expected
    to fire from the async retry path; recording it lets the test fail
    loudly if a regression slips the blocking call back in.
    """
    asyncio_sleep_calls: list[float] = []
    time_sleep_calls: list[float] = []

    real_asyncio_sleep = asyncio.sleep

    async def _mock_asyncio_sleep(seconds: float) -> None:
        asyncio_sleep_calls.append(seconds)
        # Yield control to the event loop without an actual delay so the
        # retry loop keeps making progress in tests.
        await real_asyncio_sleep(0)

    def _mock_time_sleep(seconds: float) -> None:
        time_sleep_calls.append(seconds)

    monkeypatch.setattr(s2_client.asyncio, "sleep", _mock_asyncio_sleep)
    monkeypatch.setattr(s2_client.time, "sleep", _mock_time_sleep)
    return asyncio_sleep_calls, time_sleep_calls


# ---------------------------------------------------------------------------
# 1. _request_async: retries via await asyncio.sleep, never time.sleep
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_request_async_awaits_asyncio_sleep_on_429(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 429 → 200 sequence must await ``asyncio.sleep`` for backoff and
    never call the blocking ``time.sleep``."""
    mock_client = _install_mock_async_client(
        monkeypatch,
        [
            _MockResponse(429),
            _MockResponse(200, {"data": [{"paperId": "p1"}]}),
        ],
    )
    asyncio_sleeps, time_sleeps = _install_sleep_recorders(monkeypatch)

    client = SemanticScholarClient(api_key="")
    result = await client._request_async(
        "/paper/search", params={"query": "x", "limit": 10}
    )

    assert result == {"data": [{"paperId": "p1"}]}
    assert len(mock_client.get_calls) == 2
    # backoff = min(8.0, 2**0) on the first retry attempt
    assert asyncio_sleeps == [1.0]
    assert time_sleeps == []
    # async-with lifecycle was honored on the per-call client
    assert mock_client.aenter_count == 1
    assert mock_client.aexit_count == 1


# ---------------------------------------------------------------------------
# 2. _request_async: retries on transient 5xx the same way as 429
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_request_async_retries_transient_5xx(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """503 (Service Unavailable) is in the transient set; same retry shape."""
    _install_mock_async_client(
        monkeypatch,
        [_MockResponse(503), _MockResponse(200, {"data": []})],
    )
    asyncio_sleeps, time_sleeps = _install_sleep_recorders(monkeypatch)

    client = SemanticScholarClient(api_key="")
    result = await client._request_async("/paper/search")

    assert result == {"data": []}
    assert asyncio_sleeps == [1.0]
    assert time_sleeps == []


# ---------------------------------------------------------------------------
# 3. _request_async: non-transient 4xx surfaces immediately as RuntimeError
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_request_async_raises_on_400_without_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 400 must not be retried — same contract as the sync ``_request``."""
    mock_client = _install_mock_async_client(
        monkeypatch,
        [_MockResponse(400, text="bad query")],
    )
    asyncio_sleeps, time_sleeps = _install_sleep_recorders(monkeypatch)

    client = SemanticScholarClient(api_key="")
    with pytest.raises(RuntimeError, match="Semantic Scholar API error"):
        await client._request_async("/paper/search")
    assert len(mock_client.get_calls) == 1
    assert asyncio_sleeps == []
    assert time_sleeps == []


# ---------------------------------------------------------------------------
# 4. _search_async end-to-end: 429 → 200 on the live top-up succeeds
#    and never blocks via time.sleep
# ---------------------------------------------------------------------------


class _MockAsyncpgConn:
    """asyncpg.Connection stand-in for the hybrid ``search`` path.

    ``_search_async`` only calls ``fetch`` (for the BM25 query) and
    ``close``; nothing else is exercised here.
    """

    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self._rows = rows or []
        self.fetch_calls: list[tuple[str, tuple[Any, ...]]] = []
        self.close_count = 0

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        self.fetch_calls.append((sql, args))
        return self._rows

    async def close(self) -> None:
        self.close_count += 1


def _install_database_url(
    monkeypatch: pytest.MonkeyPatch, url: str = "postgres://test/db"
) -> None:
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setattr(s2_client, "secret", lambda _k, default="": default)


def _install_mock_conn(
    monkeypatch: pytest.MonkeyPatch, mock: _MockAsyncpgConn
) -> None:
    async def _connect(_url: str, **_kwargs: Any) -> _MockAsyncpgConn:
        return mock

    monkeypatch.setattr(asyncpg, "connect", _connect)


def test_search_async_recovers_from_live_429_without_blocking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end: the live S2 top-up returns 429 → 200; the hybrid
    ``search`` envelope contains the recovered live paper and the retry
    backoff never reached ``time.sleep``."""
    _install_database_url(monkeypatch)
    _install_mock_conn(monkeypatch, _MockAsyncpgConn(rows=[]))
    live_payload = {
        "data": [
            {
                "paperId": "L1",
                "title": "Async recovery paper",
                "year": 2024,
                "authors": [],
                "abstract": "",
                "url": "https://example.invalid/L1",
                "citationCount": 0,
                "openAccessPdf": None,
            }
        ]
    }
    _install_mock_async_client(
        monkeypatch,
        [_MockResponse(429), _MockResponse(200, live_payload)],
    )
    asyncio_sleeps, time_sleeps = _install_sleep_recorders(monkeypatch)

    client = SemanticScholarClient(api_key="")
    result = client.search("async retry coverage")

    assert result["status"] == "ok"
    assert result["live_error"] is None
    assert result["live_count"] == 1
    assert [r["paperId"] for r in result["results"]] == ["L1"]
    assert [r["lane"] for r in result["results"]] == ["live"]
    # Retry backoff went through the awaitable path; nothing blocked
    # the event loop via ``time.sleep``.
    assert asyncio_sleeps == [1.0]
    assert time_sleeps == []


# ---------------------------------------------------------------------------
# 5. search_papers_async input validation mirrors the sync sibling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_papers_async_rejects_empty_query() -> None:
    client = SemanticScholarClient(api_key="")
    with pytest.raises(ValueError, match="query cannot be empty"):
        await client.search_papers_async("   ")


@pytest.mark.asyncio
async def test_search_papers_async_builds_year_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``year_from`` must be projected as ``"<year>-"`` — same shape as
    the sync ``search_papers`` so callers can swap one for the other
    without surprise."""
    _install_mock_async_client(
        monkeypatch, [_MockResponse(200, {"data": [{"paperId": "p1"}]})]
    )

    captured: dict[str, Any] = {}

    async def _capture_request_async(
        self: SemanticScholarClient,
        path: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        captured["path"] = path
        captured["params"] = params
        return {"data": []}

    monkeypatch.setattr(
        SemanticScholarClient,
        "_request_async",
        _capture_request_async,
        raising=True,
    )

    client = SemanticScholarClient(api_key="")
    await client.search_papers_async("active inference", limit=5, year_from=2023)

    assert captured["path"] == "/paper/search"
    assert captured["params"] is not None
    assert captured["params"]["query"] == "active inference"
    assert captured["params"]["limit"] == 5
    assert captured["params"]["year"] == "2023-"


# ---------------------------------------------------------------------------
# 6. _request_async survives the JSON path with default params shape
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_request_async_passes_headers_when_api_key_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ``x-api-key`` header must reach the live request when an api
    key is configured — regression guard against accidentally stripping
    headers in the async variant."""
    mock_client = _install_mock_async_client(
        monkeypatch, [_MockResponse(200, {"data": []})]
    )
    # No asyncio.sleep should fire on the happy path.
    _install_sleep_recorders(monkeypatch)

    client = SemanticScholarClient(api_key="secret-key-123")
    result = await client._request_async("/paper/search", params={"query": "x"})

    assert result == {"data": []}
    assert len(mock_client.get_calls) == 1
    headers = mock_client.get_calls[0]["headers"] or {}
    assert headers.get("x-api-key") == "secret-key-123"


# ---------------------------------------------------------------------------
# Sanity: ensure the response constructor handles ``json.dumps``-able
# inputs so future test authors can extend the suite with richer payloads.
# ---------------------------------------------------------------------------


def test_mock_response_serializes_to_json() -> None:
    payload = {"data": [{"paperId": "x", "title": "y"}]}
    assert json.loads(json.dumps(_MockResponse(200, payload).json())) == payload
