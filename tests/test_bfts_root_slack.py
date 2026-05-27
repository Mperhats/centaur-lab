"""Unit tests for BFTS root Slack idea-gating."""

from __future__ import annotations

from pathlib import Path

import workflows.bfts_root as bfts_root_mod
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


def test_bfts_root_does_not_cross_post_to_ops_channel() -> None:
    """``bfts_root`` is thread-scoped: no ``#bfts-runs`` (or any other
    ops-channel) cross-post on completion.

    The completion summary lives in the active BFTS stream session when
    one is open, otherwise as a ``send_message`` reply in the originating
    Slack thread. There must be no module-level ops-channel constant and
    no ``ctx.post_to_slack`` call inside ``bfts_root``.
    """
    assert not hasattr(bfts_root_mod, "SLACK_CHANNEL"), (
        "bfts_root.SLACK_CHANNEL re-introduced; the workflow is "
        "thread-scoped and must not cross-post to an ops channel."
    )
    source = Path("workflows/bfts_root.py").read_text(encoding="utf-8")
    assert "post_to_slack(" not in source, (
        "ctx.post_to_slack(...) call re-introduced in bfts_root; the "
        "workflow is thread-scoped and must only post via the BFTS "
        "stream session or post_thread_message."
    )
    assert "bfts-runs" not in source, (
        "Hard-coded `bfts-runs` channel name re-introduced in bfts_root; "
        "remove the ops-channel reference."
    )
