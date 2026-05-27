"""Unit tests for research BFTS defaults and run-input builder."""

from __future__ import annotations

from packages.bfts_sdk.research import (
    DEFAULT_RESEARCH_NUM_DRAFTS,
    DEFAULT_RESEARCH_NUM_SEEDS,
    DEFAULT_RESEARCH_NUM_WORKERS,
    build_bfts_run_input,
)


def test_build_bfts_run_input_applies_research_defaults() -> None:
    idea = {"Name": "x", "Title": "T", "Short Hypothesis": "h", "Experiments": ["e"]}
    body = build_bfts_run_input(idea=idea)
    assert body["idea"] == idea
    assert body["num_seeds"] == DEFAULT_RESEARCH_NUM_SEEDS
    assert body["num_drafts"] == DEFAULT_RESEARCH_NUM_DRAFTS
    assert body["num_workers"] == DEFAULT_RESEARCH_NUM_WORKERS


def test_build_bfts_run_input_merges_slack_from_parent_run_input() -> None:
    idea = {"Name": "x", "Title": "T", "Short Hypothesis": "h", "Experiments": ["e"]}
    body = build_bfts_run_input(
        idea=idea,
        run_input={
            "thread_key": "slack:C1:1.0",
            "delivery": {"platform": "slack", "channel": "C1", "thread_ts": "1.0"},
        },
    )
    assert body["thread_key"] == "slack:C1:1.0"
    assert body["delivery"]["channel"] == "C1"


def test_build_bfts_run_input_explicit_override() -> None:
    idea = {"Name": "x", "Title": "T", "Short Hypothesis": "h", "Experiments": ["e"]}
    body = build_bfts_run_input(idea=idea, num_seeds=5, num_drafts=1, num_workers=2)
    assert body["num_seeds"] == 5
    assert body["num_drafts"] == 1
    assert body["num_workers"] == 2
