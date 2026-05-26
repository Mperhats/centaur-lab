"""Tests for the ``ideation`` workflow (Phase 4d.2).

The handler turns a one-sentence research topic into a structured ``idea``
dict that ``bfts_root.Input.idea`` can consume directly. Port of Sakana's
``perform_ideation_temp_free.py``: one Semantic Scholar seed search for
grounding, one LLM function-call to synthesize the idea, and an optional
critic loop that re-invokes the LLM ``critic_retries`` times for
refinement.

These tests pin:

- The public workflow surface (``WORKFLOW_NAME``, empty ``SCHEDULE``).
- The ``Input`` dataclass shape (only ``topic`` required; LLM overrides
  default to ``None`` so ``_bfts_config.resolve_llm_settings`` reaches
  the env / module-default tiers).
- The handler's three call sites: S2 seed search, LLM synthesis, optional
  critic loop — each as its own ``ctx.step`` with a deterministic name so
  workflow replay maps back to the right call.
- The return contract: ``{"idea": <idea dict>, "seed_papers": [<paperId>, ...]}``
  with at minimum the four required idea fields (``Name``, ``Title``,
  ``Short Hypothesis``, ``Experiments``). Additional Sakana fields
  (``Abstract``, ``Related Work``, ``Risk Factors and Limitations``)
  are emitted by the implementation but not asserted here so the schema
  can evolve loosely.
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class _SemanticScholarStub:
    """Records search_papers kwargs; returns a fixed list."""

    def __init__(self, return_value: list[dict[str, Any]] | None = None) -> None:
        self.search_papers = AsyncMock(
            return_value=return_value if return_value is not None else _seed_papers()
        )


class _Tools:
    def __init__(self, *, semantic_scholar: _SemanticScholarStub | None = None) -> None:
        self.semantic_scholar = semantic_scholar or _SemanticScholarStub()


class _IdeationCtx:
    """Stand-in WorkflowContext for ``ideation.handler``.

    Mirrors ``_ReflectionCtx`` / ``_RootCtx``: ``step`` invokes its
    callable / coroutine just like the real engine, recording the step
    name so deterministic-naming assertions can pin the replay contract.
    """

    def __init__(self, *, tools: _Tools | None = None) -> None:
        self.tools = tools or _Tools()
        self.step_calls: list[str] = []
        self.logs: list[tuple[str, dict[str, Any]]] = []

    async def step(self, name: str, fn: Any) -> Any:
        self.step_calls.append(name)
        out = fn() if callable(fn) else fn
        if inspect.iscoroutine(out):
            out = await out
        return out

    def log(self, event: str, **kwargs: Any) -> None:
        self.logs.append((event, kwargs))


def _seed_papers() -> list[dict[str, Any]]:
    """Two synthetic S2 papers with all the fields the handler reads."""
    return [
        {
            "paperId": "p-aaa",
            "title": "Diffusion Models for Protein Design",
            "year": 2024,
            "abstract": "We propose a new diffusion model variant for protein backbones.",
        },
        {
            "paperId": "p-bbb",
            "title": "Score-Based Generative Modeling in Biology",
            "year": 2023,
            "abstract": "Reviews score-based generative methods applied to biological data.",
        },
    ]


def _valid_idea() -> dict[str, Any]:
    """A synthetic idea dict matching the Sakana FinalizeIdea schema."""
    return {
        "Name": "diffusion_proteins_v2",
        "Title": "Conditional Diffusion for Functional Protein Design",
        "Short Hypothesis": (
            "Conditioning diffusion sampling on a learned functional embedding "
            "improves designability vs. unconditional baselines."
        ),
        "Related Work": "Distinguishes from RFDiffusion by conditioning on function.",
        "Abstract": "We propose ...",
        "Experiments": "Train on the PDB dataset; measure designability and novelty.",
        "Risk Factors and Limitations": "Function embedding may be miscalibrated.",
    }


def _install_stub_llm(
    monkeypatch: pytest.MonkeyPatch,
    *,
    return_values: list[dict[str, Any]] | None = None,
) -> AsyncMock:
    """Replace ``ideation.call_with_function`` with an AsyncMock.

    ``return_values`` cycles through successive call returns so a critic
    loop test can assert each round received the prior round's idea.
    Defaults to a single valid idea.
    """
    import ideation

    rv = return_values if return_values is not None else [_valid_idea()]
    stub = AsyncMock(side_effect=list(rv))
    monkeypatch.setattr(ideation, "call_with_function", stub)
    monkeypatch.setattr(ideation, "resolve_llm_api_key", lambda _name: "fake-key")
    return stub


def _clear_bfts_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Drop BFTS_* env so tests pin the resolver chain deterministically."""
    monkeypatch.delenv("BFTS_DRAFT_MODEL", raising=False)
    monkeypatch.delenv("BFTS_LLM_API_KEY_SECRET", raising=False)


