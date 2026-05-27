"""Tests for compact research-brief rendering."""

from __future__ import annotations

from tools.semantic_scholar.projections.brief import render_brief_compact


def test_render_brief_compact_truncates_and_numbers() -> None:
    papers = [
        {
            "title": "Paper A",
            "year": 2024,
            "paperId": "abc123",
            "abstract": "x" * 200,
        },
        {"title": "Paper B", "abstract": ""},
    ]
    md = render_brief_compact("nca damage", papers, max_papers=2)
    assert "*Literature* — nca damage" in md
    assert "1. <https://www.semanticscholar.org/paper/abc123|Paper A> (2024)" in md
    assert "2. *Paper B*" in md
    assert len(md) < 500


def test_render_brief_compact_uses_slack_links() -> None:
    papers = [
        {
            "title": "Linked Paper",
            "url": "https://example.com/paper",
            "year": 2023,
            "abstract": "Short abstract.",
        },
    ]
    md = render_brief_compact("topic", papers)
    assert "<https://example.com/paper|Linked Paper> (2023)" in md
