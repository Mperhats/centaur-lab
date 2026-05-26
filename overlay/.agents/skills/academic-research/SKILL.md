---
name: academic-research
description: "Use when answering questions about academic papers, citations, literature reviews, or research briefs — search Semantic Scholar, build persisted lit-review briefs, surface paper metadata, walk the citation graph. Triggers on peer-reviewed papers, preprints, arXiv, NeurIPS, biology/chemistry journals, ML/AI research topics, \"research brief\", \"lit review\", \"literature review\", or \"what does the literature say about X\"."
---

# Academic Research

Use the `semantic_scholar` tool whenever the user asks about peer-reviewed
papers, preprints, citations, or what a specific researcher has published.
Prefer it over generic web search for anything that lives in the academic
literature: arXiv, NeurIPS, biology/chem journals, etc.

**Default flow: search-then-save.** Persistence is on by default — every
academic-research turn grows the cache so future turns find the same papers
in the indexed lane without an API call. Skip the `save_papers` follow-up
ONLY when the user explicitly says "just search", "don't save",
"exploratory only", or otherwise signals they don't want the result
remembered.

## Cache-Aware Search (`semantic_scholar.search`)

The hybrid `search` method returns two ranked lanes in one response:

- **`indexed` lane** — papers we already have in `company_context_documents` (saved by prior `save_papers` or `research_brief` runs). BM25-scored against your query, ranked by relevance. Each result has `score`, `preview`, `document_id`, `paperId`, and the full `metadata` row.
- **`live` lane** — fresh results from the Semantic Scholar Graph API, filtered to `year >= max(year_from, indexed_cutoff_year + 1)` so you only see papers genuinely newer than what's cached. Deduped against the indexed lane by `paperId`.

The response shape is `{status, query, limit, year_from, indexed_count, live_count, count, indexed_cutoff_year, live_year_from, live_error, results}`. The `results` array is `[*indexed, *live]` in that order. `live_error` is set (and `live_count: 0`) when the live API call fails — the indexed lane still returns successfully.

Hybrid `search` does not auto-persist — that's intentional so the agent has explicit control of when to grow the cache. **`research_brief` does write** (brief + child papers) — that's its whole point. So the tool/workflow split is no longer "tools find, workflows write"; it's "use whichever surface fits the affordance." When the user asks for a list, `search` + `save_papers`. When they ask for a brief, `research_brief`. The expected agent flow after `search` is still to call `save_papers` on the live-lane `paperId`s so the cache grows over time; saving is the default unless the user opted out. The indexed lane never needs a `save_papers` follow-up — those rows are already in `company_context_documents`.

## Output Expectations

For a Slack reply, return at most 5 papers unless the user asks for more.
For each paper include: title, first author + et al., year, citation count,
and one sentence on the contribution drawn from the abstract. Link to
`url` (or `openAccessPdf.url` when present) so the human can dive in.

Do NOT fabricate titles, authors, or DOIs. If `search_papers` returns
nothing, say so and offer alternative queries — don't substitute web
results.

## What Not To Do

- Don't use `web_search` for academic questions when this tool fits — its
  rankings are tuned for the open web, not the literature.
- Don't loop `get_paper` over hundreds of IDs in one turn; batch via
  `search_papers` first and only fetch full metadata for the few you'll
  cite.
- Don't manually concatenate paper summaries into a Slack reply when the
  user asked for a brief / lit review / writeup — use
  `semantic_scholar.research_brief` so the brief is also persisted in
  `company_context_documents` and BM25-searchable across future turns.
- Don't fall back to `call workflow run` for `research_brief` — the
  workflow handler is now a thin back-compat wrapper around the tool method.
  External `/workflows/runs` callers (Justfile cluster smoke, etc.) still
  work, but in-Slack agent turns should always go through the tool.
- Don't reach for `search_papers` when `search` will do. The hybrid path is strictly cheaper for any query that overlaps prior research (cached results return without an API call), and it gracefully falls through to live when the cache is empty. Use raw `search_papers` only when you specifically need to bypass the cache.

## Persisting Research with Workflows

Two on-demand surfaces turn ad-hoc Semantic Scholar lookups into durable
knowledge by upserting rows into `company_context_documents`: the
`save_papers` workflow and the `semantic_scholar.research_brief` tool
method. Both are content-hash idempotent — re-running with the same input
is safe and cheap (returns `noop` actions), so you don't have to track
whether you've already saved something.

### `save_papers` — Remember Specific Papers

The implicit default after every `search` / `search_papers` / `get_paper`
turn (see "Default flow: search-then-save" above). Also fires on explicit
asks: "save these papers", "remember these for later", "add these to context".

```bash
call workflow run '{"workflow_name":"save_papers","input":{"paper_ids":["173ba8ae...","abcd1234..."],"query":"diffusion models"}}'
```

Returns `{status, papers_inserted, papers_updated, papers_noop, papers_failed, results}`.
Pass the original user query in the optional `query` field — it's recorded
as traceability metadata on each row, so future BM25 retrievals can still
surface the matched-against question, not just the paper text.

Idempotency means the save call is cheap to make even when the agent
isn't sure whether the papers are already cached: re-saving an unchanged
paper returns `noop` and writes nothing.

### `research_brief` — Synthesized Lit Review, Persisted

Use when the user wants a writeup, not just a list. Trigger phrases:
"build a research brief", "give me a literature summary", "do a lit review
on X", "summary of what's known about X", "what does the literature say
about Y". Prefer this over chaining `search_papers` + `save_papers` — it
does both atomically: searches S2, renders a structured Markdown brief,
upserts the brief as one row, and upserts each underlying paper as a child
row pointing at the brief.

The tool method is what `call discover semantic_scholar` surfaces; invoke
it directly via the tool surface (no `call workflow run` needed):

`semantic_scholar.research_brief(query="active inference world models", limit=5)`

Returns `{status, brief_document_id, brief_action, results_count,
papers_inserted, papers_updated, papers_noop, markdown}`. The `markdown`
field is the full rendered brief — post that back to Slack as your reply.
The `brief_document_id` is stable for the same query + `year_from`
(case-insensitive), so re-running updates the same row instead of accruing
duplicates; surface it for traceability so a future turn (or a RAG retrieval
over `company_context_documents`) can pivot back to the exact brief.
