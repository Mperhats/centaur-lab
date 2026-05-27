"""Unit tests for BFTS root Slack delivery helpers."""

from __future__ import annotations

from packages.bfts_sdk.slack_delivery import (
    delivery_from_thread_key,
    resolve_slack_delivery,
    slack_mention_prefix,
)
from workflows.bfts_root import Input, _reject_default_idea


def test_delivery_from_slack_thread_key() -> None:
    key = "slack:C0AJ07U8Z1N:1773364194.179929"
    assert delivery_from_thread_key(key) == {
        "platform": "slack",
        "channel": "C0AJ07U8Z1N",
        "thread_ts": "1773364194.179929",
    }


def test_delivery_from_non_slack_thread_key_returns_none() -> None:
    assert delivery_from_thread_key("workflow:wfr_abc:gap") is None


def test_resolve_slack_delivery_from_explicit_delivery() -> None:
    inp = Input(
        delivery={
            "platform": "slack",
            "channel": "C123",
            "thread_ts": "1.0",
            "recipient_user_id": "U456",
        }
    )
    resolved = resolve_slack_delivery(
        explicit_delivery=inp.delivery,
        run_input={},
        explicit_thread_key=inp.thread_key,
    )
    assert resolved == {
        "platform": "slack",
        "channel": "C123",
        "thread_ts": "1.0",
        "recipient_user_id": "U456",
    }


def test_resolve_slack_delivery_derives_from_thread_key() -> None:
    inp = Input(thread_key="slack:C999:1700000000.000100")
    resolved = resolve_slack_delivery(
        explicit_delivery=inp.delivery,
        run_input={},
        explicit_thread_key=inp.thread_key,
    )
    assert resolved == {
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
