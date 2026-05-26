"""Pydantic models for the Semantic Scholar Graph API paper surface.

Lives in ``centaur_lab`` (not the tool package) because the projection
helpers in this same package — ``build_paper_document``,
``build_fulltext_document``, ``persist_research_brief_from_papers`` —
consume these models, and the dependency direction is
``tools.semantic_scholar`` → ``centaur_lab``, not the other way around.

Design notes:

- ``model_config = ConfigDict(extra="allow")`` on every model so unknown
  fields S2 ships in a future API revision flow through to consumers
  (CLI ``--json`` output, the ``search`` envelope) without a model
  bump. ``__pydantic_extra__`` is included by ``model_dump`` even when
  ``exclude_unset=True``.
- All declared fields default to ``None`` / empty so partial S2
  responses (search returns minimal fields; the per-paper endpoint
  returns the full set) parse without ``ValidationError``.
- ``model_dump(exclude_unset=True)`` at the agent boundary preserves
  the wire shape today's consumers depend on (``result["results"]``
  matches what S2 sent, byte-for-byte, when the agent only set the
  fields S2 actually returned).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class Author(BaseModel):
    """Author entry inside a Semantic Scholar ``Paper.authors`` list."""

    model_config = ConfigDict(extra="allow")

    authorId: str | None = None
    name: str | None = None


class OpenAccessPdf(BaseModel):
    """``Paper.openAccessPdf`` envelope (URL + status colour code)."""

    model_config = ConfigDict(extra="allow")

    url: str | None = None
    status: str | None = None


class Paper(BaseModel):
    """A Semantic Scholar paper as returned by the Graph API.

    Only ``paperId`` is logically required by downstream projection
    helpers (it's the stable primary key), but Pydantic still defaults
    it to ``None`` so a malformed response surfaces as a downstream
    ``ValueError`` from ``build_paper_document`` rather than a parse-
    time crash mid-batch (matches today's ``raise ValueError("paper.paperId
    is required ...")`` contract).
    """

    model_config = ConfigDict(extra="allow")

    paperId: str | None = None
    title: str | None = None
    authors: list[Author] = []
    year: int | None = None
    abstract: str | None = None
    citationCount: int = 0
    venue: str | None = None
    url: str | None = None
    openAccessPdf: OpenAccessPdf | None = None
    externalIds: dict[str, str | None] = {}
