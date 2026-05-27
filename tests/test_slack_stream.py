"""Tests for Slack stream helpers."""

from __future__ import annotations

from packages.bfts_sdk.slack_stream import (
    format_bfts_stream_intro,
    format_idea_markdown,
    format_research_stream_intro,
)


def test_format_research_stream_intro() -> None:
    intro = format_research_stream_intro("VFE-NCA")
    assert "Step 1 of 2" in intro
    assert "VFE-NCA" in intro
    assert "separate message" in intro


def test_format_bfts_stream_intro() -> None:
    intro = format_bfts_stream_intro("My Idea")
    assert "Step 2 of 2" in intro
    assert "My Idea" in intro


def test_format_idea_markdown() -> None:
    md = format_idea_markdown(
        {
            "Title": "VFE-NCA",
            "Short Hypothesis": "Free-energy updates beat MSE.",
            "Experiments": ["Train 32x32", "Ablate damage"],
        }
    )
    assert "Research idea" in md
    assert "VFE-NCA" in md
    assert "Free-energy" in md
    assert "• Train 32x32" in md
