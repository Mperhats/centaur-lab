"""Semantic Scholar Graph API client with live search and research-brief generation.

Graph API calls (``search_paper``, ``get_paper``, ``get_paper_references``)
delegate to the upstream ``semanticscholar`` PyPI package — we no longer
hand-roll the HTTP layer. The high-level orchestration methods
(``search``, ``research_brief``, ``archive_paper``) keep their previous
contracts so workflow handlers, the Slack tool runtime, and the CLI all
keep working unchanged.

The typed objects returned to callers are the upstream library's
``semanticscholar.Paper.Paper``. Wire-shape consumers (``search``
envelope, CLI ``--json``) get the original JSON dict back via
``Paper.raw_data``; ``Paper(data)`` stores ``data`` by reference so a
freshly-constructed paper round-trips byte-for-byte.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

import asyncpg
from semanticscholar import SemanticScholar
from semanticscholar.Paper import Paper
from semanticscholar.SemanticScholarException import SemanticScholarException

from centaur_lab.brief import persist_research_brief_from_papers
from centaur_lab.metrics import observe_document_size, record_document_change
from centaur_lab.paper_document import build_paper_document, upsert_document
from centaur_lab.paper_fulltext import (
    build_fulltext_document,
    compute_pdf_sha256,
    upsert_paper_archive,
)
from centaur_sdk import secret
from tools.semantic_scholar import pdf_fetch, pdf_parse

log = logging.getLogger(__name__)

# Field lists are passed to the library as ``list[str]`` (rather than a
# comma-joined string) because that's the upstream library's signature.
# Kept as module-level constants so the CLI and tests can reuse them.
DEFAULT_PAPER_FIELDS: list[str] = [
    "title",
    "authors",
    "year",
    "abstract",
    "citationCount",
    "url",
    "openAccessPdf",
]
DEFAULT_REFERENCE_FIELDS: list[str] = [
    "title",
    "authors",
    "year",
    "citationCount",
    "url",
]

DEFAULT_SEARCH_LIMIT = 10
MAX_SEARCH_LIMIT = 50

DEFAULT_RESEARCH_BRIEF_LIMIT = 5
MAX_RESEARCH_BRIEF_LIMIT = 20

MAX_PDF_BYTES = 50 * 1024 * 1024  # 50 MiB hard cap on per-paper PDF download
PDF_DOWNLOAD_TIMEOUT_S = 60.0
PDF_USER_AGENT = "centaur-scientist/0.1 (paper-archive; +https://centaur.run)"
ARCHIVE_PARSER_MIN_SIZE = 100  # mirrors AI-Scientist-v2 load_paper min_size guard

# Input-validation error strings emitted by ``research_brief``. Promoted to
# module-level constants so the workflow wrapper at
# ``overlay/workflows/research_brief.py`` can key its
# ``error → skipped`` translation table on them by import — any future
# reword surfaces as an ``ImportError`` rather than a silent contract drift
# that strands external callers (Justfile smoke recipes, direct posters to
# ``/workflows/runs``) on the wrong envelope shape.
RESEARCH_BRIEF_EMPTY_QUERY_ERROR = "query cannot be empty"
RESEARCH_BRIEF_INVALID_LIMIT_ERROR = "limit must be positive"


def _clamp(value: int, *, minimum: int, maximum: int) -> int:
    """Clamp integer tool inputs to predictable output bounds."""
    return max(minimum, min(int(value), maximum))


class SemanticScholarClient:
    """Search papers, fetch metadata, walk the citation graph, and build research briefs.

    Wraps the upstream ``semanticscholar`` library against the
    Semantic Scholar Graph API
    (https://api.semanticscholar.org/api-docs/graph). The API is callable
    anonymously (heavily rate-limited) or with an ``x-api-key`` for
    higher quotas — the key is passed to the library only when the
    secret is set so anonymous calls don't accidentally hit a 401 on a
    stale placeholder.

    The library handles HTTP, retries (exponential backoff on HTTP 429
    by default), and typed-object parsing. This class adds DB
    persistence (``research_brief``), the PDF archive flow
    (``archive_paper``), and the agent-facing ``search`` envelope.
    """

    def __init__(
        self,
        api_key: str | None = None,
        timeout: float = 30.0,
        database_url: str | None = None,
    ) -> None:
        # Store the constructor-injected key as-is; resolve any fallback
        # lazily on first ``self.client`` access. Eager resolution here
        # runs during the ToolManager's _collect_methods() pass when
        # ToolContext.secrets is still empty, so the per-call secret
        # never lands in the underlying SemanticScholar instance.
        self._api_key = api_key
        self._timeout = timeout
        self._client: SemanticScholar | None = None
        # DATABASE_URL is owned by the API process, not an agent-facing
        # secret; mirror the resolution chain upstream company_context uses
        # so a constructor arg can override env or secret for tests.
        env_database_url = os.getenv("DATABASE_URL")  # noqa: TID251
        self._database_url = (
            database_url or env_database_url or secret("DATABASE_URL", default="")
        ).strip()

    def _require_database_url(self) -> str:
        if not self._database_url:
            raise RuntimeError("DATABASE_URL is required for semantic_scholar database access")
        return self._database_url

    async def _connect(self) -> asyncpg.Connection:
        return await asyncpg.connect(self._require_database_url(), command_timeout=30)

    def _acquire_pool_for_archive(self) -> Any:
        """Return an async context manager yielding a pool-like object for the archive flow.

        Default impl opens a fresh single-connection ``asyncpg`` connection
        (``fetchval`` / ``execute`` work the same on Connection and Pool) and
        closes it on exit, mirroring the per-call connect pattern in
        ``_research_brief_async``. Workflow handlers and tests override this
        method on the instance so they can reuse an existing pool — keeping
        the override surface as a single method (instead of threading the
        pool through every internal call) keeps the orchestration code clean.
        """
        database_url = self._require_database_url()

        class _ConnAsPool:
            async def __aenter__(self) -> Any:
                self._conn = await asyncpg.connect(database_url, command_timeout=60)
                return self._conn

            async def __aexit__(self, *exc: Any) -> None:
                await self._conn.close()

        return _ConnAsPool()

    def _get_api_key(self) -> str | None:
        """Get API key from instance, then sidecar secret, then anonymous.

        Returns ``None`` (not ``""``) when no key is available, so the
        ``SemanticScholar`` library treats the call as anonymous rather
        than sending an empty ``x-api-key`` header.
        """
        if self._api_key:
            return self._api_key
        key = secret("SEMANTIC_SCHOLAR_API_KEY", "")
        return key or None

    @property
    def client(self) -> SemanticScholar:
        """Lazy-initialized upstream library client.

        Built on first access so ``_get_api_key()`` resolves at request
        time (when ``ToolContext.secrets`` is populated), not at
        ``__init__`` time during the ToolManager's ``_collect_methods()``
        discovery pass.
        """
        if self._client is None:
            self._client = SemanticScholar(
                timeout=int(self._timeout),
                api_key=self._get_api_key(),
                retry=True,
            )
        return self._client

    def search_papers(
        self,
        query: str,
        limit: int = 10,
        year_from: int | None = None,
        fields: list[str] | None = None,
    ) -> list[Paper]:
        """Search papers by free-text query.

        Args:
            query: Free-text search query (e.g. "diffusion models protein design").
            limit: Max results, 1..100.
            year_from: Optional inclusive lower bound on publication year.
            fields: List of fields per the Graph API spec; defaults to
                :data:`DEFAULT_PAPER_FIELDS`.

        Returns:
            Upstream :class:`semanticscholar.Paper.Paper` instances. Use
            ``paper.raw_data`` at the agent boundary to recover the
            original JSON dict.
        """
        if not query or not query.strip():
            raise ValueError("query cannot be empty.")
        try:
            results = self.client.search_paper(
                query.strip(),
                limit=max(1, min(limit, 100)),
                fields=list(fields) if fields is not None else list(DEFAULT_PAPER_FIELDS),
                year=f"{year_from}-" if year_from is not None else None,
            )
        except SemanticScholarException as exc:
            raise RuntimeError(f"Semantic Scholar API error: {exc}") from exc
        # ``search_paper`` returns ``PaginatedResults`` (or a single
        # ``Paper`` when ``match_title=True``, which we never set). The
        # ``.items`` accessor on ``PaginatedResults`` exposes the first
        # page without forcing additional fetches — we trust the library's
        # ``limit`` to give us what we asked for.
        return list(results.items)

    def get_paper(
        self,
        paper_id: str,
        fields: list[str] | None = None,
    ) -> Paper:
        """Fetch metadata for a single paper.

        ``paper_id`` accepts any of the IDs the Graph API understands —
        Semantic Scholar IDs, DOIs (``DOI:10.x/y``), arXiv IDs (``arXiv:1234.5678``),
        and a few others. See the upstream docs for the full list.
        """
        if not paper_id or not paper_id.strip():
            raise ValueError("paper_id cannot be empty.")
        try:
            return self.client.get_paper(
                paper_id.strip(),
                fields=list(fields) if fields is not None else list(DEFAULT_PAPER_FIELDS),
            )
        except SemanticScholarException as exc:
            raise RuntimeError(f"Semantic Scholar API error: {exc}") from exc

    def get_references(
        self,
        paper_id: str,
        limit: int = 20,
        fields: list[str] | None = None,
    ) -> list[Paper]:
        """List the papers that the given paper cites.

        Returns a flat ``list[Paper]`` of the cited papers; the
        intermediate :class:`semanticscholar.Reference.Reference` wrapper
        (which carries citation context / intent fields) is stripped to
        keep the caller-facing shape symmetric with ``search_papers``.
        """
        if not paper_id or not paper_id.strip():
            raise ValueError("paper_id cannot be empty.")
        try:
            refs = self.client.get_paper_references(
                paper_id.strip(),
                limit=max(1, min(limit, 100)),
                fields=list(fields) if fields is not None else list(DEFAULT_REFERENCE_FIELDS),
            )
        except SemanticScholarException as exc:
            raise RuntimeError(f"Semantic Scholar API error: {exc}") from exc
        return [ref.paper for ref in refs.items if ref.paper is not None]

    def search(
        self,
        query: str,
        limit: int = DEFAULT_SEARCH_LIMIT,
        year_from: int | None = None,
    ) -> dict:
        """Search papers via the live Semantic Scholar Graph API.

        Agent-facing wrapper around ``search_papers`` that never raises —
        returns ``{"status": "error", "error": ...}`` on failure. Does not
        query ``company_context_documents``; use ``save_papers`` or
        ``research_brief`` to persist results for later retrieval.

        Args:
            query: Free-text search query.
            limit: Max results, 1..``MAX_SEARCH_LIMIT``.
            year_from: Optional inclusive lower bound on publication year.

        Returns:
            On success::

                {
                    "status": "ok",
                    "query": "<normalized query>",
                    "limit": <int>,
                    "year_from": <int | null>,
                    "count": <int>,
                    "results": [<S2 paper dict>, ...],
                }

            On failure, ``{"status": "error", "error": "<message>"}``.
        """
        normalized_query = query.strip() if query else ""
        if not normalized_query:
            return {"status": "error", "error": "query cannot be empty"}

        clamped_limit = _clamp(
            limit,
            minimum=1,
            maximum=MAX_SEARCH_LIMIT,
        )

        try:
            papers = self.search_papers(
                normalized_query,
                limit=clamped_limit,
                year_from=year_from,
            )
            # ``Paper(data)`` stores ``data`` by reference, so ``raw_data``
            # is the byte-for-byte original JSON dict the agent harness /
            # Slack renderer / CLI --json consumers depend on.
            return {
                "status": "ok",
                "query": normalized_query,
                "limit": clamped_limit,
                "year_from": year_from,
                "count": len(papers),
                "results": [p.raw_data for p in papers],
            }
        except Exception as exc:
            log.warning("semantic_scholar search failed", exc_info=True)
            return {"status": "error", "error": str(exc)}

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

    def archive_paper(
        self,
        paper_id: str,
        *,
        source_url: str | None = None,
    ) -> dict[str, Any]:
        """Download, parse, and persist a paper's PDF (agent-facing tool method).

        Resolves the PDF URL via :func:`pdf_fetch.derive_pdf_url`
        (``openAccessPdf.url`` first, arXiv fallback second), downloads with
        a 50 MiB cap, parses via the ``pymupdf4llm`` → ``pymupdf`` → ``pypdf``
        fallback chain, and persists three rows: a raw-bytes row in
        ``paper_archives``, a metadata row (``source_type="paper"``) in
        ``company_context_documents``, and a parsed-text row
        (``source_type="paper_fulltext"``, parented off the metadata row).

        Idempotent on ``(paper_id, pdf_sha256)`` — re-running on an unchanged
        PDF returns ``status="noop"`` without re-parsing or rewriting.

        Returns ``{"status": "completed" | "skipped" | "noop" | "error", ...}``.
        Never raises (catches at the boundary and returns an error envelope).
        """
        try:
            return asyncio.run(self._archive_paper_async(paper_id, source_url=source_url))
        # Boundary: agent-facing wrapper must never raise — translate to error envelope.
        except Exception as exc:
            log.warning("archive_paper_failed", exc_info=True)
            return {"status": "error", "paper_id": paper_id, "error": str(exc)}

    async def _research_brief_async(
        self,
        *,
        query: str,
        limit: int,
        year_from: int | None,
    ) -> dict[str, Any]:
        # Run the (retry-prone) S2 call and the pure rendering before
        # opening a DB connection. The brief has no data dependency on
        # the DB before S2 returns, so holding a real Postgres connection
        # idle through the upstream library's HTTP retries is pure cost.
        # Postgres ``max_connections`` is finite; we open a fresh
        # connection per call, so concurrent invocations would otherwise
        # pin one connection each for the duration of the S2 round trip.
        #
        # The upstream library's ``SemanticScholar`` client is synchronous
        # (the async sibling is a separate class); bouncing it through
        # ``asyncio.to_thread`` keeps the event loop responsive without
        # maintaining a parallel ``AsyncSemanticScholar`` path (mirrors
        # how the workflow handler in
        # ``overlay/workflows/research_brief.py`` invokes the tool method).
        papers = await asyncio.to_thread(
            self.search_papers,
            query=query,
            limit=limit,
            year_from=year_from,
        )

        conn = await self._connect()
        try:
            result = await persist_research_brief_from_papers(
                conn,
                query=query,
                papers=papers,
                year_from=year_from,
                limit=limit,
            )
            return {"status": "completed", **result}
        finally:
            await conn.close()

    async def _archive_paper_async(
        self,
        paper_id: str,
        *,
        source_url: str | None = None,
    ) -> dict[str, Any]:
        """Coroutine sibling of :meth:`archive_paper` for in-loop callers.

        Performs the same fetch → parse → persist pipeline but in the
        caller's running event loop. Workflow handlers reuse their pool by
        overriding ``_acquire_pool_for_archive`` on the instance.

        Returns the same envelope shape as :meth:`archive_paper`. Never raises
        for expected failure modes (HTTP error, parse error, oversized PDF,
        no PDF URL) — always returns ``{"status": "skipped" | "noop" | "error"
        | "completed", ...}``. Programming errors (asyncpg pool down, missing
        DATABASE_URL, etc.) propagate to the caller's wrapper.
        """
        normalized_id = (paper_id or "").strip()
        if not normalized_id:
            return {"status": "error", "paper_id": paper_id, "error": "paper_id cannot be empty"}

        try:
            paper = await asyncio.to_thread(self.get_paper, normalized_id)
        except (ValueError, RuntimeError) as exc:
            return {"status": "error", "paper_id": normalized_id, "error": str(exc)}

        url = source_url or pdf_fetch.derive_pdf_url(paper)
        if not url:
            return {"status": "skipped", "paper_id": normalized_id, "reason": "no_pdf_url"}

        try:
            data, mime = await asyncio.to_thread(
                pdf_fetch.download_pdf,
                url,
                timeout=PDF_DOWNLOAD_TIMEOUT_S,
                max_bytes=MAX_PDF_BYTES,
                user_agent=PDF_USER_AGENT,
            )
        except pdf_fetch.PdfTooLargeError:
            return {
                "status": "skipped",
                "paper_id": normalized_id,
                "reason": "too_large",
                "source_url": url,
            }
        except pdf_fetch.PdfFetchError as exc:
            return {
                "status": "error",
                "paper_id": normalized_id,
                "source_url": url,
                "error": str(exc),
            }

        pdf_sha256 = compute_pdf_sha256(data)

        async with self._acquire_pool_for_archive() as pool:
            existing = await pool.fetchval(
                "SELECT pdf_sha256 FROM paper_archives WHERE paper_id = $1",
                normalized_id,
            )
            if existing == pdf_sha256:
                return {
                    "status": "noop",
                    "paper_id": normalized_id,
                    "source_url": url,
                    "archive_action": "noop",
                    "pdf_sha256": pdf_sha256,
                }

            try:
                parsed_text, parser_used = await asyncio.to_thread(
                    pdf_parse.parse_pdf_to_markdown,
                    data,
                    ARCHIVE_PARSER_MIN_SIZE,
                )
            except pdf_parse.PdfParseError as exc:
                return {
                    "status": "error",
                    "paper_id": normalized_id,
                    "source_url": url,
                    "error": str(exc),
                }

            paper_doc = build_paper_document(paper)
            observe_document_size(paper_doc)
            paper_action = await upsert_document(pool, paper_doc)
            record_document_change(paper_doc, paper_action)

            fulltext_doc = build_fulltext_document(
                paper,
                parsed_text=parsed_text,
                parser_used=parser_used,
                truncated=False,
                pdf_sha256=pdf_sha256,
                source_url=url,
            )
            observe_document_size(fulltext_doc)
            fulltext_action = await upsert_document(pool, fulltext_doc)
            record_document_change(fulltext_doc, fulltext_action)

            # ``parsed_text`` here is the FULL parser output. Only the BM25
            # body in ``fulltext_doc`` is subject to the 1 MiB cap, so the
            # archive row's ``truncated`` flag stays False — re-rendering
            # from this row produces the same parser output we got today.
            archive_action = await upsert_paper_archive(
                pool,
                {
                    "paper_id": normalized_id,
                    "source_url": url,
                    "mime_type": mime,
                    "size_bytes": len(data),
                    "pdf_sha256": pdf_sha256,
                    "pdf_bytes": data,
                    "parsed_text": parsed_text,
                    "parser_used": parser_used,
                    "truncated": False,
                    "metadata": {
                        "paperId": normalized_id,
                        "url": paper_doc["url"],
                    },
                },
            )

        return {
            "status": "completed",
            "paper_id": normalized_id,
            "source_url": url,
            "parser_used": parser_used,
            "pdf_sha256": pdf_sha256,
            "size_bytes": len(data),
            "paper_document_id": paper_doc["document_id"],
            "paper_action": paper_action,
            "fulltext_document_id": fulltext_doc["document_id"],
            "fulltext_action": fulltext_action,
            "archive_action": archive_action,
        }

    def close(self) -> None:
        """Drop the cached library client.

        The upstream ``SemanticScholar`` instance owns an ``httpx``
        client internally and has no public ``close`` hook, so we just
        release our reference and let GC collect it. Kept as a method
        for backward compatibility with callers that use this class as a
        context manager.
        """
        self._client = None

    def __enter__(self) -> SemanticScholarClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


def _client() -> SemanticScholarClient:
    """Factory for tool loader."""
    return SemanticScholarClient()
