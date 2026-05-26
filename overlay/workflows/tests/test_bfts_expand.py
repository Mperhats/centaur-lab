"""Test: _bfts_expand.expand_node issues the right ctx.step sequence."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _bfts_expand import (
    ExpandContext,
    _metric_parse_prompt,
    _plot_prompt,
    _propose_prompt,
    expand_node,
)


def test_metric_parse_prompt_does_not_reference_nested_working_subdir() -> None:
    """Phase 4h gave every expansion its own per-node ``working_dir`` (the
    runner ``cd``'s into ``/workspace/<node_id>/`` before running each
    step). A prompt that reads ``working/experiment_data.npy`` would
    resolve to ``/workspace/<node_id>/working/experiment_data.npy`` —
    a nested path that does not exist because the draft saved
    ``experiment_data.npy`` directly to cwd. Regression test for the
    2026-05-26 live failure (FileNotFoundError on plot_exec).
    """
    rendered = _metric_parse_prompt(code="print(1)", term_out=["ok\n"])
    assert "working/experiment_data.npy" not in rendered, (
        "metric_parse must read experiment_data.npy from cwd (per-node "
        "working_dir), not from a nested working/ subdir"
    )
    assert "experiment_data.npy" in rendered


def test_plot_prompt_does_not_reference_nested_working_subdir() -> None:
    """Same as the metric_parse prompt: plot code runs in
    ``/workspace/<node_id>/`` so files referenced as ``working/foo.npy``
    or ``working/*.png`` resolve to a nonexistent nested subdir.
    """
    rendered = _plot_prompt(code="print(1)", metric={"metric_names": []})
    assert "working/experiment_data.npy" not in rendered, (
        "plot prompt must read experiment_data.npy from cwd"
    )
    assert "working/" not in rendered, (
        "plot prompt must not reference a nested working/ subdir; "
        "the agent's cwd is already the per-node working_dir"
    )


def test_draft_propose_prompt_instructs_saving_experiment_data() -> None:
    """Without an explicit save instruction the agent's draft writes
    ``plt.show()`` and never persists data, so downstream metric_parse
    + plot steps have nothing to read. The draft prompt MUST tell the
    agent to save ``experiment_data.npy`` to cwd.
    """
    expand_ctx = ExpandContext(
        sandbox_id="sbx-1",
        parent_node=None,
        idea={"Title": "demo"},
        llm_api_key="sk-test",
        node_id="n-1",
    )
    rendered = _propose_prompt(expand_ctx)
    assert "experiment_data.npy" in rendered, (
        "draft prompt must instruct the agent to save experiment_data.npy "
        "so downstream metric_parse + plot steps have data to read"
    )


class _RecordingCtx:
    """Stub WorkflowContext that records each ctx.step name + returns
    canned values so we can assert the call order without I/O."""

    def __init__(self, canned: dict[str, object]) -> None:
        self._canned = canned
        self.calls: list[str] = []

    async def step(self, name, fn):
        self.calls.append(name)
        if name in self._canned:
            return self._canned[name]
        return await fn() if callable(fn) else None

    def log(self, *args, **kwargs):
        pass


@pytest.mark.asyncio
async def test_draft_expansion_calls_in_order() -> None:
    canned = {
        "draft_propose": {"plan": "p", "code": "print(1)"},
        "draft_exec": {"term_out": ["hi\n"], "exec_time": 0.1, "exc_type": None, "exc_info": None, "exc_stack": None},
        "bug_judge": {"is_bug": False, "summary": "ok"},
        "metric_parse_propose": "print('m')",
        "metric_parse_exec": {"term_out": ["m\n"], "exec_time": 0.1, "exc_type": None, "exc_info": None, "exc_stack": None},
        "metric_extract": {"metric_names": []},
        "plot_propose": "import matplotlib",
        "plot_exec": {"term_out": [], "exec_time": 0.1, "exc_type": None, "exc_info": None, "exc_stack": None},
        "collect_artifacts": [],
    }
    ctx = _RecordingCtx(canned)
    expand_ctx = ExpandContext(
        sandbox_id="sbx-1", parent_node=None, idea={}, llm_api_key="sk-test", node_id="n-1"
    )
    result = await expand_node(ctx=ctx, expand_ctx=expand_ctx)
    # Sakana's pipeline order, every entry one ctx.step:
    assert ctx.calls == [
        "draft_propose",
        "draft_exec",
        "bug_judge",
        "metric_parse_propose",
        "metric_parse_exec",
        "metric_extract",
        "plot_propose",
        "plot_exec",
        "collect_artifacts",
    ]
    assert result["code"] == "print(1)"
    assert result["is_buggy"] is False
    assert result["term_out"] == ["hi\n"]
    # Good-path expansion must surface the metric-parse + plot sub-step
    # outputs so bfts_tree can persist them via update_node_metric.
    assert result["parse_metrics_code"] == "print('m')"
    assert result["parse_term_out"] == ["m\n"]
    assert result["plot_code"] == "import matplotlib"
    assert result["plot_term_out"] == []


@pytest.mark.asyncio
async def test_buggy_path_omits_parse_and_plot_keys() -> None:
    """The buggy short-circuit return dict must NOT contain parse_*/plot_*
    keys — bfts_tree relies on `.get()` defaults to pass None for unchanged
    columns."""
    canned = {
        "draft_propose": {"plan": "p", "code": "raise RuntimeError()"},
        "draft_exec": {"term_out": ["err\n"], "exec_time": 0.1, "exc_type": "SubprocessError", "exc_info": {"exit_code": 1}, "exc_stack": None},
        "bug_judge": {"is_bug": True, "summary": "raised"},
    }
    ctx = _RecordingCtx(canned)
    expand_ctx = ExpandContext(sandbox_id="sbx-1", parent_node=None, idea={}, llm_api_key="sk-test", node_id="n-bug")
    result = await expand_node(ctx=ctx, expand_ctx=expand_ctx)
    for k in ("parse_metrics_code", "parse_term_out", "plot_code", "plot_term_out"):
        assert k not in result


@pytest.mark.asyncio
async def test_buggy_exec_skips_plotting() -> None:
    canned = {
        "draft_propose": {"plan": "p", "code": "raise RuntimeError()"},
        "draft_exec": {"term_out": ["err\n"], "exec_time": 0.1, "exc_type": "SubprocessError", "exc_info": {"exit_code": 1}, "exc_stack": None},
        "bug_judge": {"is_bug": True, "summary": "raised"},
    }
    ctx = _RecordingCtx(canned)
    expand_ctx = ExpandContext(sandbox_id="sbx-1", parent_node=None, idea={}, llm_api_key="sk-test", node_id="n-2")
    result = await expand_node(ctx=ctx, expand_ctx=expand_ctx)
    # On buggy exec, plotting + metric_extract are skipped.
    assert ctx.calls == ["draft_propose", "draft_exec", "bug_judge"]
    assert result["is_buggy"] is True
    assert result["metric"] is None


@pytest.mark.asyncio
async def test_tool_failure_in_exec_python_is_coerced_to_buggy() -> None:
    """When ``ctx.tools.bfts_executor.exec_python`` fails, the centaur
    tool_manager returns ``{"error": "...", "tool": ..., "method": ...}``
    instead of an ``ExecutionResult`` dict. The expand pipeline used to
    crash with ``KeyError: 'term_out'`` on the next ``exec_res["term_out"]``
    read, masking the real underlying K8s/HTTP error (see live failure
    in ``wfr_6123347aa5e440ad`` 2026-05-26: HTTP 422 sandbox-name
    validation surfaced as ``KeyError('term_out')`` in
    ``bfts_expand_one``).

    Regression: the pipeline MUST detect the tool-failure shape, coerce
    it to a buggy ExecutionResult-shape dict (``exc_type='ToolCallError'``,
    ``term_out`` carrying the raw error string), and return ``is_buggy=True``
    so the parent tree records the failure as a real node + retains the
    error in ``exc_info_json`` for postmortem inspection.
    """
    canned = {
        "draft_propose": {"plan": "p", "code": "print(1)"},
        "draft_exec": {
            "error": "404, message='Invalid response status', url=...",
            "tool": "bfts_executor",
            "method": "exec_python",
        },
    }
    ctx = _RecordingCtx(canned)
    expand_ctx = ExpandContext(
        sandbox_id="sbx-1",
        parent_node=None,
        idea={},
        llm_api_key="sk-test",
        node_id="n-tool-failure",
    )
    result = await expand_node(ctx=ctx, expand_ctx=expand_ctx)
    assert result["is_buggy"] is True, "tool failure must mark node buggy"
    assert result["exc_type"] == "ToolCallError"
    assert "404" in "\n".join(result["term_out"]), (
        "raw error string must be preserved in term_out for postmortem"
    )
    assert result["metric"] is None
    for k in ("parse_metrics_code", "parse_term_out", "plot_code", "plot_term_out"):
        assert k not in result, (
            "buggy short-circuit must not include parse_/plot_ keys"
        )


# ---------------------------------------------------------------------------
# F.2: prior_attempts memory injected into draft + improve prompts.
# ---------------------------------------------------------------------------


def test_draft_propose_injects_prior_attempts_section() -> None:
    """When ``prior_attempts`` is non-empty, the draft prompt body
    must include the ``## Prior attempts`` markdown header rendered by
    ``prior_attempts_section``."""
    expand_ctx = ExpandContext(
        sandbox_id="sbx-1",
        parent_node=None,
        idea={"Title": "demo"},
        llm_api_key="sk-test",
        node_id="n-draft",
        prior_attempts=[
            {"node_id": "older", "stage_name": "draft", "plan": "baseline",
             "is_buggy": False, "analysis": "ran clean"},
        ],
    )
    rendered = _propose_prompt(expand_ctx)

    assert "## Prior attempts" in rendered
    assert "older" in rendered
    assert "ran clean" in rendered


def test_improve_propose_injects_prior_attempts_section() -> None:
    """Improve branch (non-buggy parent) also gets the memory block."""
    expand_ctx = ExpandContext(
        sandbox_id="sbx-1",
        parent_node={"is_buggy": False, "code": "print('parent')"},
        idea={"Title": "demo"},
        llm_api_key="sk-test",
        node_id="n-improve",
        prior_attempts=[
            {"node_id": "n-1", "stage_name": "draft", "plan": "first try",
             "is_buggy": True, "analysis": "wrong shape"},
        ],
    )
    rendered = _propose_prompt(expand_ctx)

    assert "## Prior attempts" in rendered
    assert "first try" in rendered or "wrong shape" in rendered


def test_debug_propose_skips_prior_attempts_section() -> None:
    """Debug branch already carries parent failure context; the memory
    section is intentionally omitted to keep the prompt focused."""
    expand_ctx = ExpandContext(
        sandbox_id="sbx-1",
        parent_node={"is_buggy": True, "code": "bad", "term_out_json": "err"},
        idea={"Title": "demo"},
        llm_api_key="sk-test",
        node_id="n-debug",
        prior_attempts=[
            {"node_id": "ignored", "stage_name": "draft", "plan": "p",
             "is_buggy": False, "analysis": "irrelevant"},
        ],
    )
    rendered = _propose_prompt(expand_ctx)

    assert "## Prior attempts" not in rendered
    assert "ignored" not in rendered


def test_propose_omits_section_when_prior_attempts_empty() -> None:
    """Empty list → ``prior_attempts_section`` returns "" → no stray
    header appears in the prompt body."""
    expand_ctx = ExpandContext(
        sandbox_id="sbx-1",
        parent_node=None,
        idea={"Title": "demo"},
        llm_api_key="sk-test",
        node_id="n-draft",
    )
    rendered = _propose_prompt(expand_ctx)

    assert "## Prior attempts" not in rendered


# ---------------------------------------------------------------------------
# F.4: seed_override branch skips the LLM propose and re-executes
# parent code with a deterministic seeding preamble.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_seed_override_routes_through_seed_propose_step() -> None:
    """When ``seed_override`` is set, ``expand_node`` issues a
    ``seed_propose`` step (NOT ``draft_propose`` / ``improve_propose``)
    and the exec step uses ``seed_exec``. The synthesized code carries
    the np/random/torch seeding preamble in front of the parent code."""
    canned = {
        "seed_propose": {
            "plan": "seed re-evaluation with seed=2",
            "code": (
                "import random; random.seed(2)\n"
                "import numpy as np; np.random.seed(2)\n"
                "try:\n    import torch; torch.manual_seed(2)\nexcept Exception:\n    pass\n"
                "PARENT_CODE_HERE\n"
            ),
        },
        "seed_exec": {
            "term_out": ["ok\n"], "exec_time": 0.1,
            "exc_type": None, "exc_info": None, "exc_stack": None,
        },
        "bug_judge": {"is_bug": False, "summary": "ok"},
        "metric_parse_propose": "print('m')",
        "metric_parse_exec": {
            "term_out": ["m\n"], "exec_time": 0.1,
            "exc_type": None, "exc_info": None, "exc_stack": None,
        },
        "metric_extract": {"metric_names": []},
        "plot_propose": "import matplotlib",
        "plot_exec": {
            "term_out": [], "exec_time": 0.1,
            "exc_type": None, "exc_info": None, "exc_stack": None,
        },
        "collect_artifacts": [],
    }
    ctx = _RecordingCtx(canned)
    expand_ctx = ExpandContext(
        sandbox_id="sbx-1",
        parent_node={"code": "PARENT_CODE_HERE\n", "is_buggy": False},
        idea={"Title": "T"},
        llm_api_key="sk-test",
        node_id="n-seed",
        seed_override=2,
    )

    result = await expand_node(ctx=ctx, expand_ctx=expand_ctx)

    # Step name is ``seed_propose`` not ``draft_*`` / ``improve_*``.
    assert ctx.calls[0] == "seed_propose"
    assert ctx.calls[1] == "seed_exec"
    assert "draft_propose" not in ctx.calls
    assert "improve_propose" not in ctx.calls
    assert result["stage_name"] == "seed"
    assert result["is_buggy"] is False


@pytest.mark.asyncio
async def test_seed_propose_helper_injects_seeding_preamble() -> None:
    """``_seed_propose`` returns ``{plan, code}`` with the seeding
    preamble prepended to the parent's code. Per-seed determinism is
    the F.4 contract."""
    from _bfts_expand import _seed_propose

    expand_ctx = ExpandContext(
        sandbox_id="sbx-1",
        parent_node={"code": "do_thing()\n", "is_buggy": False},
        idea={},
        llm_api_key="k",
        node_id="n-seed-0",
        seed_override=0,
    )

    out = await _seed_propose(expand_ctx)

    assert out["plan"] == "seed re-evaluation with seed=0"
    # Preamble + parent code, in that order.
    assert out["code"].startswith("import random; random.seed(0)")
    assert "np.random.seed(0)" in out["code"]
    assert "torch.manual_seed(0)" in out["code"]
    assert out["code"].endswith("do_thing()\n")


@pytest.mark.asyncio
async def test_seed_propose_raises_when_parent_lacks_code() -> None:
    """A seed re-eval with no parent code (or a missing ``code`` field)
    must fail fast — re-executing nothing produces meaningless aggregate
    metrics."""
    from _bfts_expand import _seed_propose

    expand_ctx = ExpandContext(
        sandbox_id="sbx-1",
        parent_node=None,
        idea={},
        llm_api_key="k",
        node_id="n-seed-X",
        seed_override=0,
    )

    with pytest.raises(ValueError, match="parent node with executable code"):
        await _seed_propose(expand_ctx)
