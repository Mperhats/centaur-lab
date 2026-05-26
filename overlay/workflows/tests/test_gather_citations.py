"""Tests for the ``gather_citations`` workflow (Phase 4d.3).

The handler closes the BFTS loop's writeup half: given a completed
``bfts_run``, it pulls the best node's plan/code, asks an LLM to extract
factual claims that need citations, looks up each via Semantic Scholar
(with ``BIBTEX_PAPER_FIELDS`` so ``citationStyles.bibtex`` lands in the
response), and writes a single ``references.bib`` artifact onto the
best node so a future writeup workflow can pick it up alongside
``best_solution.py``.

These tests pin:

- The public workflow surface (``WORKFLOW_NAME``, empty ``SCHEDULE``).
- ``Input`` shape — ``run_id`` is the only required field; the
  cap/budget knobs and LLM overrides default to sensible values that
  route through the standard resolver chain.
- The DB lookup contract — best-node row loaded via the run_id, missing
  ``best_node_id`` raises a ``ValueError`` (incomplete run), and the
  ``plan``/``code`` actually reach the LLM extraction prompt.
- The S2 fan-out — one ``ctx.step("search_{i}", ...)`` per claim,
  ``limit=searches_per_claim`` (default 3 per plan), ``fields`` includes
  ``citationStyles`` so BibTeX round-trips.
- The BibTeX assembly — concatenation only (we trust S2's
  ``citationStyles.bibtex``), deduplicated by ``paperId`` so the
  downstream LaTeX compile doesn't choke on duplicate keys.
- Empty-results handling — zero claims or zero papers writes an empty
  ``references.bib`` artifact (downstream writeup can detect & skip)
  instead of leaving the node without one.
- Defensive caps — ``max_claims`` is clamped to the module-level hard
  ceiling so a misconfigured caller (``max_claims=1000``) cannot
  fan out 1000 S2 calls.
- Step naming — stable across deploys for workflow replay determinism.
- Resolver chain — same Input → ``BFTS_DRAFT_MODEL`` env → module
  default cascade as the rest of the BFTS workflows.
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ._mocks import MockPool


# --- Fixtures ------------------------------------------------------------


def _best_node_row(
    *,
    node_id: str = "node-best",
    plan: str = "We propose a graph attention variant.",
    code: str = "import torch\n# graph attention impl\n",
) -> dict[str, Any]:
    """Synthetic ``fetchrow`` response for the best-node lookup."""
    return {"node_id": node_id, "plan": plan, "code": code}


def _claim(claim: str, query: str) -> dict[str, str]:
    return {"claim": claim, "query": query}


def _paper(
    *,
    paper_id: str,
    bibtex: str | None = "@article{Author2024, title={X}, author={A}, year={2024}}",
) -> dict[str, Any]:
    """S2 paper dict with ``citationStyles.bibtex`` shaped like the real API."""
    out: dict[str, Any] = {
        "paperId": paper_id,
        "title": "Some Paper",
        "year": 2024,
        "authors": [{"name": "A. Author"}],
        "url": f"https://example.invalid/{paper_id}",
    }
    if bibtex is not None:
        out["citationStyles"] = {"bibtex": bibtex}
    return out


class _SemanticScholarStub:
    """Records ``search_papers`` kwargs; returns lists from a queue.

    The handler issues one search per claim, so the stub takes a list of
    response lists matching that order. ``AsyncMock(side_effect=...)``
    raises ``StopIteration`` if the handler issues more calls than
    supplied — which is exactly the assertion we want for the
    ``searches_per_claim`` budget.
    """

    def __init__(self, return_values: list[list[dict[str, Any]]] | None = None) -> None:
        self.search_papers = AsyncMock(
            side_effect=list(return_values) if return_values is not None else []
        )


class _Tools:
    def __init__(self, *, semantic_scholar: _SemanticScholarStub | None = None) -> None:
        self.semantic_scholar = semantic_scholar or _SemanticScholarStub()


class _GatherCtx:
    """Stand-in WorkflowContext for ``gather_citations.handler``.

    Mirrors ``_IdeationCtx`` / ``_ReflectionCtx``: ``step`` invokes its
    callable / coroutine just like the real engine, recording each step
    name so deterministic-naming assertions pin the replay contract.
    Pool is mounted at ``_pool`` (Phase 4c convention) so the handler
    can reach ``_bfts_state.fetch_best_node_for_run`` via the same
    private attribute the production engine sets.
    """

    def __init__(
        self,
        *,
        pool: Any,
        tools: _Tools | None = None,
    ) -> None:
        self._pool = pool
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


def _install_stub_llm(
    monkeypatch: pytest.MonkeyPatch,
    *,
    return_values: list[dict[str, Any]] | None = None,
) -> AsyncMock:
    """Patch ``gather_citations.call_with_function`` and the secret resolver.

    ``return_values`` cycles through the LLM call(s); the v1 handler
    issues exactly one extract_claims call so a single dict is the
    common case. Tests that want to drive the validator path can pass
    a malformed payload here.
    """
    import gather_citations

    rv = return_values if return_values is not None else [{"claims": []}]
    stub = AsyncMock(side_effect=list(rv))
    monkeypatch.setattr(gather_citations, "call_with_function", stub)
    monkeypatch.setattr(gather_citations, "resolve_llm_api_key", lambda _name: "fake-key")
    return stub


def _clear_bfts_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BFTS_DRAFT_MODEL", raising=False)
    monkeypatch.delenv("BFTS_LLM_API_KEY_SECRET", raising=False)


# --- Public surface ------------------------------------------------------


def test_workflow_name_and_schedule_shape() -> None:
    """Pin the workflow name + that SCHEDULE is empty (user-triggered).

    ``gather_citations`` runs once per completed bfts_run, after the
    operator has decided the tree is "done". A populated SCHEDULE would
    fire orphan runs on a timer with no run_id, which would either
    fail-fast or pick stale rows.
    """
    import gather_citations

    assert gather_citations.WORKFLOW_NAME == "gather_citations"
    assert gather_citations.SCHEDULE == {}


def test_input_dataclass_required_fields() -> None:
    """``run_id`` is required; everything else has a sensible default.

    LLM-override fields default to ``None`` so the resolver chain
    reaches BFTS_* env / module-default tiers instead of being
    short-circuited by a hardcoded default.
    """
    import gather_citations

    inp = gather_citations.Input(run_id="run-xyz")
    assert inp.run_id == "run-xyz"
    assert inp.max_claims == 8
    assert inp.searches_per_claim == 3
    assert inp.draft_model is None
    assert inp.llm_api_key_secret is None


# --- Handler-level tests -------------------------------------------------


@pytest.mark.asyncio
async def test_handler_loads_best_node_via_run_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The handler must resolve the best node via the run_id by reading
    ``bfts_runs.best_node_id`` joined to ``bfts_nodes``. The exact SQL
    shape is in the DAO, but pinning that the lookup happens (one
    fetchrow with ``run_id`` as the parameter) protects against a
    refactor that bypasses the DB layer."""
    import gather_citations

    _clear_bfts_env(monkeypatch)
    pool = MockPool(fetchrow_result=_best_node_row())
    _install_stub_llm(monkeypatch)
    ctx = _GatherCtx(pool=pool)

    await gather_citations.handler(gather_citations.Input(run_id="run-xyz"), ctx)

    assert len(pool.fetchrow_calls) == 1
    query, args = pool.fetchrow_calls[0]
    assert "bfts_runs" in query
    assert "bfts_nodes" in query
    assert args == ("run-xyz",)


