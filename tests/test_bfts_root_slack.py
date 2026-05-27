"""Unit tests for BFTS root Slack idea-gating."""

from __future__ import annotations

from workflows.bfts_root import Input, _reject_default_idea


def test_reject_default_idea_when_slack_thread_key() -> None:
    inp = Input(thread_key="slack:C1:1.0")
    assert _reject_default_idea(inp, {}, idea_was_defaulted=True) is True


def test_reject_default_idea_when_slack_delivery() -> None:
    inp = Input(
        delivery={"platform": "slack", "channel": "C1", "thread_ts": "1.0"},
    )
    assert _reject_default_idea(inp, {}, idea_was_defaulted=True) is True


def test_allow_smoke_idea_override() -> None:
    inp = Input(thread_key="slack:C1:1.0", allow_smoke_idea=True)
    assert _reject_default_idea(inp, {}, idea_was_defaulted=True) is False


def test_no_reject_when_idea_provided() -> None:
    inp = Input(thread_key="slack:C1:1.0")
    assert _reject_default_idea(inp, {}, idea_was_defaulted=False) is False


def test_no_reject_cli_run_without_thread() -> None:
    inp = Input()
    assert _reject_default_idea(inp, {}, idea_was_defaulted=True) is False
