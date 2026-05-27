"""Semantic Scholar query planning for literature-grounded workflows.

Mirrors the planner loop in upstream ``websearch.deep_research``: when an
initial topic returns zero papers, an LLM proposes shorter keyword-style
queries; callers retry S2 until a query hits or rounds are exhausted.
"""

from __future__ import annotations

import json
from typing import Any

from packages.bfts_sdk.llm import LLMCall, call_with_function

DEFAULT_MAX_PLANNER_ROUNDS = 2
DEFAULT_QUERIES_PER_PLAN = 4
_PLANNER_TEMP = 0.3
_PLANNER_MAX_TOKENS = 1600

QUERY_PLANNER_SYSTEM = """## ROLE
You are a literature search query planner for Semantic Scholar.

## GOAL
Given a research topic and prior failed queries, produce focused keyword-style
queries that are likely to return academic papers.

## RULES
- Output valid JSON only via the provided tool. No markdown. No prose outside JSON.
- Prefer short keyword queries (typically 2-6 words), not full sentences.
- Generate diverse, non-overlapping queries that target distinct angles of the topic.
- Use prior_queries to avoid repetition.
- Use prior_gaps to understand why earlier searches failed (often: query too long,
  too conversational, or overly specific phrasing).
- Do not include years, journal names, or boolean operators unless essential.
- Keep each query concise and high-signal.

## JSON CONTRACT
{
  "queries": ["string"],
  "reason": "string"
}
"""

_PLAN_FUNCTION_SPEC: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "plan_literature_queries",
        "description": (
            "Produce Semantic Scholar keyword queries when prior searches "
            "returned zero papers."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "queries": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Up to four keyword-style Semantic Scholar queries.",
                },
                "reason": {
                    "type": "string",
                    "description": "Brief rationale for the chosen queries.",
                },
            },
            "required": ["queries"],
        },
    },
}


def dedupe_queries(queries: list[str], *, limit: int) -> list[str]:
    """Return up to ``limit`` non-empty queries, case-insensitively unique."""
    deduped: list[str] = []
    seen: set[str] = set()
    for query in queries:
        normalized = query.strip()
        key = normalized.casefold()
        if not normalized or key in seen:
            continue
        seen.add(key)
        deduped.append(normalized)
        if len(deduped) >= limit:
            break
    return deduped


def normalize_planner_payload(payload: dict[str, Any], *, query_limit: int) -> dict[str, Any]:
    """Coerce planner tool output into a stable dict shape."""
    raw_queries = payload.get("queries", [])
    raw_queries = (
        [str(query) for query in raw_queries]
        if isinstance(raw_queries, list)
        else []
    )
    return {
        "reason": str(payload.get("reason", "")).strip(),
        "queries": dedupe_queries(raw_queries, limit=query_limit),
    }


def build_planner_user_prompt(
    *,
    topic: str,
    prior_queries: list[str],
    prior_gaps: list[str],
) -> str:
    return json.dumps(
        {
            "topic": topic.strip(),
            "prior_queries": prior_queries,
            "prior_gaps": prior_gaps,
        },
        indent=2,
    )


async def plan_literature_queries(
    *,
    topic: str,
    prior_queries: list[str],
    prior_gaps: list[str],
    api_key: str,
    draft_model: str,
    query_limit: int = DEFAULT_QUERIES_PER_PLAN,
) -> dict[str, Any]:
    """Ask the draft LLM for alternate Semantic Scholar queries."""
    user_prompt = build_planner_user_prompt(
        topic=topic,
        prior_queries=prior_queries,
        prior_gaps=prior_gaps,
    )
    raw = await call_with_function(
        LLMCall(
            model=draft_model,
            temperature=_PLANNER_TEMP,
            api_key=api_key,
            prompt=f"{QUERY_PLANNER_SYSTEM}\n\n# Input\n\n{user_prompt}",
            max_tokens=_PLANNER_MAX_TOKENS,
        ),
        function_spec=_PLAN_FUNCTION_SPEC,
    )
    if not isinstance(raw, dict):
        msg = f"literature planner returned unexpected type: {type(raw).__name__}"
        raise RuntimeError(msg)
    return normalize_planner_payload(raw, query_limit=query_limit)


def queries_not_yet_tried(
    candidate_queries: list[str],
    prior_queries: list[str],
) -> list[str]:
    """Drop queries already attempted (case-insensitive)."""
    seen = {query.strip().casefold() for query in prior_queries if query.strip()}
    fresh: list[str] = []
    for query in candidate_queries:
        normalized = query.strip()
        if not normalized or normalized.casefold() in seen:
            continue
        fresh.append(normalized)
    return fresh