# --- Public surface ------------------------------------------------------


def test_workflow_name_and_schedule_shape() -> None:
    """Pin the workflow name + that SCHEDULE is empty (user-triggered).

    Ideation is invoked via a manual POST to ``/workflows/runs``; a
    populated SCHEDULE would cause the engine to fire orphan runs on a
    timer with no topic, which would either fail-fast (empty topic) or
    burn LLM budget on a no-op default.
    """
    import ideation

    assert ideation.WORKFLOW_NAME == "ideation"
    assert ideation.SCHEDULE == {}


def test_input_dataclass_required_fields() -> None:
    """``topic`` is required; everything else has a sensible default.

    The four optional fields default to ``None`` (LLM overrides) or
    sensible literals (``seed_paper_limit=10`` per plan, ``critic_retries=0``
    for opt-in critic). ``None`` defaults route through
    ``resolve_llm_settings`` to the BFTS_* env / module defaults so an
    operator doesn't have to repeat the deployment config on every POST.
    """
    import ideation

    inp = ideation.Input(topic="diffusion models for protein design")
    assert inp.topic == "diffusion models for protein design"
    assert inp.seed_paper_limit == 10
    assert inp.critic_retries == 0
    assert inp.draft_model is None
    assert inp.llm_api_key_secret is None


# --- Handler-level tests -------------------------------------------------


@pytest.mark.asyncio
async def test_handler_calls_semantic_scholar_with_topic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The seed search must use ``topic`` as the query and ``seed_paper_limit``
    as the cap. A wrong query would ground the LLM in unrelated literature
    and silently degrade idea quality."""
    import ideation

    _clear_bfts_env(monkeypatch)
    _install_stub_llm(monkeypatch)
    ctx = _IdeationCtx()

    await ideation.handler(
        ideation.Input(topic="diffusion models for protein design", seed_paper_limit=7),
        ctx,
    )

    assert ctx.tools.semantic_scholar.search_papers.await_count == 1
    kwargs = ctx.tools.semantic_scholar.search_papers.await_args.kwargs
    assert kwargs["query"] == "diffusion models for protein design"
    assert kwargs["limit"] == 7


@pytest.mark.asyncio
async def test_handler_calls_synthesize_idea_with_seed_papers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The LLM synthesize call must receive both the topic and the seed
    paper titles in its prompt so the model can ground the proposal in
    real literature instead of inventing references."""
    import ideation

    _clear_bfts_env(monkeypatch)
    stub_llm = _install_stub_llm(monkeypatch)
    ctx = _IdeationCtx()

    await ideation.handler(
        ideation.Input(topic="diffusion models for protein design"), ctx
    )

    assert stub_llm.await_count == 1
    call = stub_llm.await_args
    llm_call = call.args[0]
    assert "diffusion models for protein design" in llm_call.prompt
    assert "Diffusion Models for Protein Design" in llm_call.prompt
    assert "Score-Based Generative Modeling in Biology" in llm_call.prompt
    spec = call.kwargs["function_spec"]
    required = set(spec["function"]["parameters"]["required"])
    assert {"Name", "Title", "Short Hypothesis", "Experiments"}.issubset(required)


@pytest.mark.asyncio
async def test_handler_returns_idea_dict_with_required_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The returned ``idea`` must carry the four fields ``bfts_root`` /
    ``_bfts_expand`` consume downstream. Asserting the loose subset keeps
    the schema free to add fields (e.g. ``Abstract``) without churning
    this test."""
    import ideation

    _clear_bfts_env(monkeypatch)
    _install_stub_llm(monkeypatch)
    ctx = _IdeationCtx()

    out = await ideation.handler(
        ideation.Input(topic="diffusion models for protein design"), ctx
    )

    assert "idea" in out
    idea = out["idea"]
    for field_name in ("Name", "Title", "Short Hypothesis", "Experiments"):
        assert field_name in idea, f"missing required idea field: {field_name}"


@pytest.mark.asyncio
async def test_handler_returns_seed_papers_as_paper_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``seed_papers`` is ``list[str]`` of S2 paperIds — the downstream
    citation step (Phase 4d.3 ``gather_citations``) keys lookups on
    paperId, so a dict-of-dicts return would force every caller to
    re-extract the ids."""
    import ideation

    _clear_bfts_env(monkeypatch)
    _install_stub_llm(monkeypatch)
    ctx = _IdeationCtx()

    out = await ideation.handler(
        ideation.Input(topic="diffusion models for protein design"), ctx
    )

    assert out["seed_papers"] == ["p-aaa", "p-bbb"]
    assert all(isinstance(pid, str) for pid in out["seed_papers"])


