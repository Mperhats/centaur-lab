"""Unit tests for ``semantic_scholar.utils`` — pure tool-scoped helpers.

``derive_pdf_url`` is also exercised by ``test_pdf_fetch.py`` (the
legacy location still re-exports the same logic until Task 3 removes
``pdf_fetch.py``); the cases here are intentionally narrower and
exercise the new module's import path so consumers of the post-refactor
``utils`` entry point have a contract test of their own.
"""

from __future__ import annotations

import hashlib
import json

import pytest
from semanticscholar.Paper import Paper

from semantic_scholar.utils import canonical_json, content_hash, derive_pdf_url


def _paper(
    *,
    open_access_pdf: dict | None = None,
    external_ids: dict | None = None,
) -> Paper:
    """Wire-shape paper with only the fields ``derive_pdf_url`` reads."""
    payload: dict = {"paperId": "test"}
    if open_access_pdf is not None:
        payload["openAccessPdf"] = open_access_pdf
    if external_ids is not None:
        payload["externalIds"] = external_ids
    return Paper(payload)


# ---------------------------------------------------------------------------
# derive_pdf_url
# ---------------------------------------------------------------------------


def test_derive_pdf_url_prefers_open_access_over_arxiv() -> None:
    paper = _paper(
        open_access_pdf={"url": "https://oa.example/paper.pdf"},
        external_ids={"ArXiv": "1234.5678"},
    )

    assert derive_pdf_url(paper) == "https://oa.example/paper.pdf"


def test_derive_pdf_url_falls_back_to_arxiv_when_open_access_missing() -> None:
    paper = _paper(open_access_pdf=None, external_ids={"ArXiv": "2401.12345"})

    assert derive_pdf_url(paper) == "https://arxiv.org/pdf/2401.12345.pdf"


def test_derive_pdf_url_returns_none_when_neither_available() -> None:
    paper = _paper(open_access_pdf=None, external_ids=None)

    assert derive_pdf_url(paper) is None


def test_derive_pdf_url_returns_none_when_both_empty() -> None:
    """Empty/whitespace strings count as absent for both sources."""
    paper = _paper(
        open_access_pdf={"url": "   "},
        external_ids={"ArXiv": "   "},
    )

    assert derive_pdf_url(paper) is None


def test_derive_pdf_url_returns_none_when_open_access_dict_lacks_url() -> None:
    paper = _paper(open_access_pdf={"status": "GREEN"}, external_ids=None)

    assert derive_pdf_url(paper) is None


# ---------------------------------------------------------------------------
# canonical_json
# ---------------------------------------------------------------------------


def test_canonical_json_sorts_keys() -> None:
    """sort_keys is what makes the hash agree across systems for the same dict."""
    a = {"z": 1, "a": 2, "m": 3}

    out = canonical_json(a)

    assert out == '{"a":2,"m":3,"z":1}'


def test_canonical_json_preserves_non_ascii_literally() -> None:
    """ensure_ascii=False is load-bearing for cross-system hash agreement."""
    out = canonical_json({"title": "深層学習"})

    assert "深層学習" in out
    assert "\\u" not in out


def test_canonical_json_matches_upstream_byte_form() -> None:
    """Locks the exact serializer config: separators, sort_keys, ensure_ascii."""
    value = {"b": 2, "a": 1, "list": [3, 1, 2]}

    out = canonical_json(value)
    upstream = json.dumps(value, separators=(",", ":"), sort_keys=True, ensure_ascii=False)

    assert out == upstream


def test_canonical_json_raises_typeerror_on_non_serializable() -> None:
    """No ``default=`` shim — non-serializable values must surface, not be silently coerced."""

    class Unserializable:
        pass

    with pytest.raises(TypeError):
        canonical_json({"x": Unserializable()})


# ---------------------------------------------------------------------------
# content_hash
# ---------------------------------------------------------------------------


def test_content_hash_is_deterministic() -> None:
    assert content_hash("a", "b", 1) == content_hash("a", "b", 1)


def test_content_hash_is_sensitive_to_argument_order() -> None:
    assert content_hash("a", "b") != content_hash("b", "a")


def test_content_hash_is_sensitive_to_value_changes() -> None:
    assert content_hash("a", "b") != content_hash("a", "B")


def test_content_hash_matches_sha256_of_canonical_json_tuple() -> None:
    """Lock the byte form: SHA-256 over canonical_json of the *tuple* of parts."""
    parts = ("title", "body", "url", {"k": "v"})

    expected = hashlib.sha256(canonical_json(parts).encode("utf-8")).hexdigest()

    assert content_hash(*parts) == expected
