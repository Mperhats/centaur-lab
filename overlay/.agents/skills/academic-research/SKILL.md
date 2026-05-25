---
name: academic-research
description: Use when answering questions about academic papers, citations, or research literature — search Semantic Scholar, surface paper metadata, walk the citation graph.
---

# academic-research

Use the `semantic_scholar` tool whenever the user asks about peer-reviewed
papers, preprints, citations, or what a specific researcher has published.
Prefer it over generic web search for anything that lives in the academic
literature: arXiv, NeurIPS, biology/chem journals, etc.

## When to use

- "Find papers about X" → `semantic_scholar.search(query=X, limit=10)` — checks `company_context_documents` first for cached papers, then tops up via the live API for anything published after the cache's cutoff year. Falls through cleanly when the cache is empty. Prefer this over `search_papers` for any query that might overlap previous research.
- "Find papers strictly newer than my cache" / "what's been published this month" → `semantic_scholar.search_papers(query=X, year_from=<recent year>)` directly. The hybrid `search` is cache-aware, so use the raw live call only when you specifically want to ignore the cache.
- "Summarize this paper" given a DOI / arXiv ID / S2 ID → `semantic_scholar.get_paper(paper_id=...)`
- "What does this paper cite?" / building a related-work list → `semantic_scholar.get_references(paper_id=..., limit=20)`

## Cache-aware search (`semantic_scholar.search`)

The hybrid `search` method returns two ranked lanes in one response:

- **`indexed` lane** — papers we already have in `company_context_documents` (saved by prior `save_papers` or `research_brief` runs). BM25-scored against your query, ranked by relevance. Each result has `score`, `preview`, `document_id`, `paperId`, and the full `metadata` row.
- **`live` lane** — fresh results from the Semantic Scholar Graph API, filtered to `year >= max(year_from, indexed_cutoff_year + 1)` so you only see papers genuinely newer than what's cached. Deduped against the indexed lane by `paperId`.

The response shape is `{status, query, limit, year_from, indexed_count, live_count, count, indexed_cutoff_year, live_year_from, live_error, results}`. The `results` array is `[*indexed, *live]` in that order. `live_error` is set (and `live_count: 0`) when the live API call fails — the indexed lane still returns successfully.

If the user is doing follow-up research on a topic and the live lane returned new papers worth keeping, run `save_papers` on those `paperId`s as a follow-up. The hybrid method does not auto-persist — that's deliberate, so cheap exploratory queries don't bloat the document table. Persistence remains opt-in via `save_papers` / `research_brief`.

## Output expectations

For a Slack reply, return at most 5 papers unless the user asks for more.
For each paper include: title, first author + et al., year, citation count,
and one sentence on the contribution drawn from the abstract. Link to
`url` (or `openAccessPdf.url` when present) so the human can dive in.

Do NOT fabricate titles, authors, or DOIs. If `search_papers` returns
nothing, say so and offer alternative queries — don't substitute web
results.

## Anti-patterns

- Don't use `web_search` for academic questions when this tool fits — its
  rankings are tuned for the open web, not the literature.
- Don't loop `get_paper` over hundreds of IDs in one turn; batch via
  `search_papers` first and only fetch full metadata for the few you'll
  cite.
- Don't manually concatenate paper summaries into a Slack reply when the
  user asked for a brief / lit review / writeup — use the `research_brief`
  workflow below so the brief is also persisted in `company_context_documents`
  and BM25-searchable across future turns.
- Don't reach for `search_papers` when `search` will do. The hybrid path is strictly cheaper for any query that overlaps prior research (cached results return without an API call), and it gracefully falls through to live when the cache is empty. Use raw `search_papers` only when you specifically need to bypass the cache.

## Persisting research with workflows

Two on-demand workflows turn ad-hoc Semantic Scholar lookups into durable
knowledge by upserting rows into `company_context_documents`. Both are
content-hash idempotent — re-running with the same input is safe and cheap
(returns `noop` actions), so you don't have to track whether you've already
saved something.

### `save_papers` — remember specific papers

Use after `search_papers` (or whenever the user gives you paper IDs) to
persist papers as standalone context for future turns. Trigger phrases:
"save these papers", "remember these for later", "add these to context".

```
call workflow run '{"workflow_name":"save_papers","input":{"paper_ids":["173ba8ae...","abcd1234..."]}}'
```

Returns `{status, papers_inserted, papers_updated, papers_noop, papers_failed, results}`.
The optional `query` field on the input is recorded as traceability metadata
on each row.

### `research_brief` — synthesized lit review, persisted

Use when the user wants a writeup, not just a list. Trigger phrases:
"build a research brief", "give me a literature summary", "do a lit review
on X", "summary of what's known about X". Prefer this over chaining
`search_papers` + `save_papers` — it does both atomically: searches S2,
renders a structured Markdown brief, upserts the brief as one row, and
upserts each underlying paper as a child row pointing at the brief.

```
call workflow run '{"workflow_name":"research_brief","input":{"query":"active inference world models","limit":5}}'
```

Returns `{status, brief_document_id, brief_action, results_count, papers_inserted, papers_updated, papers_noop, markdown}`.
The `markdown` field is the full rendered brief — post that back to Slack
as your reply. The `brief_document_id` is stable for the same query +
`year_from` (case-insensitive), so re-running updates the same row instead
of accruing duplicates; surface it for traceability so a future turn (or
a RAG retrieval over `company_context_documents`) can pivot back to the
exact brief.
