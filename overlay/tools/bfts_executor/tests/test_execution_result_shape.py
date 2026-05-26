"""Test: ExecutionResult preserves Sakana's wire shape exactly."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from models import ExecutionResult


def test_execution_result_minimal_construction() -> None:
    r = ExecutionResult(term_out=["hi\n"], exec_time=0.1, exc_type=None)
    assert r.term_out == ["hi\n"]
    assert r.exec_time == 0.1
    assert r.exc_type is None
    assert r.exc_info is None
    assert r.exc_stack is None


def test_execution_result_with_exception() -> None:
    r = ExecutionResult(
        term_out=["Traceback...\n"],
        exec_time=0.5,
        exc_type="ValueError",
        exc_info={"args": ["bad input"]},
        exc_stack=[("/work/runfile.py", 12, "<module>", "raise ValueError('bad input')")],
    )
    assert r.exc_type == "ValueError"
    assert r.exc_info == {"args": ["bad input"]}
    assert r.exc_stack[0][0] == "/work/runfile.py"


def test_execution_result_roundtrip_json() -> None:
    r = ExecutionResult(term_out=["hi\n"], exec_time=0.1, exc_type=None)
    blob = r.to_dict()
    assert blob == {
        "term_out": ["hi\n"],
        "exec_time": 0.1,
        "exc_type": None,
        "exc_info": None,
        "exc_stack": None,
    }
    r2 = ExecutionResult.from_dict(blob)
    assert r2 == r
