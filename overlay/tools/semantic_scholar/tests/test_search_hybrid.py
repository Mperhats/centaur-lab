"""Round-trip coverage for ``BIBTEX_PAPER_FIELDS``.

The hybrid file name reflects the broader role this suite plays in
phase 4d: pinning the field constants the citation-gathering workflow
sends to S2, separate from the live ``client.search`` agent-facing
wrapper (covered by ``test_search.py``) and the async retry posture
(``test_request_async.py``).

What we pin here:

1. ``BIBTEX_PAPER_FIELDS`` includes ``citationStyles`` (required for
   Sakana-style ``.bib`` emission in ``gather_citations.py``).
2. ``search_papers(fields=BIBTEX_PAPER_FIELDS)`` projects that string
   verbatim into the outbound query string — so ``citationStyles``
   actually reaches S2.
3. The parsed response surfaces ``citationStyles`` as-is (pass-through
   dicts, no typed Paper model in the way) so downstream BibTeX
   rendering has the raw ``{"bibtex": "@article{..."}`` payload S2
   returns.
4. The two existing field constants (``DEFAULT_PAPER_FIELDS``,
   ``DEFAULT_REFERENCE_FIELDS``) are untouched — guards against a
   "fix one constant, drift another" regression in a future edit.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

import semantic_scholar.client as s2_client
from semantic_scholar.client import (
    BIBTEX_PAPER_FIELDS,
    DEFAULT_PAPER_FIELDS,
    DEFAULT_REFERENCE_FIELDS,
    SemanticScholarClient,
)


class _MockResponse:
    """Minimal ``httpx.Response`` substitute for the sync ``_request`` path."""

    def __init__(self, status_code: int, json_data: dict[str, Any]) -> None:
        self.status_code = status_code
        self._json = json_data
        self.text = ""
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


class _MockHttpxClient:
    """``httpx.Client`` stand-in that records each outbound ``get``."""

    def __init__(self, response: _MockResponse) -> None:
        self._response = response
        self.get_calls: list[dict[str, Any]] = []

    def get(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> _MockResponse:
        self.get_calls.append({"url": url, "params": params, "headers": headers})
        return self._response

    def close(self) -> None:
        return None


def test_bibtex_paper_fields_includes_citation_styles() -> None:
    """The constant must request ``citationStyles`` — required by
    ``gather_citations.py`` to emit a real ``.bib`` file."""
    fields = BIBTEX_PAPER_FIELDS.split(",")
    assert "citationStyles" in fields
    # Sanity: the rest of the BibTeX-essential fields are still present.
    # If a future edit drops one of these, the downstream BibTeX
    # rendering will silently lose author / year / link info.
    for required in ("title", "authors", "year", "url"):
        assert required in fields, f"BIBTEX_PAPER_FIELDS missing {required!r}"


def test_other_field_constants_unchanged() -> None:
    """Sanity guard: only ``BIBTEX_PAPER_FIELDS`` should request
    ``citationStyles``. Other constants are tuned for different call
    sites (full-paper read with abstract, reference walks) and adding
    ``citationStyles`` to them would inflate every search response."""
    assert "citationStyles" not in DEFAULT_PAPER_FIELDS
    assert "citationStyles" not in DEFAULT_REFERENCE_FIELDS


def test_search_papers_projects_bibtex_fields_into_query_string(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end: ``search_papers(fields=BIBTEX_PAPER_FIELDS)`` must
    send ``fields=...citationStyles`` to S2 and surface the returned
    ``citationStyles`` dict verbatim — the round-trip ``gather_citations``
    depends on."""
    payload = {
        "data": [
            {
                "paperId": "p1",
                "title": "BibTeX round-trip paper",
                "year": 2024,
                "authors": [{"name": "A. Author"}],
                "citationCount": 7,
                "url": "https://example.invalid/p1",
                "citationStyles": {
                    "bibtex": (
                        "@article{Author2024, title={BibTeX round-trip paper}, "
                        "author={A. Author}, year={2024}}"
                    ),
                },
            }
        ]
    }
    mock_http = _MockHttpxClient(_MockResponse(200, payload))

    client = SemanticScholarClient(api_key="")
    monkeypatch.setattr(client, "_client", mock_http)

    papers = client.search_papers("diffusion models", fields=BIBTEX_PAPER_FIELDS)

    assert len(mock_http.get_calls) == 1
    call = mock_http.get_calls[0]
    assert call["url"] == f"{SemanticScholarClient.BASE_URL}/paper/search"
    assert call["params"] is not None
    fields_param = call["params"]["fields"]
    # httpx serializes ``params`` to a query string later; what matters
    # here is that the client handed ``citationStyles`` to httpx.
    assert "citationStyles" in fields_param.split(",")

    assert len(papers) == 1
    assert papers[0]["paperId"] == "p1"
    assert papers[0]["citationStyles"]["bibtex"].startswith("@article{")


def test_search_papers_async_projects_bibtex_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same projection guarantee for the async sibling — keeps the two
    code paths from drifting on what they ask S2 for."""
    captured: dict[str, Any] = {}

    async def _capture_request_async(
        self: SemanticScholarClient,
        path: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        captured["path"] = path
        captured["params"] = params
        return {
            "data": [
                {
                    "paperId": "p2",
                    "citationStyles": {"bibtex": "@article{P2}"},
                }
            ]
        }

    monkeypatch.setattr(
        SemanticScholarClient,
        "_request_async",
        _capture_request_async,
        raising=True,
    )

    import asyncio

    client = SemanticScholarClient(api_key="")
    papers = asyncio.run(
        client.search_papers_async("graph neural networks", fields=BIBTEX_PAPER_FIELDS)
    )

    assert captured["path"] == "/paper/search"
    assert captured["params"] is not None
    assert "citationStyles" in captured["params"]["fields"].split(",")
    assert papers[0]["citationStyles"] == {"bibtex": "@article{P2}"}


def test_bibtex_paper_fields_matches_plan_exact_string() -> None:
    """Plan 2026-05-26-bfts-phase4 §Task 4d.1 pins the exact constant
    value. Lock it here so a stray field reorder doesn't silently
    change the S2 request shape for ``gather_citations``."""
    assert (
        BIBTEX_PAPER_FIELDS
        == "title,authors,year,citationCount,url,citationStyles"
    )


# Ensure the module export is importable as a top-level attribute (not
# a function-local) — protects against a future refactor that hides it
# inside a class or function body.
def test_bibtex_paper_fields_is_module_attribute() -> None:
    assert hasattr(s2_client, "BIBTEX_PAPER_FIELDS")
    assert isinstance(s2_client.BIBTEX_PAPER_FIELDS, str)
