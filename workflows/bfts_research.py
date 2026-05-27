"""Workflow: research brief + ideation → ``bfts_root`` on one Slack agent-session.

Slack-driven science entrypoint:

1. **Agent turn** (``slack_thread_turn``): no chat text — the workflow owns
   thread delivery (avoids duplicate kickoff lines in the agent stream).
2. **Research agent-session** (one Slack message, opened by ``bfts_research``):
   step-by-step plan view rendered via ``chat.startStream`` / ``task_update``
   — ``literature_search``, optional ``query_refinement``, ``ideation``,
   then handed off to ``bfts_root`` which keeps updating ``bfts_trees`` /
   ``tree_{i}`` on the same session.
3. **Plain thread posts** (deliverables only): the rendered research brief
   markdown and the structured idea block. The agent-session shows
   *status*; the thread posts carry the *content*.

Failures post to the Slack thread and transition the open session to an
``error`` step + ``chat.stopStream``. ``bfts_root`` runs asynchronously —
its errors are also reported from ``bfts_root`` via ``notify_run_failure``
(not by re-waiting here).

Falls back to plain thread posts (no agent-session) when ``delivery`` /
``SLACKBOT_URL`` are unset.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from api.workflow_engine import WorkflowContext

from packages.bfts_sdk.config import resolve_llm_api_key, resolve_llm_settings
from packages.bfts_sdk.literature_query import (
    DEFAULT_MAX_PLANNER_ROUNDS,
    DEFAULT_QUERIES_PER_PLAN,
    plan_literature_queries,
    queries_not_yet_tried,
)
from packages.bfts_sdk.research import build_bfts_run_input
from tools.bfts_runner.slack.format import (
    format_empty_literature_thread_message,
    format_idea_markdown,
    format_research_brief_thread_message,
)
from tools.bfts_runner.slack.post import (
    enrich_run_input_from_headers,
    enrich_slack_delivery_recipient,
    notify_thread_failure,
    post_thread_message,
    resolve_slack_delivery,
    workflow_run_error_text,
    workflow_run_failed,
)
from tools.bfts_runner.slack.stream import (
    SlackStreamTarget,
    close_session,
    notify_run_failure,
    open_session,
    post_step,
    streaming_available,
)
from workflows.ideation import _child_workflow_output

WORKFLOW_NAME = "bfts_research"
SCHEDULE: dict[str, Any] = {}

_DEFAULT_BRIEF_LIMIT = 4

_STEP_LITERATURE = "literature_search"
_STEP_QUERY_REFINEMENT = "query_refinement"
_STEP_IDEATION = "ideation"


class _ResearchPipelineStop(RuntimeError):
    """Raised after Slack was already notified; skip generic failure wrapper."""


def _brief_results_count(brief_result: dict[str, Any]) -> int:
    raw = brief_result.get("results_count")
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str) and raw.isdigit():
        return int(raw)
    return 0


def _brief_has_results(brief_result: dict[str, Any]) -> bool:
    return (
        str(brief_result.get("status") or "") == "completed"
        and _brief_results_count(brief_result) > 0
    )


async def _run_research_brief_step(
    ctx: WorkflowContext,
    *,
    step_name: str,
    query: str,
    limit: int,
) -> dict[str, Any]:
    return await ctx.step(
        step_name,
        lambda q=query, lim=limit: ctx.tools.semantic_scholar.research_brief(
            query=q,
            limit=lim,
        ),
    )


async def _resolve_literature_brief(
    ctx: WorkflowContext,
    *,
    topic: str,
    brief_limit: int,
    draft_model: str | None,
    llm_api_key_secret: str | None,
    stream: SlackStreamTarget | None = None,
) -> tuple[str, dict[str, Any], list[str]]:
    """Search S2 for a literature brief, refining the query when needed.

    Transitions the ``query_refinement`` Slack step (when the planner runs)
    inline so the user sees live status during the (potentially 30s+) refine
    loop without extra thread posts.
    """
    llm = resolve_llm_settings(
        draft_model=draft_model,
        llm_api_key_secret=llm_api_key_secret,
    )
    api_key = resolve_llm_api_key(llm.llm_api_key_secret)

    prior_queries: list[str] = [topic]
    prior_gaps: list[str] = []

    brief_result = await _run_research_brief_step(
        ctx,
        step_name="research_brief",
        query=topic,
        limit=brief_limit,
    )
    if not isinstance(brief_result, dict):
        msg = f"research_brief returned unexpected type: {type(brief_result).__name__}"
        raise RuntimeError(msg)
    if _brief_has_results(brief_result):
        return topic, brief_result, prior_queries

    if str(brief_result.get("status") or "") != "completed":
        return topic, brief_result, prior_queries

    prior_gaps.append("Semantic Scholar returned zero papers for the original query.")
    await post_step(
        ctx,
        stream,
        step_id=_STEP_QUERY_REFINEMENT,
        title="Refine literature query",
        status="in_progress",
        details="Zero papers for the original wording — trying shorter keyword queries…",
        step_name="stream_step_refine_query_in_progress",
    )

    for plan_round in range(1, DEFAULT_MAX_PLANNER_ROUNDS + 1):
        planner = await ctx.step(
            f"plan_literature_queries_{plan_round}",
            lambda pq=list(prior_queries), pg=list(prior_gaps), t=topic: plan_literature_queries(
                topic=t,
                prior_queries=pq,
                prior_gaps=pg,
                api_key=api_key,
                draft_model=llm.draft_model,
                query_limit=DEFAULT_QUERIES_PER_PLAN,
            ),
        )
        candidate_queries = queries_not_yet_tried(
            planner.get("queries") if isinstance(planner, dict) else [],
            prior_queries,
        )
        if not candidate_queries:
            prior_gaps.append(
                f"Planner round {plan_round} produced no new queries "
                f"({planner.get('reason', '') if isinstance(planner, dict) else ''})."
            )
            continue

        tried_this_round = 0
        for query_index, query in enumerate(candidate_queries[:DEFAULT_QUERIES_PER_PLAN]):
            brief_result = await _run_research_brief_step(
                ctx,
                step_name=f"research_brief_plan_{plan_round}_{query_index}",
                query=query,
                limit=brief_limit,
            )
            prior_queries.append(query)
            tried_this_round += 1
            if not isinstance(brief_result, dict):
                continue
            if str(brief_result.get("status") or "") != "completed":
                prior_gaps.append(
                    f"Query {query!r} failed: {brief_result.get('error', 'unknown error')}"
                )
                continue
            if _brief_has_results(brief_result):
                ctx.log(
                    "bfts_research_literature_query_refined",
                    original_topic=topic,
                    effective_query=query,
                    plan_round=plan_round,
                    queries_tried=prior_queries,
                )
                await post_step(
                    ctx,
                    stream,
                    step_id=_STEP_QUERY_REFINEMENT,
                    title="Refine literature query",
                    status="complete",
                    details=f"Found {_brief_results_count(brief_result)} papers "
                    f"with `{query}`.",
                    step_name="stream_step_refine_query_complete",
                )
                return query, brief_result, prior_queries

        prior_gaps.append(
            f"Planner round {plan_round} tried {tried_this_round} queries; "
            "all returned zero papers."
        )

    await post_step(
        ctx,
        stream,
        step_id=_STEP_QUERY_REFINEMENT,
        title="Refine literature query",
        status="error",
        details=f"Tried {len(prior_queries)} queries; all returned zero papers.",
        step_name="stream_step_refine_query_error",
    )
    return topic, brief_result, prior_queries


@dataclass
class Input:
    topic: str
    thread_key: str | None = None
    delivery: dict[str, Any] | None = None
    num_seeds: int | None = None
    num_drafts: int | None = None
    num_workers: int | None = None
    seed_paper_limit: int | None = None
    brief_paper_limit: int | None = None
    critic_retries: int = 0
    draft_model: str | None = None
    llm_api_key_secret: str | None = None


def _slack_metadata(ctx: WorkflowContext) -> dict[str, Any]:
    raw = ctx.run_input.get("metadata")
    return dict(raw) if isinstance(raw, dict) else {}


def _brief_markdown_for_slack(brief_result: dict[str, Any]) -> str:
    if brief_result.get("status") == "completed":
        return str(
            brief_result.get("compact_markdown") or brief_result.get("markdown") or ""
        ).strip()
    return ""


def _session_title(topic: str) -> str:
    label = topic.strip() or "Research pipeline"
    if len(label) > 80:
        return label[:77].rstrip() + "…"
    return label


async def _run_research_pipeline(inp: Input, ctx: WorkflowContext) -> dict[str, Any]:
    topic = inp.topic.strip()
    merged_input = enrich_run_input_from_headers(
        header_thread_key=(
            inp.thread_key
            or str(ctx.run_input.get("thread_key") or "")
            or None
        ),
        run_input=dict(ctx.run_input),
    )
    thread_key = str(merged_input.get("thread_key") or "").strip()
    delivery = resolve_slack_delivery(
        explicit_delivery=inp.delivery or merged_input.get("delivery"),
        run_input=merged_input,
        explicit_thread_key=thread_key or inp.thread_key,
    )
    # The slackbot's ``session_step`` endpoint requires a valid Slack user id
    # in ``recipient_user_id`` (``^[UW][A-Z0-9]{2,}$``); without it Slack 502s
    # every ``task_update`` chunk. Mirrors the ``bfts_root`` enrichment
    # (workflows/bfts_root.py:285) so a session opened here renders
    # correctly when ``bfts_root`` later posts ``bfts_trees`` / ``tree_{i}``
    # rows on the same session id.
    delivery = await enrich_slack_delivery_recipient(
        ctx,
        delivery,
        thread_key=thread_key or inp.thread_key,
    )
    use_research_stream = streaming_available() and bool(delivery and thread_key)
    metadata = _slack_metadata(ctx)
    research_session: SlackStreamTarget | None = None
    brief_limit = inp.brief_paper_limit or inp.seed_paper_limit or _DEFAULT_BRIEF_LIMIT

    try:
        if use_research_stream and delivery:
            research_session = await open_session(
                ctx,
                delivery=delivery,
                thread_key=thread_key,
                metadata=metadata,
                title=_session_title(topic),
                header=None,
                step_name="open_slack_research_stream",
            )

        await post_step(
            ctx,
            research_session,
            step_id=_STEP_LITERATURE,
            title="Search the literature",
            status="in_progress",
            details=f"Querying Semantic Scholar for: {topic}",
            step_name="stream_step_lit_search_in_progress",
        )

        literature_query, brief_result, queries_tried = await _resolve_literature_brief(
            ctx,
            topic=topic,
            brief_limit=brief_limit,
            draft_model=inp.draft_model,
            llm_api_key_secret=inp.llm_api_key_secret,
            stream=research_session,
        )

        brief_markdown = _brief_markdown_for_slack(brief_result)
        results_count = _brief_results_count(brief_result)
        is_completed = str(brief_result.get("status") or "") == "completed"

        if is_completed and results_count == 0:
            await post_step(
                ctx,
                research_session,
                step_id=_STEP_LITERATURE,
                title="Search the literature",
                status="error",
                details=f"No papers found across {len(queries_tried)} queries.",
                step_name="stream_step_lit_search_empty",
            )
            if delivery:
                await post_thread_message(
                    ctx,
                    delivery=delivery,
                    text=format_empty_literature_thread_message(
                        topic=topic,
                        queries_tried=queries_tried,
                    ),
                    step_name="post_slack_empty_literature",
                    log_event="bfts_research_slack_empty_literature_failed",
                )
            await close_session(
                ctx, research_session,
                step_name="close_research_stream_empty_literature",
            )
            raise _ResearchPipelineStop(
                "Semantic Scholar returned no papers after query refinement; "
                "ask the user to broaden their search and retry."
            )

        if not is_completed:
            err = workflow_run_error_text(brief_result)
            await post_step(
                ctx,
                research_session,
                step_id=_STEP_LITERATURE,
                title="Search the literature",
                status="error",
                details=err,
                step_name="stream_step_lit_search_error",
            )
            if delivery:
                await notify_thread_failure(
                    ctx,
                    delivery=delivery,
                    headline="Research brief failed",
                    orchestrator_run_id=ctx.run_id,
                    error_text=err,
                    step_name="post_slack_research_brief_failed",
                )
            raise RuntimeError(f"research_brief did not complete: {err}")

        lit_complete_details = (
            f"Found {results_count} papers with `{literature_query}`."
            if literature_query and literature_query != topic
            else f"Found {results_count} papers."
        )
        await post_step(
            ctx,
            research_session,
            step_id=_STEP_LITERATURE,
            title="Search the literature",
            status="complete",
            details=lit_complete_details,
            step_name="stream_step_lit_search_complete",
        )

        if delivery and brief_markdown:
            await post_thread_message(
                ctx,
                delivery=delivery,
                text=format_research_brief_thread_message(
                    topic=topic,
                    search_query=literature_query,
                    markdown=brief_markdown,
                ),
                step_name="post_slack_research_brief",
                log_event="bfts_research_slack_brief_failed",
            )

        await post_step(
            ctx,
            research_session,
            step_id=_STEP_IDEATION,
            title="Draft a research idea",
            status="in_progress",
            details="Generating a structured hypothesis from the literature…",
            step_name="stream_step_ideation_in_progress",
        )

        ideation_input: dict[str, Any] = {"topic": literature_query}
        if inp.thread_key:
            ideation_input["thread_key"] = inp.thread_key
        if inp.delivery is not None:
            ideation_input["delivery"] = inp.delivery
        for key, val in (
            ("num_seeds", inp.num_seeds),
            ("num_drafts", inp.num_drafts),
            ("num_workers", inp.num_workers),
        ):
            if val is not None:
                ideation_input[key] = val
        if inp.seed_paper_limit is not None:
            ideation_input["seed_paper_limit"] = inp.seed_paper_limit
        if inp.critic_retries:
            ideation_input["critic_retries"] = inp.critic_retries
        if inp.draft_model is not None:
            ideation_input["draft_model"] = inp.draft_model
        if inp.llm_api_key_secret is not None:
            ideation_input["llm_api_key_secret"] = inp.llm_api_key_secret

        ideation_child = await ctx.start_workflow(
            "start_ideation",
            workflow_name="ideation",
            run_input=ideation_input,
            trigger_key=f"{ctx.run_id}:ideation",
            eager_start=True,
        )
        ideation_run_id = str(ideation_child.get("run_id") or "")
        ideation_result = await ctx.wait_for_workflow(
            "wait_ideation",
            run_id=ideation_run_id,
        )
        if workflow_run_failed(ideation_result):
            err = workflow_run_error_text(ideation_result)
            await post_step(
                ctx,
                research_session,
                step_id=_STEP_IDEATION,
                title="Draft a research idea",
                status="error",
                details=err,
                step_name="stream_step_ideation_error",
            )
            await notify_thread_failure(
                ctx,
                delivery=delivery,
                headline="Ideation failed",
                orchestrator_run_id=ctx.run_id,
                error_text=err,
                step_name="post_slack_ideation_child_failed",
                child_run_id=ideation_run_id or None,
                child_workflow="ideation",
            )
            raise RuntimeError(f"ideation child failed: {err}")

        ideation_output = _child_workflow_output(ideation_result)
        idea = ideation_output.get("idea")
        if not isinstance(idea, dict) or not idea.get("Title"):
            err = workflow_run_error_text(ideation_result)
            await post_step(
                ctx,
                research_session,
                step_id=_STEP_IDEATION,
                title="Draft a research idea",
                status="error",
                details="Child returned no valid idea.",
                step_name="stream_step_ideation_invalid",
            )
            await notify_thread_failure(
                ctx,
                delivery=delivery,
                headline="Ideation produced no valid idea",
                orchestrator_run_id=ctx.run_id,
                error_text=err,
                step_name="post_slack_ideation_invalid",
                child_run_id=ideation_run_id or None,
                child_workflow="ideation",
            )
            raise RuntimeError(f"ideation child did not return a valid idea: {err}")

        idea_title = str(idea.get("Title") or idea.get("Name") or "")
        await post_step(
            ctx,
            research_session,
            step_id=_STEP_IDEATION,
            title="Draft a research idea",
            status="complete",
            details=idea_title or "Idea ready.",
            step_name="stream_step_ideation_complete",
        )

        if delivery:
            await post_thread_message(
                ctx,
                delivery=delivery,
                text=format_idea_markdown(idea),
                step_name="post_slack_research_idea",
                log_event="bfts_research_slack_idea_failed",
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

        slack_stream_session_id: str | None = None
        if research_session:
            slack_stream_session_id = research_session.session_id
            bfts_run_input["slack_stream_session_id"] = slack_stream_session_id

        bfts_child = await ctx.start_workflow(
            "start_bfts_root",
            workflow_name="bfts_root",
            run_input=bfts_run_input,
            trigger_key=f"{ctx.run_id}:bfts",
            eager_start=True,
        )
        bfts_run_id = str(bfts_child.get("run_id") or "")

        ctx.log(
            "bfts_research_started",
            ideation_run_id=ideation_run_id,
            bfts_run_id=bfts_run_id,
            slack_stream=bool(slack_stream_session_id),
            num_seeds=bfts_run_input["num_seeds"],
            num_drafts=bfts_run_input["num_drafts"],
            num_workers=bfts_run_input["num_workers"],
        )

        return {
            "topic": topic,
            "literature_query": literature_query,
            "literature_queries_tried": queries_tried,
            "ideation_run_id": ideation_run_id,
            "bfts_run_id": bfts_run_id,
            "idea": idea,
            "brief_document_id": brief_result.get("brief_document_id"),
            "brief_results_count": brief_result.get("results_count"),
            "seed_papers": ideation_output.get("seed_papers"),
            "papers_persisted": ideation_output.get("papers_persisted"),
            "bfts_run_input": bfts_run_input,
            "slack_stream_session_id": slack_stream_session_id,
            "slack_streaming": bool(slack_stream_session_id),
        }
    except Exception as exc:
        from api.workflow_engine import SuspendWorkflow

        if isinstance(exc, (SuspendWorkflow, _ResearchPipelineStop)):
            raise
        await notify_run_failure(
            ctx,
            delivery=delivery,
            stream=research_session,
            orchestrator_run_id=ctx.run_id,
            headline="bfts_research failed",
            error_text=str(exc),
            thread_step_name="post_slack_bfts_research_failed",
        )
        raise


async def handler(inp: Input, ctx: WorkflowContext) -> dict[str, Any]:
    if not inp.topic or not inp.topic.strip():
        raise ValueError("topic cannot be empty")

    return await _run_research_pipeline(inp, ctx)
