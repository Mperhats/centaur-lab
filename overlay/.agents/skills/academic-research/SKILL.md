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

- "Find recent papers about X" → `semantic_scholar.search_papers(query=X, year_from=<recent year>)`
- "Summarize this paper" given a DOI / arXiv ID / S2 ID → `semantic_scholar.get_paper(paper_id=...)`
- "What does this paper cite?" / building a related-work list → `semantic_scholar.get_references(paper_id=..., limit=20)`

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
