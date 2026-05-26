"""Unit tests for ``pdf_fetch.derive_pdf_url``.

``download_pdf`` is exercised against ``httpx.MockTransport`` in the
integration suite; the URL-derivation logic, by contrast, is pure and
warrants the cheap unit-test coverage that catches typo-level field
access regressions (e.g. ``paper.openAccessPdf.url`` when the upstream
library exposes ``openAccessPdf`` as a plain dict).
"""

from __future__ import annotations

from semanticscholar.Paper import Paper

from semantic_scholar.pdf_fetch import derive_pdf_url


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


def test_prefers_open_access_pdf_url() -> None:
    """``openAccessPdf["url"]`` wins over an arXiv fallback when both present."""
    paper = _paper(
        open_access_pdf={"url": "https://oa.example/paper.pdf"},
        external_ids={"ArXiv": "1234.5678"},
    )

    assert derive_pdf_url(paper) == "https://oa.example/paper.pdf"


def test_strips_whitespace_from_open_access_url() -> None:
    paper = _paper(open_access_pdf={"url": "  https://oa.example/x.pdf  "})

    assert derive_pdf_url(paper) == "https://oa.example/x.pdf"


def test_falls_back_to_arxiv_when_open_access_missing() -> None:
    """No openAccessPdf → arXiv id rendered into the canonical PDF URL."""
    paper = _paper(open_access_pdf=None, external_ids={"ArXiv": "2401.12345"})

    assert derive_pdf_url(paper) == "https://arxiv.org/pdf/2401.12345.pdf"


def test_falls_back_to_arxiv_when_open_access_url_empty() -> None:
    """Empty/whitespace ``openAccessPdf.url`` falls through to arXiv too."""
    paper = _paper(
        open_access_pdf={"url": "   "},
        external_ids={"ArXiv": "2401.12345"},
    )

    assert derive_pdf_url(paper) == "https://arxiv.org/pdf/2401.12345.pdf"


def test_returns_none_when_neither_source_available() -> None:
    paper = _paper(
        open_access_pdf=None,
        external_ids={"DOI": "10.0/x"},
    )

    assert derive_pdf_url(paper) is None


def test_handles_paper_with_no_external_ids_attribute() -> None:
    """``Paper`` returns ``None`` (not ``{}``) when the response omitted externalIds."""
    paper = _paper(open_access_pdf=None, external_ids=None)

    assert derive_pdf_url(paper) is None


def test_returns_none_when_open_access_pdf_dict_lacks_url_key() -> None:
    """Some publishers return ``openAccessPdf: {}`` with status only — no URL."""
    paper = _paper(open_access_pdf={"status": "GREEN"}, external_ids=None)

    assert derive_pdf_url(paper) is None
