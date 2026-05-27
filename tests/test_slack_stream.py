"""Tests for Slack stream helpers."""

from __future__ import annotations

from packages.bfts_sdk.slack_stream import format_idea_markdown


def test_format_idea_markdown() -> None:
    md = format_idea_markdown(
        {
            "Title": "VFE-NCA",
            "Short Hypothesis": "Free-energy updates beat MSE.",
            "Experiments": ["Train 32x32", "Ablate damage"],
        }
    )
    assert "VFE-NCA" in md
    assert "Free-energy" in md
    assert "Train 32x32" in md