@pytest.mark.asyncio
async def test_handler_raises_when_run_has_no_best_node_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A run with ``best_node_id IS NULL`` (incomplete tree, or every
    expansion was buggy) yields ``fetchrow → None``; the handler must
    fail fast rather than silently writing an artifact against a NULL
    foreign key."""
    import gather_citations

    _clear_bfts_env(monkeypatch)
    pool = MockPool(fetchrow_result=None)
    _install_stub_llm(monkeypatch)
    ctx = _GatherCtx(pool=pool)

    with pytest.raises(ValueError) as excinfo:
        await gather_citations.handler(
            gather_citations.Input(run_id="run-incomplete"), ctx
        )

    msg = str(excinfo.value)
    assert "run-incomplete" in msg
    assert "best_node_id" in msg


@pytest.mark.asyncio
async def test_handler_calls_extract_claims_with_plan_and_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The LLM extract step must see both the plan (high-level proposal
    text) and the code (implementation details) — both contain claims
    worth grounding. Asserting both substrings end up in the prompt
    pins the contract loosely without freezing the prompt template."""
    import gather_citations

    _clear_bfts_env(monkeypatch)
    pool = MockPool(
        fetchrow_result=_best_node_row(
            plan="UNIQUE_PLAN_TOKEN graph attention.",
            code="UNIQUE_CODE_TOKEN # impl",
        )
    )
    stub_llm = _install_stub_llm(monkeypatch)
    ctx = _GatherCtx(pool=pool)

    await gather_citations.handler(
        gather_citations.Input(run_id="run-xyz"), ctx
    )

    assert stub_llm.await_count == 1
    llm_call = stub_llm.await_args.args[0]
    assert "UNIQUE_PLAN_TOKEN" in llm_call.prompt
    assert "UNIQUE_CODE_TOKEN" in llm_call.prompt
    spec = stub_llm.await_args.kwargs["function_spec"]
    assert spec["function"]["name"] == "extract_claims"


