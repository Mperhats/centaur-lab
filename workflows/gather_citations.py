"""Workflow: gather_citations — best-node plan/code → references.bib (Phase 4d.3).

Closes the BFTS loop's writeup half. Given a completed ``bfts_run``:

1. Resolve the best node (``bfts_runs.best_node_id → bfts_nodes`` via
   ``_bfts_state.fetch_best_node_for_run``). Fail fast on a NULL
   ``best_node_id`` (incomplete tree, every expansion buggy) — silently
   writing an artifact against a NULL foreign key is worse than the
   error.
2. Ask the draft LLM to extract a list of factual claims from the
   plan + code, each paired with a Semantic Scholar query that would
   surface citations for it. Function-call shape so the model emits
   structured ``[{claim, query}]`` directly and we don't hit the
   regex-parsing cliff Sakana's text-mode tools fall back to.
3. Fan out one Semantic Scholar ``search_papers`` per claim
   (``fields=BIBTEX_PAPER_FIELDS`` so ``citationStyles.bibtex`` is in the
   response — added in Phase 4d.1).
4. Concatenate the BibTeX entries S2 already emitted, deduplicated by
   ``paperId`` so a downstream LaTeX compile doesn't choke on duplicate
   keys. We don't parse/regenerate BibTeX — we trust S2's emitted
   strings.
5. Persist a single ``references.bib`` artifact next to
   ``best_solution.py`` via ``_bfts_export.write_references_artifact``.

Empty cases (no claims, no S2 hits, no papers with ``bibtex``) write
an empty artifact rather than skipping the write. A future writeup
workflow can detect "tried but found nothing" via byte length without
distinguishing "artifact missing" from "empty artifact".

``SCHEDULE`` is the empty dict: ``gather_citations`` runs once per
completed run, after the operator decides the tree is done. A
populated SCHEDULE would fire orphan runs on a timer with no run_id.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from api.workflow_engine import WorkflowContext

from bfts.config import resolve_llm_api_key, resolve_llm_settings
from bfts.export import write_references_artifact
from bfts.llm import LLMCall, call_with_function
from bfts.state import fetch_best_node_for_run
from tools.semantic_scholar.client import BIBTEX_PAPER_FIELDS

WORKFLOW_NAME = "gather_citations"
SCHEDULE: dict[str, Any] = {}

# Plan §Task 4d.3: ``limit=3`` per claim, ``max_claims=8``. Both exposed
# on Input so an operator triggering a manual run can crank either knob;
# the module hard cap below is the budget guardrail.
_DEFAULT_MAX_CLAIMS = 8
_DEFAULT_SEARCHES_PER_CLAIM = 3
# Upper bound on extracted claims regardless of what ``Input.max_claims``
# requests. Caps fan-out so a misconfigured ``max_claims=1000`` can't
# burn 1000 serial S2 + LLM calls on a single run. 20 is well past the
# point of diminishing returns for a Sakana-style writeup pipeline,
# which typically cites ~10–20 papers per Stage 1 result.
_MAX_CLAIMS = 20
# Claim-extraction is creative (open-ended search-query phrasing) but
# should stay close to the plan/code wording — temperature 0.5 mirrors
# ``ideation._CRITIC_TEMP``.
_EXTRACT_TEMP = 0.5
# Trim plan/code at this many chars before feeding the prompt. Most
# Stage 1 plans are <1k chars and Stage 1 code <8k chars; the cap
# protects against the Sakana ``code`` field occasionally landing in the
# 30k-char range when the LLM dumps a full debug trace into ``code``.
_PROMPT_PLAN_CHARS = 4000
_PROMPT_CODE_CHARS = 16000


@dataclass
class Input:
    """User-triggered input for the ``gather_citations`` workflow.

    ``run_id`` is required (no sensible default); the budget knobs
    have plan-aligned defaults; LLM-override fields default to ``None``
    so ``resolve_llm_settings`` reaches the BFTS_* env / module-default
    tiers rather than being short-circuited by a hardcoded default.
    """

    run_id: str
    max_claims: int = _DEFAULT_MAX_CLAIMS
    searches_per_claim: int = _DEFAULT_SEARCHES_PER_CLAIM
    draft_model: str | None = None
    llm_api_key_secret: str | None = None


# Function spec the LLM is forced to invoke. The flat
# ``{"claims": [{"claim", "query"}]}`` shape is the smallest envelope
# that round-trips through both OpenAI ``tool_calls`` and Anthropic
# ``tool_use`` blocks unchanged — see ``_bfts_llm._extract_anthropic_tool_input``
# for the receiver. Keeping ``claim`` + ``query`` distinct (rather than
# packing them into one string) lets the prompt teach the model
# explicitly that ``query`` should be tuned for S2 phrasing while
# ``claim`` stays human-readable.
_EXTRACT_CLAIMS_FUNCTION_SPEC: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "extract_claims",
        "description": (
            "Emit a list of factual claims from the BFTS best-node plan/code "
            "that need scholarly citations, each paired with a Semantic "
            "Scholar query."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "claims": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "claim": {
                                "type": "string",
                                "description": (
                                    "Human-readable factual claim made in "
                                    "the plan or code (e.g. 'Graph attention "
                                    "outperforms vanilla GCN on citation "
                                    "networks')."
                                ),
                            },
                            "query": {
                                "type": "string",
                                "description": (
                                    "Semantic Scholar free-text search query "
                                    "tuned for the claim (e.g. 'graph "
                                    "attention networks citation "
                                    "classification benchmark')."
                                ),
                            },
                        },
                        "required": ["claim", "query"],
                    },
                },
            },
            "required": ["claims"],
        },
    },
}


_SYSTEM_PROMPT = (
    "You are an experienced AI researcher writing a literature-review "
    "section for a paper draft. Given a research plan and its "
    "implementation, list the factual claims that need to be backed up "
    "by citations from the existing literature. For each claim, emit a "
    "Semantic Scholar search query tailored to surface authoritative "
    "papers. Stay grounded in what the plan and code actually say — "
    "don't invent claims the implementation doesn't make."
)


def _truncate(text: str, *, max_chars: int) -> str:
    """Trim ``text`` to ``max_chars`` with an ellipsis on overflow.

    Plan/code occasionally land in the tens of thousands of chars
    (Sakana's ``code`` field can pick up a debug stack trace); cap
    them here so a single bad node doesn't blow the prompt budget.
    """
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "…"


def _extract_prompt(*, plan: str, code: str) -> str:
    return (
        _SYSTEM_PROMPT
        + "\n\n# Plan\n\n"
        + _truncate(plan, max_chars=_PROMPT_PLAN_CHARS)
        + "\n\n# Code\n\n"
        + _truncate(code, max_chars=_PROMPT_CODE_CHARS)
        + "\n\n# Task\n\nInvoke the `extract_claims` function with one entry "
        "per claim that needs a citation. Each entry must include both "
        "a `claim` (human-readable) and a `query` (Semantic Scholar "
        "search phrase). Aim for breadth over depth — different claims "
        "should map to different sub-areas of the literature."
    )


async def _extract(
    *, api_key: str, draft_model: str, plan: str, code: str
) -> list[dict[str, str]]:
    """One LLM call to surface the claim list.

    Returns the raw ``claims`` array; validation (each entry has both
    keys, non-empty strings) happens in ``_validate_claims``. Splitting
    the validator out keeps the LLM call site composable with replay
    semantics — ``ctx.step`` checkpoints the raw response and the
    handler validates after the step completes.
    """
    payload = await call_with_function(
        LLMCall(
            model=draft_model,
            temperature=_EXTRACT_TEMP,
            api_key=api_key,
            prompt=_extract_prompt(plan=plan, code=code),
        ),
        function_spec=_EXTRACT_CLAIMS_FUNCTION_SPEC,
    )
    claims = payload.get("claims") if isinstance(payload, dict) else None
    if not isinstance(claims, list):
        msg = (
            "extract_claims LLM response missing 'claims' array; "
            f"got {type(claims).__name__}"
        )
        raise ValueError(msg)
    return claims


def _validate_claims(claims: list[Any]) -> list[dict[str, str]]:
    """Reject malformed claim entries fail-fast.

    Both ``claim`` and ``query`` must be non-empty strings; an empty
    ``query`` would fan out a no-op ``search_papers`` call (S2 raises
    on empty query), and an empty ``claim`` provides no operator
    signal in the structured log. Mirrors ``ideation._validate_idea``'s
    treat-empty-as-missing contract.
    """
    out: list[dict[str, str]] = []
    for i, entry in enumerate(claims):
        if not isinstance(entry, dict):
            msg = (
                f"extract_claims returned non-dict entry at index {i}: "
                f"{type(entry).__name__}"
            )
            raise ValueError(msg)
        claim = entry.get("claim")
        query = entry.get("query")
        if not isinstance(claim, str) or not claim.strip():
            msg = f"extract_claims entry {i} missing or empty 'claim'"
            raise ValueError(msg)
        if not isinstance(query, str) or not query.strip():
            msg = f"extract_claims entry {i} missing or empty 'query'"
            raise ValueError(msg)
        out.append({"claim": claim.strip(), "query": query.strip()})
    return out


def _to_bibtex(results: list[list[dict[str, Any]]]) -> tuple[str, int, int]:
    """Concatenate ``citationStyles.bibtex`` from S2 results.

    Returns ``(bibtex_body, kept_count, skipped_count)``:
    - ``bibtex_body`` is the joined entries (``\\n\\n`` between), or
      ``""`` if no usable entries.
    - ``kept_count`` is how many distinct papers contributed an entry.
    - ``skipped_count`` counts papers S2 returned without a
      ``citationStyles.bibtex`` payload — those drop silently (S2
      occasionally hasn't generated a citation entry yet for very new
      papers); the caller can log the count for operator visibility.

    Deduplicated by ``paperId`` so cross-claim hits on the same paper
    don't produce duplicate BibTeX keys (which would break ``bibtex``
    and ``biber`` at compile time).
    """
    seen: set[str] = set()
    entries: list[str] = []
    skipped = 0
    for paper_list in results:
        if not isinstance(paper_list, list):
            continue
        for paper in paper_list:
            if not isinstance(paper, dict):
                continue
            paper_id = paper.get("paperId")
            if not isinstance(paper_id, str) or not paper_id:
                continue
            if paper_id in seen:
                continue
            styles = paper.get("citationStyles")
            bibtex = (
                styles.get("bibtex") if isinstance(styles, dict) else None
            )
            if not isinstance(bibtex, str) or not bibtex.strip():
                skipped += 1
                continue
            seen.add(paper_id)
            entries.append(bibtex.strip())
    return "\n\n".join(entries), len(entries), skipped


async def handler(inp: Input, ctx: WorkflowContext) -> dict[str, Any]:
    if not inp.run_id or not inp.run_id.strip():
        msg = "run_id cannot be empty"
        raise ValueError(msg)

    llm = resolve_llm_settings(
        draft_model=inp.draft_model,
        llm_api_key_secret=inp.llm_api_key_secret,
    )
    api_key = resolve_llm_api_key(llm.llm_api_key_secret)
    pool = ctx._pool
    run_id = inp.run_id.strip()

    best = await ctx.step(
        "load_best_node",
        lambda: fetch_best_node_for_run(pool, run_id=run_id),
    )
    if best is None:
        msg = (
            f"bfts_run {run_id} has no best_node_id (incomplete or every "
            "expansion was buggy); cannot gather citations"
        )
        raise ValueError(msg)
    best_node_id = str(best["node_id"])
    plan = str(best.get("plan") or "")
    code = str(best.get("code") or "")

    raw_claims = await ctx.step(
        "extract_claims",
        lambda: _extract(
            api_key=api_key,
            draft_model=llm.draft_model,
            plan=plan,
            code=code,
        ),
    )
    claims = _validate_claims(raw_claims)

    # Cap to the lower of the user-supplied budget and the module hard
    # cap so a misconfigured ``Input.max_claims=1000`` cannot fan out
    # 1000 S2 calls. Negative or zero is treated as "no claims" — the
    # write-empty path below handles that uniformly with the
    # LLM-returned-zero case.
    effective_cap = max(0, min(inp.max_claims, _MAX_CLAIMS))
    if len(claims) > effective_cap:
        ctx.log(
            "gather_citations_claims_capped",
            requested=inp.max_claims,
            extracted=len(claims),
            applied=effective_cap,
        )
        claims = claims[:effective_cap]

    if not claims:
        ctx.log(
            "gather_citations_no_claims_extracted",
            run_id=run_id,
            node_id=best_node_id,
        )

    # ``searches_per_claim`` is bounded by S2's per-call ``limit``
    # (1..100) so we don't need our own clamp here; the call site
    # already validates it. We clamp negatives to 1 so a misconfigured
    # zero / negative doesn't accidentally make S2 reject the request.
    searches_per_claim = max(1, inp.searches_per_claim)

    results: list[list[dict[str, Any]]] = []
    for i, claim_entry in enumerate(claims):
        query = claim_entry["query"]
        papers = await ctx.step(
            f"search_{i}",
            lambda q=query, lim=searches_per_claim: ctx.tools.semantic_scholar.search_papers(
                query=q, limit=lim, fields=BIBTEX_PAPER_FIELDS
            ),
        )
        results.append(list(papers) if papers else [])

    bibtex_body, kept, skipped = await ctx.step(
        "build_bibtex",
        lambda: _to_bibtex(results),
    )
    if skipped:
        ctx.log(
            "gather_citations_skipped_papers_without_bibtex",
            run_id=run_id,
            node_id=best_node_id,
            skipped=skipped,
        )
    if claims and kept == 0:
        ctx.log(
            "gather_citations_no_papers_found",
            run_id=run_id,
            node_id=best_node_id,
            claims=len(claims),
        )

    artifact_id = await ctx.step(
        "write_references",
        lambda: write_references_artifact(
            pool, node_id=best_node_id, bibtex=bibtex_body
        ),
    )

    ctx.log(
        "gather_citations_completed",
        run_id=run_id,
        node_id=best_node_id,
        claims=len(claims),
        papers=kept,
        skipped=skipped,
        artifact_id=artifact_id,
    )
    return {
        "node_id": best_node_id,
        "claims": len(claims),
        "papers": kept,
        "skipped": skipped,
        "artifact_id": artifact_id,
    }
