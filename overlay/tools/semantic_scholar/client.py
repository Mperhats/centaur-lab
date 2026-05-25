"""Semantic Scholar Graph API client.

Reference: https://api.semanticscholar.org/api-docs/graph

The Graph API is callable anonymously (heavily rate-limited) or with an
``x-api-key`` for higher quotas. We send the header only when the secret is
set so anonymous calls don't accidentally hit a 401 on a stale placeholder.
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from centaur_sdk import secret

DEFAULT_PAPER_FIELDS = "title,authors,year,abstract,citationCount,url,openAccessPdf"
DEFAULT_REFERENCE_FIELDS = "title,authors,year,citationCount,url"


class SemanticScholarClient:
    """Search papers, fetch metadata, and walk the citation graph."""

    BASE_URL = "https://api.semanticscholar.org/graph/v1"

    # Anonymous Semantic Scholar IPs hit 429 quickly. A small bounded backoff
    # smooths over the common case without masking real failures.
    MAX_RETRIES = 4

    def __init__(self, api_key: str | None = None, timeout: float = 30.0):
        self._api_key = api_key if api_key is not None else self._resolve_api_key()
        self._timeout = timeout
        self._client: httpx.Client | None = None

    @staticmethod
    def _resolve_api_key() -> str:
        # The tool works anonymously; default to "" so callers don't have to
        # branch on None. Iron-proxy only injects the real value when the
        # header is actually present, so an empty string keeps requests
        # anonymous instead of breaking them.
        return secret("SEMANTIC_SCHOLAR_API_KEY", "")

    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=self._timeout)
        return self._client

    def _headers(self) -> dict[str, str]:
        if self._api_key:
            return {"x-api-key": self._api_key}
        return {}

    def _request(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{self.BASE_URL}{path}"
        last_exc: Exception | None = None
        for attempt in range(self.MAX_RETRIES):
            try:
                response = self.client.get(url, params=params, headers=self._headers())
                # 429 is the dominant failure mode for anonymous use; retry with
                # exponential backoff. 5xx is also transient. Anything else is
                # raised immediately (4xx errors are usually our fault).
                if response.status_code in (429, 502, 503, 504):
                    last_exc = httpx.HTTPStatusError(
                        f"transient {response.status_code}", request=response.request, response=response
                    )
                    if attempt < self.MAX_RETRIES - 1:
                        time.sleep(min(8.0, 2**attempt))
                        continue
                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as exc:
                body = exc.response.text if exc.response is not None else ""
                status = exc.response.status_code if exc.response is not None else "?"
                raise RuntimeError(f"Semantic Scholar API error ({status}): {body}") from exc
            except httpx.RequestError as exc:
                last_exc = exc
                if attempt < self.MAX_RETRIES - 1:
                    time.sleep(min(8.0, 2**attempt))
                    continue
                raise RuntimeError(f"Semantic Scholar request failed: {exc}") from exc
        raise RuntimeError(
            f"Semantic Scholar request failed after {self.MAX_RETRIES} attempts: {last_exc}"
        )

    def search_papers(
        self,
        query: str,
        limit: int = 10,
        year_from: int | None = None,
        fields: str = DEFAULT_PAPER_FIELDS,
    ) -> list[dict]:
        """Search papers by free-text query.

        Args:
            query: Free-text search query (e.g. "diffusion models protein design").
            limit: Max results, 1..100.
            year_from: Optional inclusive lower bound on publication year.
            fields: Comma-separated list of fields per the Graph API spec.

        Returns:
            A list of paper dicts (already unwrapped from the ``data`` envelope).
        """
        if not query or not query.strip():
            raise ValueError("query cannot be empty.")
        params: dict[str, Any] = {
            "query": query.strip(),
            "limit": max(1, min(limit, 100)),
            "fields": fields,
        }
        if year_from is not None:
            params["year"] = f"{year_from}-"
        payload = self._request("/paper/search", params=params)
        results = payload.get("data", [])
        return [item for item in results if isinstance(item, dict)]

    def get_paper(
        self,
        paper_id: str,
        fields: str = DEFAULT_PAPER_FIELDS,
    ) -> dict:
        """Fetch metadata for a single paper.

        ``paper_id`` accepts any of the IDs the Graph API understands —
        Semantic Scholar IDs, DOIs (``DOI:10.x/y``), arXiv IDs (``arXiv:1234.5678``),
        and a few others. See the upstream docs for the full list.
        """
        if not paper_id or not paper_id.strip():
            raise ValueError("paper_id cannot be empty.")
        return self._request(f"/paper/{paper_id.strip()}", params={"fields": fields})

    def get_references(
        self,
        paper_id: str,
        limit: int = 20,
        fields: str = DEFAULT_REFERENCE_FIELDS,
    ) -> list[dict]:
        """List the papers that the given paper cites."""
        if not paper_id or not paper_id.strip():
            raise ValueError("paper_id cannot be empty.")
        params = {"limit": max(1, min(limit, 100)), "fields": fields}
        payload = self._request(f"/paper/{paper_id.strip()}/references", params=params)
        items = payload.get("data", [])
        # Each reference entry wraps the cited paper under "citedPaper"; flatten
        # so callers get a list of paper dicts directly.
        out: list[dict] = []
        for entry in items:
            if not isinstance(entry, dict):
                continue
            cited = entry.get("citedPaper")
            if isinstance(cited, dict):
                out.append(cited)
        return out

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def __enter__(self) -> SemanticScholarClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


def _client() -> SemanticScholarClient:
    """Factory the Centaur tool loader calls to instantiate the tool."""
    return SemanticScholarClient()