@pytest.mark.asyncio
async def test_handler_calls_s2_for_each_claim_with_limit_3(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One ``search_papers`` call per claim, ``limit=searches_per_claim``
    (default 3 per plan), and ``fields`` projects ``citationStyles`` so
    the BibTeX round-trip lands in the response. A future refactor that
    drops ``fields=`` would silently lose every BibTeX entry."""
    import gather_citations

    _clear_bfts_env(monkeypatch)
    pool = MockPool(fetchrow_result=_best_node_row())
    _install_stub_llm(
        monkeypatch,
        return_values=[
            {
                "claims": [
                    _claim("Graph attention beats GCN.", "graph attention citation"),
                    _claim("Diffusion models scale.", "diffusion model scaling"),
                ]
            }
        ],
    )
    s2 = _SemanticScholarStub(
        return_values=[
            [_paper(paper_id="p1")],
            [_paper(paper_id="p2")],
        ]
    )
    ctx = _GatherCtx(pool=pool, tools=_Tools(semantic_scholar=s2))

    await gather_citations.handler(
        gather_citations.Input(run_id="run-xyz"), ctx
    )

    assert s2.search_papers.await_count == 2
    for call in s2.search_papers.await_args_list:
        assert call.kwargs["limit"] == 3
        assert "citationStyles" in call.kwargs["fields"].split(",")
    queries = [c.kwargs["query"] for c in s2.search_papers.await_args_list]
    assert queries == ["graph attention citation", "diffusion model scaling"]


@pytest.mark.asyncio
async def test_handler_uses_searches_per_claim_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``Input.searches_per_claim`` overrides the default 3."""
    import gather_citations

    _clear_bfts_env(monkeypatch)
    pool = MockPool(fetchrow_result=_best_node_row())
    _install_stub_llm(
        monkeypatch,
        return_values=[{"claims": [_claim("c", "q")]}],
    )
    s2 = _SemanticScholarStub(return_values=[[_paper(paper_id="p1")]])
    ctx = _GatherCtx(pool=pool, tools=_Tools(semantic_scholar=s2))

    await gather_citations.handler(
        gather_citations.Input(run_id="run-xyz", searches_per_claim=5), ctx
    )

    assert s2.search_papers.await_args.kwargs["limit"] == 5


@pytest.mark.asyncio
async def test_handler_builds_bibtex_from_citation_styles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The artifact written must contain the verbatim ``citationStyles.bibtex``
    string from each S2 result. We don't parse/regenerate BibTeX — we trust
    S2's emitted entries and concatenate them."""
    import gather_citations

    _clear_bfts_env(monkeypatch)
    pool = MockPool(fetchrow_result=_best_node_row())
    _install_stub_llm(
        monkeypatch,
        return_values=[{"claims": [_claim("c1", "q1"), _claim("c2", "q2")]}],
    )
    bib_a = "@article{Alpha2024, title={Alpha}}"
    bib_b = "@article{Beta2024, title={Beta}}"
    s2 = _SemanticScholarStub(
        return_values=[
            [_paper(paper_id="p1", bibtex=bib_a)],
            [_paper(paper_id="p2", bibtex=bib_b)],
        ]
    )
    ctx = _GatherCtx(pool=pool, tools=_Tools(semantic_scholar=s2))

    await gather_citations.handler(
        gather_citations.Input(run_id="run-xyz"), ctx
    )

    assert len(pool.execute_calls) == 1
    _query, args = pool.execute_calls[0]
    bytes_arg = args[2]
    assert isinstance(bytes_arg, (bytes, bytearray))
    body = bytes(bytes_arg).decode("utf-8")
    assert bib_a in body
    assert bib_b in body


@pytest.mark.asyncio
async def test_handler_dedupes_papers_by_paper_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two claims may surface the same paper. Dedup by ``paperId`` so
    BibTeX key collisions don't break the downstream LaTeX compile."""
    import gather_citations

    _clear_bfts_env(monkeypatch)
    pool = MockPool(fetchrow_result=_best_node_row())
    _install_stub_llm(
        monkeypatch,
        return_values=[{"claims": [_claim("c1", "q1"), _claim("c2", "q2")]}],
    )
    bib = "@article{Shared2024, title={Shared}}"
    s2 = _SemanticScholarStub(
        return_values=[
            [_paper(paper_id="p-shared", bibtex=bib)],
            [_paper(paper_id="p-shared", bibtex=bib)],
        ]
    )
    ctx = _GatherCtx(pool=pool, tools=_Tools(semantic_scholar=s2))

    await gather_citations.handler(
        gather_citations.Input(run_id="run-xyz"), ctx
    )

    _query, args = pool.execute_calls[0]
    body = bytes(args[2]).decode("utf-8")
    assert body.count(bib) == 1


@pytest.mark.asyncio
async def test_handler_writes_references_bib_artifact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The artifact write must target ``bfts_artifacts`` with
    ``relative_path = 'references.bib'`` and the best node's id."""
    import gather_citations

    _clear_bfts_env(monkeypatch)
    pool = MockPool(fetchrow_result=_best_node_row(node_id="best-1"))
    _install_stub_llm(
        monkeypatch,
        return_values=[{"claims": [_claim("c", "q")]}],
    )
    s2 = _SemanticScholarStub(
        return_values=[[_paper(paper_id="p1")]],
    )
    ctx = _GatherCtx(pool=pool, tools=_Tools(semantic_scholar=s2))

    await gather_citations.handler(
        gather_citations.Input(run_id="run-xyz"), ctx
    )

    assert len(pool.execute_calls) == 1
    query, args = pool.execute_calls[0]
    assert "bfts_artifacts" in query
    assert "references.bib" in query
    assert args[1] == "best-1"


@pytest.mark.asyncio
async def test_handler_writes_empty_bib_when_no_claims_extracted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Zero-claim case: write an empty ``references.bib`` so the
    downstream writeup workflow can deterministically skip on empty
    bytes rather than checking artifact existence + missing-row
    handling. Also emit a structured log so an operator can see the
    silent-degradation case."""
    import gather_citations

    _clear_bfts_env(monkeypatch)
    pool = MockPool(fetchrow_result=_best_node_row(node_id="best-1"))
    _install_stub_llm(monkeypatch, return_values=[{"claims": []}])
    s2 = _SemanticScholarStub(return_values=[])
    ctx = _GatherCtx(pool=pool, tools=_Tools(semantic_scholar=s2))

    await gather_citations.handler(
        gather_citations.Input(run_id="run-xyz"), ctx
    )

    assert s2.search_papers.await_count == 0
    assert len(pool.execute_calls) == 1
    _query, args = pool.execute_calls[0]
    body = bytes(args[2]).decode("utf-8")
    assert body == ""
    no_claims = [
        kw for ev, kw in ctx.logs if ev == "gather_citations_no_claims_extracted"
    ]
    assert len(no_claims) == 1


@pytest.mark.asyncio
async def test_handler_writes_empty_bib_when_s2_returns_no_papers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """All-empty searches: the LLM extracted real claims but S2 found
    nothing for any of them. Same write-empty pattern as the no-claims
    case so writeup tooling sees a consistent envelope."""
    import gather_citations

    _clear_bfts_env(monkeypatch)
    pool = MockPool(fetchrow_result=_best_node_row(node_id="best-1"))
    _install_stub_llm(
        monkeypatch,
        return_values=[{"claims": [_claim("c1", "q1"), _claim("c2", "q2")]}],
    )
    s2 = _SemanticScholarStub(return_values=[[], []])
    ctx = _GatherCtx(pool=pool, tools=_Tools(semantic_scholar=s2))

    await gather_citations.handler(
        gather_citations.Input(run_id="run-xyz"), ctx
    )

    assert len(pool.execute_calls) == 1
    _query, args = pool.execute_calls[0]
    body = bytes(args[2]).decode("utf-8")
    assert body == ""


@pytest.mark.asyncio
async def test_handler_skips_papers_without_bibtex(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Papers without ``citationStyles.bibtex`` are silently dropped
    (S2 hasn't generated a citation entry for them yet) — they'd
    otherwise produce a broken BibTeX file. A log surfaces the count."""
    import gather_citations

    _clear_bfts_env(monkeypatch)
    pool = MockPool(fetchrow_result=_best_node_row(node_id="best-1"))
    _install_stub_llm(
        monkeypatch, return_values=[{"claims": [_claim("c", "q")]}]
    )
    bib = "@article{Has2024}"
    s2 = _SemanticScholarStub(
        return_values=[
            [
                _paper(paper_id="p-with", bibtex=bib),
                _paper(paper_id="p-without", bibtex=None),
            ],
        ]
    )
    ctx = _GatherCtx(pool=pool, tools=_Tools(semantic_scholar=s2))

    await gather_citations.handler(
        gather_citations.Input(run_id="run-xyz"), ctx
    )

    _query, args = pool.execute_calls[0]
    body = bytes(args[2]).decode("utf-8")
    assert bib in body


@pytest.mark.asyncio
async def test_handler_caps_max_claims_at_module_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``Input.max_claims`` is upper-clamped at the module hard-cap so
    a misconfigured caller (``max_claims=1000``) cannot fan out 1000
    serial S2 calls. Mirrors ``ideation._MAX_CRITIC_RETRIES``."""
    import gather_citations

    _clear_bfts_env(monkeypatch)
    pool = MockPool(fetchrow_result=_best_node_row())
    # LLM returns more claims than the hard cap allows.
    flooded = [
        _claim(f"c{i}", f"q{i}")
        for i in range(gather_citations._MAX_CLAIMS + 5)
    ]
    _install_stub_llm(
        monkeypatch, return_values=[{"claims": flooded}]
    )
    s2 = _SemanticScholarStub(
        return_values=[[_paper(paper_id=f"p{i}")] for i in range(gather_citations._MAX_CLAIMS)]
    )
    ctx = _GatherCtx(pool=pool, tools=_Tools(semantic_scholar=s2))

    await gather_citations.handler(
        gather_citations.Input(run_id="run-xyz", max_claims=1000), ctx
    )

    # Even with Input.max_claims=1000 and an over-flooded LLM, the
    # handler must not exceed the module cap.
    assert s2.search_papers.await_count == gather_citations._MAX_CLAIMS


@pytest.mark.asyncio
async def test_handler_uses_deterministic_step_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Step names must be stable across deploys so workflow replay maps
    cached step rows back to handler call sites. Pin the no-search,
    one-claim, two-claim shapes here."""
    import gather_citations

    _clear_bfts_env(monkeypatch)
    pool = MockPool(fetchrow_result=_best_node_row())
    _install_stub_llm(
        monkeypatch,
        return_values=[{"claims": [_claim("c1", "q1"), _claim("c2", "q2")]}],
    )
    s2 = _SemanticScholarStub(
        return_values=[
            [_paper(paper_id="p1")],
            [_paper(paper_id="p2")],
        ]
    )
    ctx = _GatherCtx(pool=pool, tools=_Tools(semantic_scholar=s2))

    await gather_citations.handler(
        gather_citations.Input(run_id="run-xyz"), ctx
    )

    assert ctx.step_calls == [
        "load_best_node",
        "extract_claims",
        "search_0",
        "search_1",
        "build_bibtex",
        "write_references",
    ]


@pytest.mark.asyncio
async def test_handler_resolves_model_via_input_env_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The draft model used for claim extraction must come from the
    standard BFTS resolver chain (Input override → BFTS_DRAFT_MODEL env →
    DEFAULT_DRAFT_MODEL). Pin all three branches in one test."""
    import gather_citations
    from _bfts_config import DEFAULT_DRAFT_MODEL

    pool = MockPool(fetchrow_result=_best_node_row())

    # --- Input override branch ---
    monkeypatch.setenv("BFTS_DRAFT_MODEL", "claude-env-test")
    stub = _install_stub_llm(monkeypatch)
    ctx = _GatherCtx(pool=pool)
    await gather_citations.handler(
        gather_citations.Input(run_id="run-xyz", draft_model="claude-input-override"), ctx
    )
    assert stub.await_args.args[0].model == "claude-input-override"

    # --- env override branch ---
    pool = MockPool(fetchrow_result=_best_node_row())
    monkeypatch.setenv("BFTS_DRAFT_MODEL", "claude-env-override-test")
    stub = _install_stub_llm(monkeypatch)
    ctx = _GatherCtx(pool=pool)
    await gather_citations.handler(gather_citations.Input(run_id="run-xyz"), ctx)
    assert stub.await_args.args[0].model == "claude-env-override-test"

    # --- default-fallback branch ---
    pool = MockPool(fetchrow_result=_best_node_row())
    monkeypatch.delenv("BFTS_DRAFT_MODEL", raising=False)
    stub = _install_stub_llm(monkeypatch)
    ctx = _GatherCtx(pool=pool)
    await gather_citations.handler(gather_citations.Input(run_id="run-xyz"), ctx)
    assert stub.await_args.args[0].model == DEFAULT_DRAFT_MODEL


@pytest.mark.asyncio
async def test_handler_validates_malformed_claims(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """JSON-Schema enforcement is best-effort across providers; the
    handler must reject claims missing ``claim`` or ``query`` keys
    rather than fan out a bogus query like ``None`` to S2."""
    import gather_citations

    _clear_bfts_env(monkeypatch)
    pool = MockPool(fetchrow_result=_best_node_row())
    _install_stub_llm(
        monkeypatch,
        return_values=[{"claims": [{"claim": "x"}]}],  # missing "query"
    )
    ctx = _GatherCtx(pool=pool)

    with pytest.raises(ValueError) as excinfo:
        await gather_citations.handler(
            gather_citations.Input(run_id="run-xyz"), ctx
        )

    assert "query" in str(excinfo.value)
