"""Tests for compact research-brief rendering."""

from __future__ import annotations

from tools.semantic_scholar.projections.brief import render_brief_compact


def test_render_brief_compact_truncates_and_numbers() -> None:
    papers = [
        {
            "title": "Paper A",
            "year": 2024,
            "abstract": "x" * 200,
        },
        {"title": "Paper B", "abstract": ""},
    ]
    md = render_brief_compact("nca damage", papers, max_papers=2)
    assert "**Research brief**" in md
    assert "nca damage" in md
    assert "1. **Paper A** (2024)" in md
    assert "2. **Paper B**" in md
    assert len(md) < 800
