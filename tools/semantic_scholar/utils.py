"""Tool-scoped pure helpers for the ``semantic_scholar`` tool.

Two pure helpers used by every projection in
``tools/semantic_scholar/projections/`` and by the persistence call
sites that produce ``content_hash`` columns for the
``company_context_documents`` table:

* :func:`canonical_json` — stable JSON serialization for hashing and
  JSONB metadata columns. Byte-identical to upstream's
  ``api.runtime_control.canonical_json`` so ``content_hash`` identity
  holds across systems.
* :func:`content_hash` — SHA-256 over the canonical-JSON form of the
  given parts. Used by every projection that produces a
  ``content_hash`` column for ``company_context_documents``.

These helpers stay tool-scoped because they compose Semantic Scholar
projection shapes; nothing here is domain-agnostic enough to live in a
shared overlay package.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


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
