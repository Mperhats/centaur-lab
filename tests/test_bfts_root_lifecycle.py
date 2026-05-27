"""Lifecycle invariants for ``bfts_root`` (durable-workflow replay safety)."""

from __future__ import annotations

import ast
from pathlib import Path


def test_bfts_root_handler_does_not_teardown_in_try_finally() -> None:
    """``finally`` around ``wait_for_workflow`` deletes sandboxes on suspend."""
    source = Path("workflows/bfts_root.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    handler = next(
        node
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "handler"
    )
    for try_node in ast.walk(handler):
        if not isinstance(try_node, ast.Try):
            continue
        if not try_node.finalbody:
            continue
        finally_src = ast.get_source_segment(source, try_node) or ""
        assert "wait_for_workflow" not in finally_src, (
            "bfts_root must not use try/finally around wait_for_workflow; "
            "finally runs on workflow suspend and tears down sandboxes early"
        )
        for stmt in try_node.finalbody:
            seg = ast.get_source_segment(source, stmt) or ""
            assert "stop_sandbox" not in seg, (
                "stop_sandbox must not live in try/finally; use explicit "
                "teardown after all wait_tree_* steps complete"
            )
