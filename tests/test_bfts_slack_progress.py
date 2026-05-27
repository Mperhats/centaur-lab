"""Unit tests for BFTS Slack progress formatting."""

from __future__ import annotations

from packages.bfts_sdk.slack_delivery import (
    enrich_run_input_from_headers,
    format_progress_message,
    format_search_config_line,
    format_tree_progress_line,
)


def test_format_search_config_line_shows_sources() -> None:
    line = format_search_config_line(
        num_drafts=4,
        num_seeds=2,
        num_workers=2,
        sources={
            "num_drafts": "default",
            "num_seeds": "env",
            "num_workers": "default",
        },
    )
    assert "4 trees" in line
    assert "num_seeds, env" in line


def test_enrich_run_input_from_header_thread_key() -> None:
    enriched = enrich_run_input_from_headers(
        header_thread_key="slack:C1:1700000000.000100",
        run_input={"idea": {}},
    )
    assert enriched["thread_key"] == "slack:C1:1700000000.000100"
    assert enriched["delivery"] == {
        "platform": "slack",
        "channel": "C1",
        "thread_ts": "1700000000.000100",
    }


def test_enrich_does_not_override_explicit_delivery() -> None:
    enriched = enrich_run_input_from_headers(
        header_thread_key="slack:C1:1700000000.000100",
        run_input={
            "delivery": {
                "platform": "slack",
                "channel": "C_OVERRIDE",
                "thread_ts": "2.0",
                "recipient_user_id": "U1",
            },
        },
    )
    assert enriched["delivery"]["channel"] == "C_OVERRIDE"
    assert enriched["delivery"]["recipient_user_id"] == "U1"


def test_format_tree_progress_completed() -> None:
    line = format_tree_progress_line(
        tree_index=0,
        status="completed",
        output={
            "best_node_id": "node-abc",
            "node_count": 12,
            "best_metric_json": {"mse": 0.04},
        },
    )
    assert "tree 0" in line
    assert "completed" in line
    assert "node-abc" in line
    assert "mse=0.04" in line


def test_format_progress_message_launched() -> None:
    text = format_progress_message(
        run_id="wfr_test",
        phase="launched",
        children=[
            {"tree_index": 0, "run_id": "wfr_test:tree:0"},
            {"tree_index": 1, "run_id": "wfr_test:tree:1"},
        ],
        child_results=[],
    )
    assert "wfr_test" in text
    assert "2 trees launched" in text
    assert "tree 0: running" in text
    assert "tree 1: running" in text


def test_format_progress_message_partial() -> None:
    text = format_progress_message(
        run_id="wfr_test",
        phase="progress",
        children=[
            {"tree_index": 0, "run_id": "wfr_test:tree:0"},
            {"tree_index": 1, "run_id": "wfr_test:tree:1"},
        ],
        child_results=[
            {
                "status": "completed",
                "output_json": {
                    "best_node_id": "n1",
                    "node_count": 5,
                    "best_metric_json": {"metric": 1.0},
                },
            },
        ],
    )
    assert "1/2 trees finished" in text
    assert "tree 0: completed" in text
    assert "tree 1: running" in text
