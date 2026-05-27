"""Workflow: ideation — research topic to structured ``idea`` dict (Phase 4d.2).

Turns a one-sentence research topic into a structured ``idea`` dict
that ``bfts_root.Input.idea`` consumes directly. Port of Sakana's
``.scientist/ai_scientist/perform_ideation_temp_free.py`` (research 02
§Outer loop):

1. Search Semantic Scholar for ``seed_paper_limit`` seed papers grounding
   the topic in real literature.
2. Call the draft LLM with a ``finalize_idea`` function spec so the model
   emits the structured proposal directly (avoids the regex-based
   ``ACTION:`` / ``ARGUMENTS:`` parsing Sakana uses for OpenAI-only
   text-mode tools — our ``_bfts_llm.call_with_function`` works against
   both Anthropic and OpenAI shapes).
3. Optionally re-invoke the LLM ``critic_retries`` times for refinement;
   each retry sees the prior draft and a "sharpen the hypothesis" prompt.

Returned envelope is ``{"idea": <dict>, "seed_papers": [<paperId>, ...],
"papers_persisted": {...}}`` so a downstream ``bfts_root`` POST can pass
``run_input["idea"]`` straight through. Seed papers from the S2 search are
**always** persisted via a child ``save_papers`` run (brief + paper rows in
``company_context_documents``) when the search returns any ``paperId``s —
not optional for the caller.

``SCHEDULE`` is the empty dict: ideation is user-triggered via a manual
POST to ``/workflows/runs``; a populated SCHEDULE would burn LLM budget
firing orphan runs on a timer with no topic.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from api.workflow_engine import WorkflowContext

from packages.bfts_sdk.config import resolve_llm_api_key, resolve_llm_settings
from packages.bfts_sdk.llm import LLMCall, call_with_function
from packages.bfts_sdk.research import build_bfts_run_input

WORKFLOW_NAME = "ideation"
SCHEDULE: dict[str, Any] = {}

_DEFAULT_SEED_PAPER_LIMIT = 10
# Draft-temp matches ``_bfts_expand._DRAFT_TEMP`` (creative generation);
# critic-temp drops to feedback-temp so refinements stay close to the
# original proposal.
_IDEATION_TEMP = 1.0
_CRITIC_TEMP = 0.5
_SEED_ABSTRACT_CHARS = 600
# Upper bound for ``Input.critic_retries`` so a misconfigured caller
# (e.g. ``critic_retries=1000``) cannot burn budget on serial LLM calls.
# Five rounds is well past the point of diminishing returns for the
# Sakana-style refinement loop.
_MAX_CRITIC_RETRIES = 5

# Full Sakana ``FinalizeIdea`` schema. The plan only requires the first
# four downstream; the rest are emitted so the ``_bfts_expand`` prompt
# (which renders the entire idea dict as markdown headers) sees the same
# fields Sakana's ``parallel_agent`` does. The tests assert the loose
# subset so a future schema change can drop optional fields without
# churning every test.
_REQUIRED_IDEA_FIELDS: tuple[str, ...] = (
    "Name",
    "Title",
    "Short Hypothesis",
    "Related Work",
    "Abstract",
    "Experiments",
    "Risk Factors and Limitations",
)


@dataclass
class Input:
    """User-triggered input for the ``ideation`` workflow.

    ``topic`` is the only required field; the LLM-override fields default
    to ``None`` so ``resolve_llm_settings`` reaches the BFTS_* env / module
    defaults rather than being short-circuited by a hardcoded default.
    """

    topic: str
    seed_paper_limit: int = _DEFAULT_SEED_PAPER_LIMIT
    critic_retries: int = 0
    draft_model: str | None = None
    llm_api_key_secret: str | None = None
    # Optional overrides for ``output_json["bfts_run_input"]``. When omitted,
    # ``build_bfts_run_input`` applies research defaults (num_seeds=3, …).
    num_seeds: int | None = None
    num_drafts: int | None = None
    num_workers: int | None = None
    thread_key: str | None = None
    delivery: dict[str, Any] | None = None


# Anthropic tool schemas require property keys matching ``^[a-zA-Z0-9_.-]{1,64}$``
# (no spaces). Sakana's display labels (``Short Hypothesis``, etc.) are mapped
# back in ``_normalize_idea_from_tool`` for ``_bfts_expand`` markdown headers.
_IDEA_TOOL_PROPERTY_KEYS: tuple[str, ...] = (
    "name",
    "title",
    "short_hypothesis",
    "related_work",
    "abstract",
    "experiments",
    "risk_factors_and_limitations",
)

_TOOL_KEY_TO_IDEA_LABEL: dict[str, str] = {
    "name": "Name",
    "title": "Title",
    "short_hypothesis": "Short Hypothesis",
    "related_work": "Related Work",
    "abstract": "Abstract",
    "experiments": "Experiments",
    "risk_factors_and_limitations": "Risk Factors and Limitations",
}

_IDEA_FUNCTION_SPEC: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "finalize_idea",
        "description": "Emit a structured research proposal as a single JSON object.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": (
                        "Short descriptor. Lowercase, no spaces, underscores allowed."
                    ),
                },
                "title": {
                    "type": "string",
                    "description": "Catchy informative title for the proposal.",
                },
                "short_hypothesis": {
                    "type": "string",
                    "description": (
                        "Concise main hypothesis or research question; justify "
                        "why this is the best setting to investigate it."
                    ),
                },
                "related_work": {
                    "type": "string",
                    "description": (
                        "Most relevant related work and how the proposal "
                        "distinguishes from it (not a trivial extension)."
                    ),
                },
                "abstract": {
                    "type": "string",
                    "description": "Conference-format abstract (~250 words).",
                },
                "experiments": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Experiments to validate the proposal — specific, "
                        "feasible at academic scale, with evaluation metrics."
                    ),
                },
                "risk_factors_and_limitations": {
                    "type": "string",
                    "description": "Potential risks and limitations of the proposal.",
                },
            },
            "required": list(_IDEA_TOOL_PROPERTY_KEYS),
        },
    },
}


def _normalize_idea_from_tool(raw: dict[str, Any]) -> dict[str, Any]:
    """Map provider-safe tool keys to canonical idea labels for BFTS prompts."""
    if not raw:
        return {}
    out: dict[str, Any] = {}
    for tool_key, label in _TOOL_KEY_TO_IDEA_LABEL.items():
        if tool_key in raw and raw[tool_key] not in (None, ""):
            out[label] = raw[tool_key]
        elif label in raw and raw[label] not in (None, ""):
            out[label] = raw[label]
    experiments = out.get("Experiments")
    if isinstance(experiments, str) and experiments.strip():
        out["Experiments"] = [experiments.strip()]
    elif isinstance(experiments, list):
        out["Experiments"] = [str(x).strip() for x in experiments if str(x).strip()]
    return out


_SYSTEM_PROMPT = (
    "You are an experienced AI researcher proposing high-impact research "
    "ideas resembling exciting grant proposals. Be very creative and think "
    "out of the box; each proposal should stem from a simple and elegant "
    "question or observation. Clearly distinguish from existing literature. "
    "Ensure proposals do not require resources beyond what an academic lab "
    "could afford, and would lead to papers publishable at top ML conferences."
)


def _format_seed_papers(papers: list[dict[str, Any]]) -> str:
    """Render seed papers as a bullet list the LLM can ground on.

    Truncates abstracts to ``_SEED_ABSTRACT_CHARS`` so a 10-paper search
    stays well under prompt budgets even when S2 returns long abstracts.
    """
    if not papers:
        return "(no seed literature available)"
    lines: list[str] = []
    for paper in papers:
        title = paper.get("title") or "(untitled)"
        year = paper.get("year") or "?"
        abstract = (paper.get("abstract") or "").strip()
        if len(abstract) > _SEED_ABSTRACT_CHARS:
            abstract = abstract[:_SEED_ABSTRACT_CHARS] + "…"
        lines.append(f"- ({year}) {title}\n  {abstract}")
    return "\n".join(lines)


def _ideation_prompt(*, topic: str, papers: list[dict[str, Any]]) -> str:
    return (
        _SYSTEM_PROMPT
        + "\n\n# Research topic\n\n"
        + topic
        + "\n\n# Seed literature\n\n"
        + _format_seed_papers(papers)
        + "\n\n# Task\n\n"
        "Propose ONE novel research idea grounded in the seed literature "
        "above but clearly distinguished from it. Invoke the "
        "`finalize_idea` function with the structured proposal. Ensure "
        "every required field is populated and that `Name` is lowercase "
        "with underscores (no spaces)."
    )


def _critique_prompt(*, topic: str, idea: dict[str, Any], round_index: int) -> str:
    rendered = "\n\n".join(
        f"## {key}\n{idea.get(key, '')}" for key in _REQUIRED_IDEA_FIELDS
    )
    return (
        _SYSTEM_PROMPT
        + "\n\n# Research topic\n\n"
        + topic
        + "\n\n# Draft proposal (round "
        + str(round_index + 1)
        + ")\n\n"
        + rendered
        + "\n\n# Task\n\n"
        "Carefully consider the quality, novelty, and feasibility of the "
        "proposal above. Refine it: sharpen the hypothesis, verify the "
        "experiments are feasible at academic scale, and improve novelty "
        "vs. the existing literature. Stick to the spirit of the original "
        "idea unless there are glaring issues. Invoke `finalize_idea` "
        "with the revised proposal."
    )


# Subset of ``_REQUIRED_IDEA_FIELDS`` that downstream consumers
# (``bfts_root``, ``_bfts_expand``) actually read; provider-side JSON
# Schema enforcement is best-effort across OpenAI/Anthropic shapes, so
# we re-check post-call to fail fast on partial dicts rather than let a
# malformed idea poison a bfts_root run.
_PLAN_REQUIRED_IDEA_FIELDS: tuple[str, ...] = (
    "Name",
    "Title",
    "Short Hypothesis",
    "Experiments",
)


def _validate_idea(idea: dict[str, Any]) -> None:
    """Raise ``ValueError`` if ``idea`` is missing any plan-required field.

    Treats empty strings as missing — an empty ``Short Hypothesis`` is as
    useless to ``_bfts_expand`` as a missing key.
    """
    missing = [f for f in _PLAN_REQUIRED_IDEA_FIELDS if not idea.get(f)]
    if missing:
        msg = f"ideation LLM returned idea missing required fields: {missing}"
        raise ValueError(msg)


async def _synthesize(
    *,
    api_key: str,
    draft_model: str,
    topic: str,
    papers: list[dict[str, Any]],
) -> dict[str, Any]:
    raw = await call_with_function(
        LLMCall(
            model=draft_model,
            temperature=_IDEATION_TEMP,
            api_key=api_key,
            prompt=_ideation_prompt(topic=topic, papers=papers),
        ),
        function_spec=_IDEA_FUNCTION_SPEC,
    )
    return _normalize_idea_from_tool(raw)


async def _critique(
    *,
    api_key: str,
    draft_model: str,
    topic: str,
    idea: dict[str, Any],
    round_index: int,
) -> dict[str, Any]:
    raw = await call_with_function(
        LLMCall(
            model=draft_model,
            temperature=_CRITIC_TEMP,
            api_key=api_key,
            prompt=_critique_prompt(
                topic=topic, idea=idea, round_index=round_index
            ),
        ),
        function_spec=_IDEA_FUNCTION_SPEC,
    )
    return _normalize_idea_from_tool(raw)


def _child_workflow_output(result: dict[str, Any] | None) -> dict[str, Any]:
    """Extract ``output_json`` from ``ctx.wait_for_workflow`` response."""
    if not isinstance(result, dict):
        return {}
    output = result.get("output_json")
    return output if isinstance(output, dict) else {}


async def _persist_seed_papers(
    ctx: WorkflowContext,
    *,
    topic: str,
    paper_ids: list[str],
) -> dict[str, Any]:
    """Run ``save_papers`` as a child so seed literature is always in the DB."""
    child = await ctx.start_workflow(
        "persist_seed_papers",
        workflow_name="save_papers",
        run_input={"paper_ids": paper_ids, "query": topic},
        trigger_key=f"{ctx.run_id}:seed_papers",
        eager_start=True,
    )
    result = await ctx.wait_for_workflow(
        "wait_persist_seed_papers",
        run_id=child["run_id"],
    )
    output = _child_workflow_output(result)
    ctx.log(
        "ideation_seed_papers_persisted",
        topic=topic,
        paper_count=len(paper_ids),
        brief_document_id=output.get("brief_document_id"),
        papers_inserted=output.get("papers_inserted"),
        papers_updated=output.get("papers_updated"),
        papers_failed=output.get("papers_failed"),
    )
    return output


def _seed_paper_ids(papers: list[dict[str, Any]]) -> list[str]:
    """Extract the S2 paperIds in result order.

    Silently skips entries without a ``paperId`` — S2 always returns it
    for ``/paper/search`` regardless of ``fields``, but a future S2
    response shape change shouldn't crash ideation.
    """
    out: list[str] = []
    for paper in papers or []:
        if not isinstance(paper, dict):
            continue
        pid = paper.get("paperId")
        if pid:
            out.append(str(pid))
    return out


async def handler(inp: Input, ctx: WorkflowContext) -> dict[str, Any]:
    if not inp.topic or not inp.topic.strip():
        msg = "topic cannot be empty"
        raise ValueError(msg)

    llm = resolve_llm_settings(
        draft_model=inp.draft_model,
        llm_api_key_secret=inp.llm_api_key_secret,
    )
    api_key = resolve_llm_api_key(llm.llm_api_key_secret)
    topic = inp.topic.strip()

    papers = await ctx.step(
        "seed_search",
        lambda: ctx.tools.semantic_scholar.search_papers(
            query=topic, limit=inp.seed_paper_limit
        ),
    )

    seed_paper_ids = _seed_paper_ids(papers)

    # Silent-degradation case: S2 outage or no-match query. The workflow
    # still proceeds (the LLM can produce something less well-grounded),
    # but a log surfaces it for operators inspecting low-quality runs.
    if not seed_paper_ids:
        ctx.log("ideation_no_seed_papers", topic=topic)
        papers_persisted: dict[str, Any] = {
            "status": "skipped",
            "reason": "no_seed_papers",
        }
    else:
        papers_persisted = await _persist_seed_papers(
            ctx,
            topic=topic,
            paper_ids=seed_paper_ids,
        )

    idea = await ctx.step(
        "synthesize_idea",
        lambda: _synthesize(
            api_key=api_key,
            draft_model=llm.draft_model,
            topic=topic,
            papers=papers,
        ),
    )
    _validate_idea(idea)

    # Each retry needs a unique deterministic step name so workflow
    # replay maps cached step rows back to the right call. The
    # critic-retries=0 default skips this loop entirely (opt-in only).
    # Upper-clamped to ``_MAX_CRITIC_RETRIES`` so a misconfigured caller
    # cannot burn budget on serial LLM calls.
    critic_retries = max(0, min(inp.critic_retries, _MAX_CRITIC_RETRIES))
    if critic_retries != inp.critic_retries:
        ctx.log(
            "ideation_critic_retries_clamped",
            requested=inp.critic_retries,
            applied=critic_retries,
        )
    for round_index in range(critic_retries):
        idea = await ctx.step(
            f"validate_idea_{round_index}",
            lambda current=idea, i=round_index: _critique(
                api_key=api_key,
                draft_model=llm.draft_model,
                topic=topic,
                idea=current,
                round_index=i,
            ),
        )
        _validate_idea(idea)

    ctx.log(
        "ideation_completed",
        topic=topic,
        seed_papers=len(seed_paper_ids),
        draft_model=llm.draft_model,
        critic_retries=critic_retries,
    )
    bfts_run_input = build_bfts_run_input(
        idea=idea,
        run_input=ctx.run_input,
        thread_key=inp.thread_key,
        delivery=inp.delivery,
        num_seeds=inp.num_seeds,
        num_drafts=inp.num_drafts,
        num_workers=inp.num_workers,
    )

    return {
        "idea": idea,
        "seed_papers": seed_paper_ids,
        "papers_persisted": papers_persisted,
        "bfts_run_input": bfts_run_input,
    }
