"""Result shape for one Sandbox-side code execution.

Mirrors .scientist/ai_scientist/treesearch/interpreter.py:26-37 verbatim
so existing Sakana-shape prompts and metric-parse scripts work unchanged
inside the Centaur workflow.
"""
from __future__ import annotations

from dataclasses import dataclass

from dataclasses_json import DataClassJsonMixin


@dataclass
class ExecutionResult(DataClassJsonMixin):
    """One code-execution result (stdout/stderr, timing, exception)."""

    term_out: list[str]
    exec_time: float
    exc_type: str | None
    exc_info: dict | None = None
    exc_stack: list[tuple] | None = None
