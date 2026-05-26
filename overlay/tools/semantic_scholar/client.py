"""Semantic Scholar Graph API client with hybrid indexed/live search and research-brief generation."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import time
from datetime import UTC, datetime
from typing import Any

import asyncpg
import httpx

from centaur_sdk import secret
from shared.metrics import observe_document_size, record_document_change
from shared.paper_document import (
    _canonical_json,
    _content_hash,
    build_paper_document,
    upsert_document,
)

log = logging.getLogger(__name__)

DEFAULT_PAPER_FIELDS = "title,authors,year,abstract,citationCount,url,openAccessPdf"
DEFAULT_REFERENCE_FIELDS = "title,authors,year,citationCount,url"

DEFAULT_HYBRID_SEARCH_LIMIT = 10
MAX_HYBRID_SEARCH_LIMIT = 50

DEFAULT_RESEARCH_BRIEF_LIMIT = 5
MAX_RESEARCH_BRIEF_LIMIT = 20

# Input-validation error strings emitted by ``research_brief``. Promoted to
# module-level constants so the workflow wrapper at
# ``overlay/workflows/research_brief.py`` can key its
# ``error → skipped`` translation table on them by import — any future
# reword surfaces as an ``ImportError`` rather than a silent contract drift
# that strands external callers (Justfile smoke recipes, direct posters to
# ``/workflows/runs``) on the wrong envelope shape.
RESEARCH_BRIEF_EMPTY_QUERY_ERROR = "query cannot be empty"
RESEARCH_BRIEF_INVALID_LIMIT_ERROR = "limit must be positive"

# Brief markdown rendering knobs. Mirror the workflow constants from
# ``overlay/workflows/research_brief.py`` so the rendered output is
# byte-identical to the legacy path until Task 3 collapses the workflow
# into a wrapper around this method.
_BRIEF_ABSTRACT_TRUNCATE = 500
_BRIEF_TITLE_QUERY_TRUNCATE = 80
_BRIEF_ID_HEX_LEN = 16
_BRIEF_MAX_AUTHORS_INLINE = 3


# ---------------------------------------------------------------------------
# The BM25 query helpers below are copied verbatim from
# .centaur/tools/productivity/company_context/client.py. Keep in sync — they
# implement the same paradedb scoring contract that the upstream Slack tool
# relies on.
# ---------------------------------------------------------------------------

EXACT_QUERY_TITLE_BOOST = 8
EXACT_QUERY_BODY_BOOST = 2
TITLE_MATCH_BOOST = 4
DEFAULT_PREVIEW_CHARS = 280

_SEARCH_TERM_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]*")
_STOP_WORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "but",
    "by",
    "for",
    "from",
    "how",
    "i",
    "if",
    "in",
    "into",
    "is",
    "it",
    "of",
    "on",
    "or",
    "our",
    "that",
    "the",
    "their",
    "there",
    "these",
    "they",
    "this",
    "to",
    "was",
    "we",
    "were",
    "what",
    "when",
    "where",
    "which",
    "who",
    "why",
    "will",
    "with",
}


def _clamp(value: int, *, minimum: int, maximum: int) -> int:
    """Clamp integer tool inputs to predictable output bounds."""
    return max(minimum, min(int(value), maximum))


def _as_dict(value: Any) -> dict[str, Any]:
    """Decode asyncpg JSON/JSONB values into a dict."""
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            return {}
    return {}


def _isoformat(value: Any) -> str | None:
    """Serialize datetimes while leaving absent values explicit."""
    if isinstance(value, datetime):
        return value.isoformat()
    return None


def _normalize_text(value: str) -> str:
    """Collapse whitespace so previews stay compact and readable."""
    return re.sub(r"\s+", " ", value).strip()


def _search_terms(query: str) -> list[str]:
    """Extract unique content terms, falling back when filtering removes everything."""
    seen: set[str] = set()
    all_terms: list[str] = []
    filtered_terms: list[str] = []
    for match in _SEARCH_TERM_RE.finditer(query):
        term = match.group(0).strip()
        if len(term) < 2:
            continue
        key = term.lower()
        if key in seen:
            continue
        seen.add(key)
        all_terms.append(term)
        if key not in _STOP_WORDS:
            filtered_terms.append(term)
    return filtered_terms or all_terms or [query]


def _search_where_clause(term_count: int) -> str:
    """Build a ParadeDB query that boosts exact matches and falls back to OR term matching."""
    clauses = [
        "("
        f"title ||| $1::text::pdb.boost({EXACT_QUERY_TITLE_BOOST}) "
        f"OR body ||| $1::text::pdb.boost({EXACT_QUERY_BODY_BOOST})"
        ")"
    ]
    for index in range(2, term_count + 2):
        clauses.append(
            f"(title ||| ${index}::text::pdb.boost({TITLE_MATCH_BOOST}) OR body ||| ${index})"
        )
    return " OR ".join(clauses)


def _body_preview(body: str, *, query: str, max_chars: int = DEFAULT_PREVIEW_CHARS) -> str:
    """Build a compact preview centered on the first query-term hit when possible."""
    normalized = _normalize_text(body)
    if not normalized:
        return ""
    if len(normalized) <= max_chars:
        return normalized

    terms = _search_terms(query)
    start = 0
    lowered = normalized.lower()
    for term in terms:
        index = lowered.find(term.lower())
        if index >= 0:
            start = max(0, index - max_chars // 3)
            break

    end = min(len(normalized), start + max_chars)
    snippet = normalized[start:end].strip()
    if start > 0:
        snippet = f"...{snippet}"
    if end < len(normalized):
        snippet = f"{snippet}..."
    return snippet


def _row_value(row: Any, key: str, default: Any = None) -> Any:
    """Read values from asyncpg rows while tolerating sparse test doubles."""
    try:
        value = row[key]
    except (KeyError, IndexError, TypeError):
        return default
    return default if value is None else value


def _document_summary(row: Any) -> dict[str, Any]:
    """Return the common metadata we expose for document records."""
    return {
        "document_id": str(_row_value(row, "document_id", "")),
        "source": str(_row_value(row, "source", "")),
        "source_type": str(_row_value(row, "source_type", "")),
        "source_document_id": str(_row_value(row, "source_document_id", "")),
        "source_chunk_id": str(_row_value(row, "source_chunk_id", "")),
        "parent_document_id": str(_row_value(row, "parent_document_id", "") or "") or None,
        "title": str(_row_value(row, "title", "")),
        "url": str(_row_value(row, "url", "")),
        "author_name": str(_row_value(row, "author_name", "")),
        "access_scope": str(_row_value(row, "access_scope", "")),
        "occurred_at": _isoformat(_row_value(row, "occurred_at")),
        "source_updated_at": _isoformat(_row_value(row, "source_updated_at")),
        "metadata": _as_dict(_row_value(row, "metadata", {})),
    }


def _extract_paper_id(summary: dict[str, Any]) -> str:
    """Pick a paperId out of an indexed-document summary."""
    metadata_paper_id = summary.get("metadata", {}).get("paperId")
    if metadata_paper_id:
        return str(metadata_paper_id)
    return str(summary.get("source_document_id") or "")


def _cutoff_year_from_rows(rows: list[Any]) -> int | None:
    """Compute the most recent ``metadata.year`` across the indexed rows."""
    best: int | None = None
    for row in rows:
        metadata = _as_dict(_row_value(row, "metadata", {}))
        raw_year = metadata.get("year")
        if raw_year is None:
            continue
        try:
            year_int = int(raw_year)
        except (TypeError, ValueError):
            continue
        if best is None or year_int > best:
            best = year_int
    return best


def _live_paper_result(paper: dict[str, Any]) -> dict[str, Any]:
    """Project a Semantic Scholar paper dict into the merged search result shape."""
    return {
        "paperId": str(paper.get("paperId") or ""),
        "title": paper.get("title"),
        "year": paper.get("year"),
        "authors": paper.get("authors") or [],
        "abstract": paper.get("abstract"),
        "url": paper.get("url"),
        "citationCount": paper.get("citationCount"),
        "openAccessPdf": paper.get("openAccessPdf"),
        "lane": "live",
        "result_type": "paper",
        "score": None,
    }


# ---------------------------------------------------------------------------
# Research-brief rendering helpers (ported from
# overlay/workflows/research_brief.py). Pure functions, no I/O — kept
# at module scope so they're trivially testable and so Task 3 can have
# the workflow delegate here without circular imports.
# ---------------------------------------------------------------------------


def _brief_id_for(query: str, year_from: int | None) -> str:
    """Stable, case-insensitive id suffix for the brief document.

    Date is intentionally excluded so re-running the same query updates
    the same row instead of accreting one brief per run. Reuses the
    ``shared.paper_document`` canonical JSON helper so any future tweak
    to canonicalization flows through here without silently drifting
    brief IDs.
    """
    canonical = _canonical_json([query.strip().lower(), year_from])
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:_BRIEF_ID_HEX_LEN]


def _normalize_oneline(text: str) -> str:
    """Collapse all whitespace to single spaces; safe for Markdown headings."""
    return " ".join(text.split())


def _format_authors(authors: list[Any]) -> str:
    names: list[str] = []
    for entry in authors or []:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        if name:
            names.append(str(name))
    if not names:
        return "Unknown"
    if len(names) <= _BRIEF_MAX_AUTHORS_INLINE:
        return ", ".join(names)
    head = ", ".join(names[:_BRIEF_MAX_AUTHORS_INLINE])
    return f"{head} +{len(names) - _BRIEF_MAX_AUTHORS_INLINE} more"


def _paper_url(paper: dict[str, Any]) -> str:
    url = paper.get("url")
    if url:
        return str(url)
    paper_id = paper.get("paperId")
    if paper_id:
        return f"https://www.semanticscholar.org/paper/{paper_id}"
    return ""


def _format_abstract(paper: dict[str, Any]) -> str:
    abstract = paper.get("abstract")
    if not abstract:
        return "No abstract available."
    text = str(abstract)
    if len(text) > _BRIEF_ABSTRACT_TRUNCATE:
        return text[:_BRIEF_ABSTRACT_TRUNCATE] + "..."
    return text


def _render_brief(query: str, year_from: int | None, papers: list[dict[str, Any]]) -> str:
    """Render the brief Markdown. Pure; no I/O."""
    display_query = _normalize_oneline(query)
    year_label = str(year_from) if year_from is not None else "any"
    header = [
        f"# Research Brief: {display_query}",
        "",
        f"- Query: {display_query}",
        f"- Year filter: {year_label}",
        f"- Results: {len(papers)} papers",
        "",
        "---",
        "",
    ]

    if not papers:
        return "\n".join([*header, "No papers found for this query.", ""])

    lines: list[str] = [*header, "## Papers", ""]
    for index, paper in enumerate(papers, start=1):
        display_title = _normalize_oneline(str(paper.get("title") or "Untitled"))
        year = paper.get("year")
        year_text = str(year) if isinstance(year, int) else "Unknown"
        citations = int(paper.get("citationCount") or 0)
        authors_value = paper.get("authors")
        authors_list = authors_value if isinstance(authors_value, list) else []
        lines.extend(
            [
                f"### {index}. {display_title}",
                "",
                f"- Authors: {_format_authors(authors_list)}",
                f"- Year: {year_text}",
                f"- Citations: {citations}",
                f"- URL: {_paper_url(paper)}",
                "",
                _format_abstract(paper),
                "",
            ]
        )
    return "\n".join(lines)


def _build_brief_document(
    query: str,
    year_from: int | None,
    limit: int,
    papers: list[dict[str, Any]],
    markdown: str,
) -> dict[str, Any]:
    """Project the rendered brief into a ``company_context_documents`` row.

    The brief metadata follows the same explicit-nulls convention as
    ``build_paper_document``: every key is listed even when its value
    is ``None`` (e.g. ``year_from``) so JSONB key-presence checks
    behave the same way across overlay sources.

    ``source_updated_at`` is set to ``datetime.now(UTC)`` rather than
    ``None`` for the same reason ``build_paper_document`` does: the
    freshness dashboard tracking ``source_updated_at`` should see the
    brief as freshly synced at projection time, not as a row with no
    known source-side timestamp. ``occurred_at`` stays ``None`` — a
    brief has no publication date analog.
    """
    suffix = _brief_id_for(query, year_from)
    truncated_query = query[:_BRIEF_TITLE_QUERY_TRUNCATE]
    title = f"Research Brief: {truncated_query}"
    paper_ids = [str(p["paperId"]) for p in papers if p.get("paperId")]
    metadata: dict[str, Any] = {
        "query": query,
        "year_from": year_from,
        "limit": limit,
        "results_count": len(papers),
        "paper_ids": paper_ids,
    }
    return {
        "document_id": f"semantic_scholar:research_brief:{suffix}",
        "source": "semantic_scholar",
        "source_type": "research_brief",
        "source_document_id": suffix,
        "source_chunk_id": "",
        "parent_document_id": None,
        "title": title,
        "body": markdown,
        "url": "",
        "author_id": "",
        "author_name": "",
        "access_scope": "company",
        "occurred_at": None,
        "source_updated_at": datetime.now(UTC),
        "content_hash": _content_hash(title, markdown, "", metadata),
        "metadata": metadata,
    }


class SemanticScholarClient:
    """Search papers, fetch metadata, walk the citation graph, and build research briefs.

    Wraps the Semantic Scholar Graph API
    (https://api.semanticscholar.org/api-docs/graph), which is callable
    anonymously (heavily rate-limited) or with an ``x-api-key`` for
    higher quotas — the header is sent only when the secret is set so
    anonymous calls don't accidentally hit a 401 on a stale placeholder.
    Exposes a hybrid ``search`` that consults already-indexed papers in
    ``company_context_documents`` before topping up via the live API.
    """

    BASE_URL = "https://api.semanticscholar.org/graph/v1"

    # Anonymous Semantic Scholar IPs hit 429 quickly. A small bounded backoff
    # smooths over the common case without masking real failures.
    MAX_RETRIES = 4

    def __init__(
        self,
        api_key: str | None = None,
        timeout: float = 30.0,
        database_url: str | None = None,
    ) -> None:
        # Store the constructor-injected key as-is; resolve any fallback
        # lazily at request time. Eager resolution here runs during the
        # ToolManager's _collect_methods() pass when ToolContext.secrets
        # is still empty, so the per-call secret never lands in the header.
        self._api_key = api_key
        self._timeout = timeout
        self._client: httpx.Client | None = None
        # DATABASE_URL is owned by the API process, not an agent-facing
        # secret; mirror the resolution chain upstream company_context uses
        # so a constructor arg can override env or secret for tests.
        env_database_url = os.getenv("DATABASE_URL")  # noqa: TID251
        self._database_url = (
            database_url or env_database_url or secret("DATABASE_URL", default="")
        ).strip()

    def _require_database_url(self) -> str:
        if not self._database_url:
            raise RuntimeError(
                "DATABASE_URL is required for semantic_scholar database access"
            )
        return self._database_url

    async def _connect(self) -> asyncpg.Connection:
        return await asyncpg.connect(self._require_database_url(), command_timeout=30)

    def _get_api_key(self) -> str | None:
        """Get API key from instance or env var."""
        # The tool works anonymously; default to "" so callers don't have to
        # branch on None. Iron-proxy only injects the real value when the
        # header is actually present, so an empty string keeps requests
        # anonymous instead of breaking them.
        if self._api_key:
            return self._api_key
        return secret("SEMANTIC_SCHOLAR_API_KEY", "")

    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=self._timeout)
        return self._client

    def _headers(self) -> dict[str, str]:
        api_key = self._get_api_key()
        if api_key:
            return {"x-api-key": api_key}
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

    async def _request_async(
        self, path: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Async sibling of ``_request`` — ``await asyncio.sleep`` for backoff.

        Mirrors the retry policy of the sync ``_request`` (transient 429/5xx
        gets exponential backoff up to 8s; any other 4xx raises immediately)
        but uses an ``httpx.AsyncClient`` and ``await asyncio.sleep`` so the
        retry sleeps don't block the asyncio event loop the way ``time.sleep``
        does. Mirrors ``_exa_search_async`` in
        ``.centaur/tools/research/websearch/client.py``.

        The ``AsyncClient`` is created per-call rather than cached on the
        instance (Option A in the review): the sync entry points
        (``search`` / ``research_brief``) drive their own ``asyncio.run``
        loop, and ``httpx.AsyncClient`` binds its internal anyio primitives
        to the event loop it's instantiated under — so a cached client
        would crash on the second ``asyncio.run`` with "Future attached to a
        different loop". Per-call ``async with`` keeps lifecycle trivial
        (no ``aclose`` to wire through ``__exit__``) and matches upstream
        websearch's pattern exactly.
        """
        url = f"{self.BASE_URL}{path}"
        last_exc: Exception | None = None
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            for attempt in range(self.MAX_RETRIES):
                try:
                    response = await client.get(
                        url, params=params, headers=self._headers()
                    )
                    if response.status_code in (429, 502, 503, 504):
                        last_exc = httpx.HTTPStatusError(
                            f"transient {response.status_code}",
                            request=response.request,
                            response=response,
                        )
                        if attempt < self.MAX_RETRIES - 1:
                            await asyncio.sleep(min(8.0, 2**attempt))
                            continue
                    response.raise_for_status()
                    return response.json()
                except httpx.HTTPStatusError as exc:
                    body = exc.response.text if exc.response is not None else ""
                    status = exc.response.status_code if exc.response is not None else "?"
                    raise RuntimeError(
                        f"Semantic Scholar API error ({status}): {body}"
                    ) from exc
                except httpx.RequestError as exc:
                    last_exc = exc
                    if attempt < self.MAX_RETRIES - 1:
                        await asyncio.sleep(min(8.0, 2**attempt))
                        continue
                    raise RuntimeError(
                        f"Semantic Scholar request failed: {exc}"
                    ) from exc
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

    async def search_papers_async(
        self,
        query: str,
        limit: int = 10,
        year_from: int | None = None,
        fields: str = DEFAULT_PAPER_FIELDS,
    ) -> list[dict]:
        """Async variant of ``search_papers`` for use inside coroutines.

        Identical input validation and query-parameter shaping as
        ``search_papers``; delegates to ``_request_async`` so retry backoff
        awaits instead of blocking the event loop. Use this whenever the
        caller is already running inside a coroutine (e.g. ``_search_async``,
        ``_research_brief_async``); call the sync ``search_papers`` from
        sync code paths.
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
        payload = await self._request_async("/paper/search", params=params)
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

    def search(
        self,
        query: str,
        limit: int = DEFAULT_HYBRID_SEARCH_LIMIT,
        year_from: int | None = None,
    ) -> dict:
        """Hybrid indexed-first, live-after search across saved + live S2 papers.

        BM25-queries ``company_context_documents`` for Semantic Scholar
        papers already projected into the table, then tops up via the
        live ``/paper/search`` endpoint with ``year_from`` advanced past
        the most recent indexed year. Live results whose ``paperId``
        already appears in the indexed slice are dropped.

        Never raises — returns an ``{"status": "error", "error": ...}``
        dict on any failure that prevents producing results.
        """
        normalized_query = query.strip() if query else ""
        if not normalized_query:
            return {"status": "error", "error": "query cannot be empty"}

        if not self._database_url:
            return {
                "status": "error",
                "error": "DATABASE_URL is required for semantic_scholar.search",
            }

        clamped_limit = _clamp(
            limit,
            minimum=1,
            maximum=MAX_HYBRID_SEARCH_LIMIT,
        )

        try:
            return asyncio.run(
                self._search_async(
                    query=normalized_query,
                    limit=clamped_limit,
                    year_from=year_from,
                )
            )
        except Exception as exc:
            log.warning("semantic_scholar search failed", exc_info=True)
            return {"status": "error", "error": str(exc)}

    async def _search_async(
        self,
        *,
        query: str,
        limit: int,
        year_from: int | None,
    ) -> dict[str, Any]:
        conn = await self._connect()
        try:
            terms = _search_terms(query)
            search_terms = [query, *terms]
            limit_param = len(search_terms) + 1
            rows = await conn.fetch(
                f"""
                SELECT
                    document_id,
                    source,
                    source_type,
                    source_document_id,
                    source_chunk_id,
                    parent_document_id,
                    title,
                    url,
                    author_name,
                    access_scope,
                    body,
                    occurred_at,
                    source_updated_at,
                    metadata,
                    paradedb.score(document_id) AS score
                FROM company_context_documents
                WHERE ({_search_where_clause(len(terms))})
                  AND source = 'semantic_scholar'
                  AND source_type = 'paper'
                ORDER BY
                    paradedb.score(document_id) DESC,
                    source_updated_at DESC NULLS LAST
                LIMIT ${limit_param}
                """,
                *search_terms,
                limit,
            )

            indexed_results: list[dict[str, Any]] = []
            indexed_paper_ids: set[str] = set()
            for row in rows:
                summary = _document_summary(row)
                summary["score"] = float(_row_value(row, "score", 0.0) or 0.0)
                summary["preview"] = _body_preview(
                    str(_row_value(row, "body", "") or ""),
                    query=query,
                )
                summary["lane"] = "indexed"
                summary["result_type"] = "paper"
                paper_id = _extract_paper_id(summary)
                summary["paperId"] = paper_id
                if paper_id:
                    indexed_paper_ids.add(paper_id)
                indexed_results.append(summary)

            cutoff_year = _cutoff_year_from_rows(rows)
            requested_floor = year_from or 0
            indexed_floor = (cutoff_year + 1) if cutoff_year is not None else 0
            effective_year_from_raw = max(requested_floor, indexed_floor)
            effective_year_from = effective_year_from_raw or None

            live_results: list[dict[str, Any]] = []
            live_error: str | None = None
            try:
                # Async-aware retry: ``search_papers_async`` → ``_request_async``
                # awaits ``asyncio.sleep`` on backoff instead of blocking the
                # event loop with ``time.sleep``. See review.md A5.
                raw_live = await self.search_papers_async(
                    query,
                    limit=limit,
                    year_from=effective_year_from,
                )
                for paper in raw_live:
                    if not isinstance(paper, dict):
                        continue
                    paper_id = str(paper.get("paperId") or "")
                    if paper_id and paper_id in indexed_paper_ids:
                        continue
                    live_results.append(_live_paper_result(paper))
            except Exception as exc:
                log.warning("semantic_scholar live api error", exc_info=True)
                live_error = str(exc)

            return {
                "status": "ok",
                "query": query,
                "limit": limit,
                "year_from": year_from,
                "indexed_count": len(indexed_results),
                "live_count": len(live_results),
                "count": len(indexed_results) + len(live_results),
                "indexed_cutoff_year": cutoff_year,
                "live_year_from": effective_year_from,
                "live_error": live_error,
                "results": [*indexed_results, *live_results],
            }
        finally:
            await conn.close()

    def research_brief(
        self,
        query: str,
        limit: int = DEFAULT_RESEARCH_BRIEF_LIMIT,
        year_from: int | None = None,
    ) -> dict[str, Any]:
        """Build a persisted research brief on a topic — searches Semantic Scholar,
        renders a Markdown lit review, and writes the brief plus its citing papers
        to ``company_context_documents`` for future RAG retrieval.

        Use this when a user asks for a literature review, a research summary,
        or "what does the literature say about X" — typical Slack prompts
        include "build a research brief on diffusion models", "lit review on
        active inference", "summarize recent work on retrieval-augmented
        generation". The rendered Markdown is returned as the ``markdown``
        field so the caller (e.g. a Slack agent) can post it directly.

        Idempotent: re-running with the same ``(query, year_from)`` updates
        the existing brief row in place (matched on a stable hash of the
        normalized query) instead of duplicating it. Each underlying paper
        is upserted under ``source_type="paper"`` with ``parent_document_id``
        stamped to the brief's document id, so downstream tools can pivot
        from a paper back to the brief that surfaced it.

        Never raises — returns an ``{"status": "error", "error": ...}`` dict
        on any failure (empty query, non-positive limit, missing
        ``DATABASE_URL``, S2 outage, DB failure). ``limit`` above the
        per-call ceiling is clamped, not rejected.

        Args:
            query: Free-text topic to brief (e.g. "diffusion models
                protein design").
            limit: Max underlying papers, 1..``MAX_RESEARCH_BRIEF_LIMIT``.
                Values above the ceiling are clamped.
            year_from: Optional inclusive lower bound on publication year.

        Returns:
            On success, a dict shaped::

                {
                    "status": "completed",
                    "brief_document_id": "semantic_scholar:research_brief:<hex>",
                    "brief_action": "inserted" | "updated" | "noop",
                    "results_count": <int>,
                    "papers_inserted": <int>,
                    "papers_updated": <int>,
                    "papers_noop": <int>,
                    "markdown": "<full rendered brief>",
                }

            On failure, ``{"status": "error", "error": "<message>"}``.
        """
        normalized_query = query.strip() if query else ""
        if not normalized_query:
            return {"status": "error", "error": RESEARCH_BRIEF_EMPTY_QUERY_ERROR}

        if limit <= 0:
            return {"status": "error", "error": RESEARCH_BRIEF_INVALID_LIMIT_ERROR}

        if not self._database_url:
            return {
                "status": "error",
                "error": "DATABASE_URL is required for semantic_scholar.research_brief",
            }

        clamped_limit = _clamp(
            limit,
            minimum=1,
            maximum=MAX_RESEARCH_BRIEF_LIMIT,
        )

        try:
            return asyncio.run(
                self._research_brief_async(
                    query=query,
                    limit=clamped_limit,
                    year_from=year_from,
                )
            )
        except Exception as exc:
            log.warning("semantic_scholar research_brief failed", exc_info=True)
            return {"status": "error", "error": str(exc)}

    async def _research_brief_async(
        self,
        *,
        query: str,
        limit: int,
        year_from: int | None,
    ) -> dict[str, Any]:
        # Run the (retry-prone) S2 call and the pure rendering before
        # opening a DB connection. Unlike hybrid ``search`` — which needs
        # a ``conn.fetch`` to resolve the cutoff year — the brief has no
        # data dependency on the DB, so holding a real Postgres
        # connection idle through httpx retries (up to ~15s) is pure cost.
        # Postgres ``max_connections`` is finite; we open a fresh
        # connection per call, so concurrent invocations would otherwise
        # pin one connection each for the duration of the S2 round trip.
        #
        # Async-aware retry: ``search_papers_async`` → ``_request_async``
        # awaits ``asyncio.sleep`` on backoff instead of blocking the
        # event loop with ``time.sleep``. See review.md A5.
        papers = await self.search_papers_async(
            query=query,
            limit=limit,
            year_from=year_from,
        )

        markdown = _render_brief(query, year_from, papers)
        brief_doc = _build_brief_document(query, year_from, limit, papers, markdown)

        conn = await self._connect()
        try:
            observe_document_size(brief_doc)
            brief_action = await upsert_document(conn, brief_doc)
            record_document_change(brief_doc, brief_action)

            papers_inserted = 0
            papers_updated = 0
            papers_noop = 0
            for paper in papers:
                try:
                    paper_doc = build_paper_document(paper, query=query)
                except ValueError:
                    # No paperId → cannot synthesize a stable primary key.
                    # The workflow logs this through ctx.log; here we
                    # silently drop the row and let the counters reflect
                    # only the upsertable subset. The outer error
                    # envelope handles unrecoverable failures.
                    continue
                observe_document_size(paper_doc)
                action = await upsert_document(
                    conn,
                    paper_doc,
                    parent_document_id=brief_doc["document_id"],
                )
                record_document_change(paper_doc, action)
                if action == "inserted":
                    papers_inserted += 1
                elif action == "updated":
                    papers_updated += 1
                else:
                    papers_noop += 1

            return {
                "status": "completed",
                "brief_document_id": brief_doc["document_id"],
                "brief_action": brief_action,
                "results_count": len(papers),
                "papers_inserted": papers_inserted,
                "papers_updated": papers_updated,
                "papers_noop": papers_noop,
                "markdown": markdown,
            }
        finally:
            await conn.close()

    def close(self) -> None:
        """Close the HTTP client."""
        if self._client is not None:
            self._client.close()
            self._client = None

    def __enter__(self) -> SemanticScholarClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


def _client() -> SemanticScholarClient:
    """Factory for tool loader."""
    return SemanticScholarClient()
