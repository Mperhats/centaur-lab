"""Semantic Scholar tool client.

Three sync network methods that delegate to the upstream
``semanticscholar`` PyPI package — ``search_papers``, ``get_paper``,
``get_references`` — plus three agent-facing convenience methods
(``search``, ``research_brief``, ``archive_paper``) that wrap them
with the ``{"status": ...}`` envelope contract the Slack/sandbox tool
runtime depends on.

This module is read-only with respect to Postgres. ``research_brief``
and ``archive_paper`` return *projection bundles* — dicts shaped for
the inlined ``_upsert_document`` / ``_upsert_paper_archive`` helpers in
each workflow handler — and never open a pool of their own. Workflow
handlers in ``overlay/workflows/`` own all DB writes; the agent-facing
contract is "tool returns the rows, caller persists them".

The typed objects returned by ``search_papers`` / ``get_paper`` /
``get_references`` are the upstream library's
``semanticscholar.Paper.Paper``. Wire-shape consumers (``search``
envelope, CLI ``--json``) recover the original JSON dict via
``Paper.raw_data``; ``Paper(data)`` stores ``data`` by reference so a
freshly-constructed paper round-trips byte-for-byte.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Final

from pdf.fetch.http import (
    PdfFetchError,
    PdfHttpError,
    PdfNetworkError,
    PdfNotPdfError,
    PdfTooLargeError,
    download_pdf,
)
from pdf.parse.markdown import (
    PdfInsufficientTextError,
    PdfParseError,
    parse_pdf,
)
from pdf.utils import compute_pdf_sha256
from semanticscholar import SemanticScholar
from semanticscholar.Paper import Paper
from semanticscholar.SemanticScholarException import SemanticScholarException

from centaur_sdk import secret
from semantic_scholar.projections.archive import build_paper_archive_row
from semantic_scholar.projections.brief import build_brief_document, render_brief
from semantic_scholar.projections.fulltext import build_fulltext_document
from semantic_scholar.projections.paper import build_paper_document
from semantic_scholar.utils import derive_pdf_url

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

# Archive-pipeline knobs. Held on the client (not the workflow) so the
# agent-facing ``archive_paper`` method ships sensible defaults without
# forcing every caller to repeat the constants.
MAX_PDF_BYTES: Final[int] = 50 * 1024 * 1024
PDF_DOWNLOAD_TIMEOUT_S: Final[float] = 60.0
PDF_USER_AGENT: Final[str] = "centaur-scientist/0.1 (paper-archive; +https://centaur.run)"
ARCHIVE_PARSER_MIN_SIZE: Final[int] = 100


def _clamp(value: int, *, minimum: int, maximum: int) -> int:
    return max(minimum, min(int(value), maximum))


class SemanticScholarClient:
    """Search papers, fetch metadata, walk the citation graph, build research-brief bundles.

    Wraps the upstream ``semanticscholar`` library against the Graph
    API. Anonymous calls work (heavy rate limits); pass
    ``SEMANTIC_SCHOLAR_API_KEY`` via the secret sidecar for production
    quotas. The library handles HTTP, retries, and typed-object parsing.

    The two persistence-adjacent methods (``research_brief``,
    ``archive_paper``) return projection bundles — pure dicts shaped for
    the workflow-owned upsert SQL. They never open a pool of their own.
    """

    def __init__(self, api_key: str | None = None, timeout: float = 30.0) -> None:
        # API key is resolved lazily on first ``self.client`` access; the
        # tool manager's ``_collect_methods()`` pass instantiates this
        # class while ``ToolContext.secrets`` is still empty.
        self._api_key = api_key
        self._timeout = timeout
        self._client: SemanticScholar | None = None

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
        """Search Semantic Scholar, render a Markdown brief, project to DB-row dicts.

        Returns a bundle the workflow handler persists; never opens a
        pool or writes to Postgres. Idempotent inputs produce identical
        bundles — the workflow's ``_upsert_document`` short-circuits on
        unchanged ``content_hash``.

        Bundle shape on success::

            {
                "status": "ok",
                "query": str,
                "year_from": int | None,
                "limit": int,
                "results_count": int,
                "markdown": str,
                "brief_doc": dict,          # company_context_documents row
                "paper_docs": list[dict],   # each parent_document_id ==
                                            # brief_doc["document_id"]
            }

        On error::

            {"status": "error", "query": ..., "error": "..."}

        Never raises. ``limit`` above
        :data:`MAX_RESEARCH_BRIEF_LIMIT` is clamped, not rejected.
        """
        normalized_query = query.strip() if query else ""
        if not normalized_query:
            return {"status": "error", "query": query, "error": "query cannot be empty"}
        if limit <= 0:
            return {
                "status": "error",
                "query": normalized_query,
                "error": "limit must be positive",
            }

        clamped_limit = _clamp(limit, minimum=1, maximum=MAX_RESEARCH_BRIEF_LIMIT)

        try:
            papers = await asyncio.to_thread(
                self.search_papers,
                normalized_query,
                limit=clamped_limit,
                year_from=year_from,
            )
        except Exception as exc:
            log.warning("semantic_scholar research_brief search failed", exc_info=True)
            return {"status": "error", "query": normalized_query, "error": str(exc)}

        markdown = render_brief(normalized_query, year_from, papers)
        brief_doc = build_brief_document(
            normalized_query, year_from, clamped_limit, papers, markdown
        )

        paper_docs: list[dict[str, Any]] = []
        for paper in papers:
            try:
                paper_docs.append(
                    build_paper_document(
                        paper,
                        query=normalized_query,
                        parent_document_id=brief_doc["document_id"],
                    )
                )
            except ValueError:
                # Mirrors the pre-bundle behaviour: a paper missing paperId
                # cannot be projected (no stable primary key), so we drop
                # it from the bundle and let the brief itself carry the
                # un-projectable paper's metadata via ``paper_ids``.
                continue

        return {
            "status": "ok",
            "query": normalized_query,
            "year_from": year_from,
            "limit": clamped_limit,
            "results_count": len(papers),
            "markdown": markdown,
            "brief_doc": brief_doc,
            "paper_docs": paper_docs,
        }

    async def archive_paper(
        self,
        paper_id: str,
        *,
        source_url: str | None = None,
    ) -> dict[str, Any]:
        """Fetch metadata + PDF for a paper, project to DB-row dicts. No DB.

        Returns a bundle the caller persists. Never raises.

        Bundle shape on success::

            {
                "status": "ok",
                "paper_id": str,
                "pdf_sha256": str,
                "source_url": str,
                "size_bytes": int,
                "mime_type": str,
                "parser_used": str,
                "paper_doc": dict,          # company_context_documents row
                "fulltext_doc": dict,       # company_context_documents row
                "archive_row": dict,        # paper_archives row
            }

        Skipped / error shapes::

            {"status": "skipped", "paper_id", "reason": "no_pdf_url"}
            {"status": "skipped", "paper_id", "source_url",
             "reason": "too_large", "max_bytes", "received_bytes"}
            {"status": "error",   "paper_id", "stage": "metadata"|"fetch"|"parse",
             "reason": "<code>", "source_url"?: str, "error": str, ...}
        """
        normalized_id = (paper_id or "").strip()
        if not normalized_id:
            return {
                "status": "error",
                "paper_id": paper_id,
                "stage": "metadata",
                "reason": "empty_paper_id",
                "error": "paper_id cannot be empty",
            }

        try:
            paper = await asyncio.to_thread(self.get_paper, normalized_id)
        except (RuntimeError, ValueError) as exc:
            return {
                "status": "error",
                "paper_id": normalized_id,
                "stage": "metadata",
                "reason": "fetch_failed",
                "error": str(exc),
            }

        url = source_url or derive_pdf_url(paper)
        if not url:
            return {
                "status": "skipped",
                "paper_id": normalized_id,
                "reason": "no_pdf_url",
            }

        try:
            data, mime = await asyncio.to_thread(
                download_pdf,
                url,
                timeout=PDF_DOWNLOAD_TIMEOUT_S,
                max_bytes=MAX_PDF_BYTES,
                user_agent=PDF_USER_AGENT,
            )
        except PdfTooLargeError as exc:
            return {
                "status": "skipped",
                "paper_id": normalized_id,
                "source_url": url,
                "reason": "too_large",
                "max_bytes": exc.max_bytes,
                "received_bytes": exc.received_bytes,
            }
        except (PdfHttpError, PdfNetworkError, PdfNotPdfError, PdfFetchError) as exc:
            return {
                "status": "error",
                "paper_id": normalized_id,
                "source_url": url,
                "stage": "fetch",
                "reason": exc.reason,
                "error": str(exc),
            }

        pdf_sha256 = compute_pdf_sha256(data)

        try:
            parsed_text, parser_used = await asyncio.to_thread(
                parse_pdf,
                data,
                min_size=ARCHIVE_PARSER_MIN_SIZE,
            )
        except (PdfInsufficientTextError, PdfParseError) as exc:
            return {
                "status": "error",
                "paper_id": normalized_id,
                "source_url": url,
                "stage": "parse",
                "reason": exc.reason,
                "error": str(exc),
            }

        paper_doc = build_paper_document(paper)
        fulltext_doc = build_fulltext_document(
            paper,
            parsed_text=parsed_text,
            parser_used=parser_used,
            truncated=False,
            pdf_sha256=pdf_sha256,
            source_url=url,
        )
        archive_row = build_paper_archive_row(
            paper,
            data=data,
            mime=mime,
            pdf_sha256=pdf_sha256,
            parsed_text=parsed_text,
            parser_used=parser_used,
            source_url=url,
            truncated=False,
        )

        return {
            "status": "ok",
            "paper_id": normalized_id,
            "pdf_sha256": pdf_sha256,
            "source_url": url,
            "size_bytes": len(data),
            "mime_type": mime,
            "parser_used": parser_used,
            "paper_doc": paper_doc,
            "fulltext_doc": fulltext_doc,
            "archive_row": archive_row,
        }


def _client() -> SemanticScholarClient:
    """Factory for the tool loader."""
    return SemanticScholarClient()