@pytest.mark.asyncio
async def test_handler_skips_validate_step_when_critic_retries_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default ``critic_retries=0`` must not invoke any ``validate_idea`` step.
    The critic costs an extra LLM call per retry; opt-in only."""
    import ideation

    _clear_bfts_env(monkeypatch)
    stub_llm = _install_stub_llm(monkeypatch)
    ctx = _IdeationCtx()

    await ideation.handler(ideation.Input(topic="x"), ctx)

    assert not any(name.startswith("validate_idea") for name in ctx.step_calls)
    assert stub_llm.await_count == 1


@pytest.mark.asyncio
async def test_handler_runs_validate_step_when_critic_retries_positive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``critic_retries=2`` triggers two distinct ``validate_idea_{i}`` steps
    (per-retry deterministic names so workflow replay maps each refinement
    back to its call site) and re-invokes the LLM once per retry, threading
    the previous idea forward."""
    import ideation

    _clear_bfts_env(monkeypatch)
    refined_first = {**_valid_idea(), "Title": "Refined v1"}
    refined_second = {**_valid_idea(), "Title": "Refined v2"}
    stub_llm = _install_stub_llm(
        monkeypatch,
        return_values=[_valid_idea(), refined_first, refined_second],
    )
    ctx = _IdeationCtx()

    out = await ideation.handler(
        ideation.Input(topic="diffusion models", critic_retries=2), ctx
    )

    validate_steps = [n for n in ctx.step_calls if n.startswith("validate_idea")]
    assert len(validate_steps) == 2
    assert len(set(validate_steps)) == 2, "each retry needs a unique step name"
    assert stub_llm.await_count == 3
    assert out["idea"]["Title"] == "Refined v2"


@pytest.mark.asyncio
async def test_handler_resolves_model_via_env_or_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The draft model used for synthesis must come from the standard
    BFTS resolver chain (Input override → BFTS_DRAFT_MODEL env →
    DEFAULT_DRAFT_MODEL). Pin both the env-override branch and the
    default-fallback branch in one test."""
    import ideation
    from _bfts_config import DEFAULT_DRAFT_MODEL

    # --- env-override branch ---
    monkeypatch.setenv("BFTS_DRAFT_MODEL", "claude-env-override-test")
    stub_llm = _install_stub_llm(monkeypatch)
    ctx = _IdeationCtx()
    await ideation.handler(ideation.Input(topic="x"), ctx)
    assert stub_llm.await_args.args[0].model == "claude-env-override-test"

    # --- default-fallback branch ---
    monkeypatch.delenv("BFTS_DRAFT_MODEL", raising=False)
    stub_llm = _install_stub_llm(monkeypatch)
    ctx = _IdeationCtx()
    await ideation.handler(ideation.Input(topic="x"), ctx)
    assert stub_llm.await_args.args[0].model == DEFAULT_DRAFT_MODEL


@pytest.mark.asyncio
async def test_handler_input_draft_model_overrides_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``Input.draft_model`` beats BFTS_DRAFT_MODEL — the per-run override
    is the top tier of the resolver chain so operators can run ideation
    against a different model without touching Helm."""
    import ideation

    monkeypatch.setenv("BFTS_DRAFT_MODEL", "claude-env-test")
    stub_llm = _install_stub_llm(monkeypatch)
    ctx = _IdeationCtx()
    await ideation.handler(
        ideation.Input(topic="x", draft_model="claude-input-override"), ctx
    )
    assert stub_llm.await_args.args[0].model == "claude-input-override"


@pytest.mark.asyncio
async def test_handler_uses_step_names_for_durability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Step names must be stable across deploys so workflow replay maps
    cached step rows back to handler call sites. Pin the no-critic-loop
    ordering here; the critic-loop test pins the per-retry naming."""
    import ideation

    _clear_bfts_env(monkeypatch)
    _install_stub_llm(monkeypatch)
    ctx = _IdeationCtx()

    await ideation.handler(ideation.Input(topic="x"), ctx)

    assert ctx.step_calls == ["seed_search", "synthesize_idea"]
