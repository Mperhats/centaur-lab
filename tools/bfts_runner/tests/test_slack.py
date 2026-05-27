"""Unit tests for ``tools.bfts_runner.slack`` helpers."""

from __future__ import annotations

from pathlib import Path

from tools.bfts_runner.slack.format import (
    format_empty_literature_thread_message,
    format_failure_thread_message,
    format_idea_markdown,
    format_progress_message,
    format_research_brief_thread_message,
    format_search_config_line,
    format_tree_progress_line,
    slack_mention_prefix,
)
from tools.bfts_runner.slack.post import (
    delivery_from_thread_key,
    enrich_run_input_from_headers,
    resolve_slack_delivery,
    workflow_run_error_text,
    workflow_run_failed,
)
from workflows.bfts_root import Input


def test_bfts_runner_client_imports_slack_enrich() -> None:
    source = Path("tools/bfts_runner/client.py").read_text(encoding="utf-8")
    assert "from tools.bfts_runner.slack.post import enrich_run_input_from_headers" in source
    assert "enrich_run_input_from_headers(" in source


def test_delivery_from_slack_thread_key() -> None:
    key = "slack:C0AJ07U8Z1N:1773364194.179929"
    assert delivery_from_thread_key(key) == {
        "platform": "slack",
        "channel": "C0AJ07U8Z1N",
        "thread_ts": "1773364194.179929",
    }


def test_delivery_from_four_part_slack_thread_key() -> None:
    assert delivery_from_thread_key(
        "slack:TKW6CBDSB:C0B5Y8J1K1T:1779861892.728879",
    ) == {
        "platform": "slack",
        "recipient_team_id": "TKW6CBDSB",
        "channel": "C0B5Y8J1K1T",
        "thread_ts": "1779861892.728879",
    }


def test_delivery_from_non_slack_thread_key_returns_none() -> None:
    assert delivery_from_thread_key("workflow:wfr_abc:gap") is None


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


def test_resolve_slack_delivery_from_explicit_delivery() -> None:
    inp = Input(
        delivery={
            "platform": "slack",
            "channel": "C123",
            "thread_ts": "1.0",
            "recipient_user_id": "U456",
        }
    )
    assert resolve_slack_delivery(
        explicit_delivery=inp.delivery,
        run_input={},
        explicit_thread_key=inp.thread_key,
    ) == {
        "platform": "slack",
        "channel": "C123",
        "thread_ts": "1.0",
        "recipient_user_id": "U456",
    }


def test_resolve_slack_delivery_derives_from_thread_key() -> None:
    inp = Input(thread_key="slack:C999:1700000000.000100")
    assert resolve_slack_delivery(
        explicit_delivery=inp.delivery,
        run_input={},
        explicit_thread_key=inp.thread_key,
    ) == {
        "platform": "slack",
        "channel": "C999",
        "thread_ts": "1700000000.000100",
    }


def test_resolve_slack_delivery_explicit_overrides_thread_key() -> None:
    inp = Input(
        thread_key="slack:C999:1700000000.000100",
        delivery={
            "platform": "slack",
            "channel": "C_OVERRIDE",
            "thread_ts": "2.0",
        },
    )
    resolved = resolve_slack_delivery(
        explicit_delivery=inp.delivery,
        run_input={},
        explicit_thread_key=inp.thread_key,
    )
    assert resolved["channel"] == "C_OVERRIDE"
    assert resolved["thread_ts"] == "2.0"


def test_slack_mention_prefix() -> None:
    assert slack_mention_prefix({"recipient_user_id": "U1"}) == "<@U1> "
    assert slack_mention_prefix({}) == ""
    assert slack_mention_prefix(None) == ""


def test_format_research_brief_thread_message_compact() -> None:
    compact = (
        "*Literature* — active inference\n\n"
        "1. <https://example.com|Paper A> (2024) — One-liner."
    )
    msg = format_research_brief_thread_message(
        topic="active inference",
        markdown=compact,
        run_id="wfr_718932ca5ff74a67",
    )
    assert msg == compact
    assert "wfr_718932ca5ff74a67" not in msg
    assert "Paper A" in msg
    assert "Research pipeline" not in msg


def test_format_research_brief_thread_message_notes_refined_query() -> None:
    compact = "*Literature* — PageRank\n\n1. <https://example.com|Paper A> (2024)."
    msg = format_research_brief_thread_message(
        topic="latest graph theory research related to decentralized PageRank",
        search_query="decentralized PageRank",
        markdown=compact,
    )
    assert "searched with:" in msg
    assert "*decentralized PageRank*" in msg
    assert compact in msg


def test_format_research_brief_thread_message_empty_falls_back() -> None:
    msg = format_research_brief_thread_message(
        topic="active inference",
        markdown="",
    )
    assert "*Literature* — active inference" in msg
    assert "_No papers found._" in msg


def test_format_empty_literature_thread_message() -> None:
    msg = format_empty_literature_thread_message(
        topic="latest graph theory research related to decentralized PageRank",
        queries_tried=[
            "latest graph theory research related to decentralized PageRank",
            "decentralized PageRank",
            "PageRank graph theory",
        ],
    )
    assert "Semantic Scholar returned **no papers** after trying multiple" in msg
    assert "decentralized PageRank" in msg
    assert "Queries tried:" in msg


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
    assert "*VFE-NCA*" in md
    assert "Free-energy" in md
    assert "• Train 32x32" in md


def test_workflow_run_failed_and_error_text() -> None:
    run = {
        "status": "failed",
        "error_text": "LLM call failed: 502 bad gateway",
        "run_id": "wfr_abc",
    }
    assert workflow_run_failed(run)
    assert "502" in workflow_run_error_text(run)
    assert not workflow_run_failed({"status": "completed"})


def test_format_failure_thread_message_includes_child() -> None:
    msg = format_failure_thread_message(
        delivery={"platform": "slack", "channel": "C1", "recipient_user_id": "U1"},
        headline="Ideation failed",
        orchestrator_run_id="wfr_parent",
        error_text="timeout",
        child_run_id="wfr_child",
        child_workflow="ideation",
    )
    assert "<@U1>" in msg
    assert "Ideation failed" in msg
    assert "wfr_parent" in msg
    assert "wfr_child" in msg
    assert "ideation" in msg
    assert "timeout" in msg


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
