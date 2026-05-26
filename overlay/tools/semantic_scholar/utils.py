"""Tool-scoped helpers for the ``semantic_scholar`` tool.

Three pure helpers that the tool's network layer (and the workflows
that consume the tool's typed ``Paper`` objects) need to share without
pulling in the broader ``centaur_lab`` persistence library:

* :func:`derive_pdf_url` — pick the best PDF URL for a paper, or
  ``None``. Used by the archive pipeline before download.
* :func:`canonical_json` — stable JSON serialization for hashing and
  JSONB metadata columns. Byte-identical to upstream's
  ``api.runtime_control.canonical_json`` so content_hash identity holds
  across systems.
* :func:`content_hash` — SHA-256 over the canonical-JSON form of the
  given parts. Used by every projection that produces a
  ``content_hash`` column for ``company_context_documents``.

These helpers stay tool-scoped (not in ``tools/pdf/utils.py``) because
they all read or compose Semantic Scholar's typed shapes (``Paper``,
``externalIds``, ``openAccessPdf``); none of them are domain-agnostic.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from semanticscholar.Paper import Paper


def derive_pdf_url(paper: Paper) -> str | None:
    """Pick the best PDF URL for a Semantic Scholar :class:`Paper`, or ``None``.

    Preference order:

    1. ``openAccessPdf["url"]`` (when it's a non-empty stripped string).
    2. ``https://arxiv.org/pdf/{externalIds.ArXiv}.pdf`` (when an
       ArXiv ID is present and non-empty).

    Returns ``None`` when neither field is usable.

    The upstream ``semanticscholar`` library exposes ``openAccessPdf``
    and ``externalIds`` as plain dicts and returns ``None`` (not
    ``{}``) when the API response omitted the field — every access
    below normalises that.
    """
    open_access_pdf = paper.openAccessPdf or {}
    open_access_url = open_access_pdf.get("url")
    if open_access_url:
        stripped = str(open_access_url).strip()
        if stripped:
            return stripped

    external_ids = paper.externalIds or {}
    arxiv_id = external_ids.get("ArXiv")
    if arxiv_id:
        stripped = str(arxiv_id).strip()
        if stripped:
            return f"https://arxiv.org/pdf/{stripped}.pdf"

    return None


# We intentionally do not import api.runtime_control.canonical_json here so
# this module stays unit-testable outside the API pod. The argument list
# below is kept byte-identical to upstream's ``canonical_json``
# (``api.runtime_control.canonical_json``): same separators, ``sort_keys``,
# ``ensure_ascii=False`` so non-ASCII titles/authors hash to literal Unicode
# bytes rather than ``\uXXXX`` escapes, and no ``default=`` so non-
# serializable values raise ``TypeError`` instead of being silently coerced.
# Cross-system content_hash identity depends on this byte equivalence.
def canonical_json(value: Any) -> str:
    """Stable JSON form used for hashing and JSONB metadata serialization."""
    return json.dumps(value, separators=(",", ":"), sort_keys=True, ensure_ascii=False)


def content_hash(*parts: Any) -> str:
    """Hash projected document content so future syncs can detect changes cheaply."""
    return hashlib.sha256(canonical_json(parts).encode("utf-8")).hexdigest()
