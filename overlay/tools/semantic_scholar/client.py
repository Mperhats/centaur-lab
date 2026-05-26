"""Semantic Scholar tool client.

Three sync network methods that delegate to the upstream
``semanticscholar`` PyPI package — ``search_papers``, ``get_paper``,
``get_references`` — plus three agent-facing convenience methods
(``search``, ``research_brief``, ``archive_paper``) that wrap them
with the ``{"status": ...}`` envelope contract the Slack/sandbox tool
runtime depends on.

Persistence work (``research_brief``, ``archive_paper``) is delegated
to module-level helpers in ``centaur_lab``; this client opens a
short-lived asyncpg pool when invoked directly by an agent. The
``archive_papers`` and ``research_brief`` workflow handlers skip the
tool's persistence methods and call the same helpers directly with the
workflow's own pool, so the tool never has to bridge between
"caller-owns-pool" and "tool-opens-pool" — the workflow takes the
former path, the agent takes the latter.

The typed objects returned to callers are the upstream library's
``semanticscholar.Paper.Paper``. Wire-shape consumers (``search``
envelope, CLI ``--json``) recover the original JSON dict via
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
from centaur_lab.paper_archive import archive_paper_to_pool
from centaur_sdk import secret

log = logging.getLogger(__name__)

DEFAULT_PAPER_FIELDS: list[str] = [
    "title",
    "authors",
    "year",
    "abstract",
    "citationCount",
    "url",
    "openAccessPdf",
    # Needed so derive_pdf_url's arxiv fallback (which reads
    # externalIds["ArXiv"]) actually fires for non-OA papers — without
    # this, the Graph API omits externalIds and the fallback was dead
    # code on every real call.
    "externalIds",
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


def _clamp(value: int, *, minimum: int, maximum: int) -> int:
    return max(minimum, min(int(value), maximum))


class SemanticScholarClient:
    """Search papers, fetch metadata, walk the citation graph, build research briefs.

    Wraps the upstream ``semanticscholar`` library against the Graph
    API. Anonymous calls work (heavy rate limits); pass
    ``SEMANTIC_SCHOLAR_API_KEY`` via the secret sidecar for production
    quotas. The library handles HTTP, retries, and typed-object parsing.
    """

    def __init__(
        self,
        api_key: str | None = None,
        timeout: float = 30.0,
        database_url: str | None = None,
    ) -> None:
        # API key is resolved lazily on first ``self.client`` access; the
        # tool manager's ``_collect_methods()`` pass instantiates this
        # class while ``ToolContext.secrets`` is still empty.
        self._api_key = api_key
        self._timeout = timeout
        self._client: SemanticScholar | None = None
        env_database_url = os.getenv("DATABASE_URL")  # noqa: TID251
        self._database_url = (
            database_url or env_database_url or secret("DATABASE_URL", default="")
        ).strip()

    def _get_api_key(self) -> str | None:
        if self._api_key:
            return self._api_key
        return secret("SEMANTIC_SCHOLAR_API_KEY", "") or None

    @property
    def client(self) -> SemanticScholar:
        """Lazy-initialized upstream library client."""
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
        """Search papers by free-text query. Raises on API error."""
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
        return list(results.items)

    def get_paper(
        self,
        paper_id: str,
        fields: list[str] | None = None,
    ) -> Paper:
        """Fetch metadata for a single paper. Accepts S2/DOI/arXiv IDs."""
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
        """List papers cited by the given paper.

        Returns a flat ``list[Paper]``; the
        :class:`semanticscholar.Reference.Reference` wrapper (citation
        context / intent) is stripped to keep the shape symmetric with
        ``search_papers``.
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
        """Agent-facing search wrapper: never raises; returns an envelope.

        Returns ``{"status": "ok", "results": [<dict>...], ...}`` on
        success, ``{"status": "error", "error": ...}`` otherwise. Does
        not query ``company_context_documents``; use ``save_papers`` or
        ``research_brief`` to persist results.
        """
        normalized_query = query.strip() if query else ""
        if not normalized_query:
            return {"status": "error", "error": "query cannot be empty"}

        clamped_limit = _clamp(limit, minimum=1, maximum=MAX_SEARCH_LIMIT)
        try:
            papers = self.search_papers(
                normalized_query,
                limit=clamped_limit,
                year_from=year_from,
            )
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

    async def research_brief(
        self,
        query: str,
        limit: int = DEFAULT_RESEARCH_BRIEF_LIMIT,
        year_from: int | None = None,
    ) -> dict[str, Any]:
        """Build and persist a research brief on a topic.

        Opens a short-lived asyncpg pool, searches Semantic Scholar,
        renders a Markdown lit review, and writes the brief plus its
        citing papers to ``company_context_documents``. Idempotent on
        ``(query, year_from)`` via :func:`brief.brief_id_for`.

        Never raises — returns ``{"status": "error", ...}`` on failure.
        ``limit`` above the per-call ceiling is clamped, not rejected.
        Workflow callers should skip this and call
        :func:`centaur_lab.brief.persist_research_brief_from_papers`
        directly with their own pool.
        """
        normalized_query = query.strip() if query else ""
        if not normalized_query:
            return {"status": "error", "error": "query cannot be empty"}
        if limit <= 0:
            return {"status": "error", "error": "limit must be positive"}
        if not self._database_url:
            return {
                "status": "error",
                "error": "DATABASE_URL is required for semantic_scholar.research_brief",
            }

        clamped_limit = _clamp(limit, minimum=1, maximum=MAX_RESEARCH_BRIEF_LIMIT)

        try:
            papers = await asyncio.to_thread(
                self.search_papers,
                query=normalized_query,
                limit=clamped_limit,
                year_from=year_from,
            )
            conn = await asyncpg.connect(self._database_url, command_timeout=60)
            try:
                result = await persist_research_brief_from_papers(
                    conn,
                    query=normalized_query,
                    papers=papers,
                    year_from=year_from,
                    limit=clamped_limit,
                )
            finally:
                await conn.close()
            return {"status": "completed", **result}
        except Exception as exc:
            log.warning("semantic_scholar research_brief failed", exc_info=True)
            return {"status": "error", "error": str(exc)}

    async def archive_paper(
        self,
        paper_id: str,
        *,
        source_url: str | None = None,
    ) -> dict[str, Any]:
        """Download, parse, and persist a paper's PDF.

        Opens a short-lived asyncpg pool and runs the archive pipeline
        via :func:`centaur_lab.paper_archive.archive_paper_to_pool`.
        Idempotent on ``(paper_id, pdf_sha256)``. Never raises; returns
        ``{"status": "completed" | "skipped" | "noop" | "error", ...}``.
        Workflow callers with their own pool should call
        ``archive_paper_to_pool`` directly.
        """
        try:
            if not self._database_url:
                return {
                    "status": "error",
                    "paper_id": paper_id,
                    "error": "DATABASE_URL is required for semantic_scholar.archive_paper",
                }
            conn = await asyncpg.connect(self._database_url, command_timeout=60)
            try:
                return await archive_paper_to_pool(self, conn, paper_id, source_url=source_url)
            finally:
                await conn.close()
        except Exception as exc:
            log.warning("archive_paper_failed", exc_info=True)
            return {"status": "error", "paper_id": paper_id, "error": str(exc)}


def _client() -> SemanticScholarClient:
    """Factory for the tool loader."""
    return SemanticScholarClient()
