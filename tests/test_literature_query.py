"""Unit tests for Semantic Scholar query planning helpers."""

from __future__ import annotations

from packages.bfts_sdk.literature_query import (
    build_planner_user_prompt,
    dedupe_queries,
    normalize_planner_payload,
    queries_not_yet_tried,
)


def test_dedupe_queries_case_insensitive() -> None:
    out = dedupe_queries(
        ["PageRank", " pagerank ", "Graph Theory", "PageRank"],
        limit=4,
    )
    assert out == ["PageRank", "Graph Theory"]


def test_normalize_planner_payload() -> None:
    payload = normalize_planner_payload(
        {
            "queries": [" decentralized PageRank ", "PageRank graph theory", ""],
            "reason": " shorter keywords ",
        },
        query_limit=3,
    )
    assert payload["reason"] == "shorter keywords"
    assert payload["queries"] == ["decentralized PageRank", "PageRank graph theory"]


def test_queries_not_yet_tried() -> None:
    fresh = queries_not_yet_tried(
        ["PageRank", "decentralized PageRank", "Graph Theory"],
        prior_queries=["latest graph theory research related to decentralized PageRank"],
    )
    assert fresh == ["PageRank", "decentralized PageRank", "Graph Theory"]


def test_build_planner_user_prompt_includes_gaps() -> None:
    prompt = build_planner_user_prompt(
        topic="decentralized PageRank",
        prior_queries=["latest graph theory research related to decentralized PageRank"],
        prior_gaps=["Semantic Scholar returned zero papers for the original query."],
    )
    assert "prior_gaps" in prompt
    assert "decentralized PageRank" in prompt
