"""Regression tests for ``bfts_tree`` orchestration."""

from __future__ import annotations

from pathlib import Path


def test_bfts_tree_yields_worker_between_iterations() -> None:
    """``bfts_tree`` must release its workflow worker after each iteration.

    Without this yield, the handler holds its worker continuously for the
    full ``max_iters`` x ~2-min/iter run (40-60 min), pinning a worker
    slot and starving the ``bfts_root`` progress poller -- Slack stream
    updates freeze even though the trees are still running (root-caused
    on 2026-05-27 against the run ``wfr_862be158102e4a13``). The fix is
    a ``ctx.sleep(... timedelta(0))`` at the bottom of the iteration
    loop; this test asserts the exact wiring so a future refactor can't
    drop it silently.
    """
    source = Path("workflows/bfts_tree.py").read_text(encoding="utf-8")
    assert "import datetime as dt" in source
    assert "tree_iter_yield_" in source
    assert "dt.timedelta(seconds=0)" in source
    # The yield must come AFTER ``pause_sandbox_{iters_used}`` so the
    # sandbox is parked at replicas=0 before we hand the worker back —
    # otherwise concurrent BFTS runs pay double pod-cost during the
    # interleaved poller window.
    pause_idx = source.index("pause_sandbox_{iters_used}")
    yield_idx = source.index("tree_iter_yield_")
    assert pause_idx < yield_idx
    # The yield must live inside the ``while iters_used < inp.max_iters``
    # body, not after it, so the LAST iteration also releases the worker
    # before falling through to ``list_nodes_final`` / ``set_best_node``
    # — those run on the next claim, after ``bfts_root`` has had a chance
    # to poll.
    while_idx = source.index("while iters_used < inp.max_iters:")
    final_nodes_idx = source.index('list_nodes_final')
    assert while_idx < yield_idx < final_nodes_idx
