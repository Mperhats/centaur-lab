---
name: academic-research
description: "Use when answering questions about academic papers, citations, literature reviews, or research briefs — search Semantic Scholar, build persisted lit-review briefs, surface paper metadata, walk the citation graph. Triggers on peer-reviewed papers, preprints, arXiv, NeurIPS, biology/chemistry journals, ML/AI research topics, \"research brief\", \"lit review\", \"literature review\", or \"what does the literature say about X\". For BFTS / tree-search runs, use the bfts-experiments skill instead."
---

# Academic Research

For **BFTS / `bfts_root` / tree-search experiments**, use
`.agents/skills/bfts-experiments/SKILL.md` — literature tools here do not
replace a populated `idea` or the `ideation` workflow.

Use the `semantic_scholar` tool whenever the user asks about peer-reviewed
papers, preprints, citations, or what a specific researcher has published.
Prefer it over generic web search for anything that lives in the academic
literature: arXiv, NeurIPS, biology/chem journals, etc.

**Default flow: persist with a research brief.** After finding papers via
`search`, call `save_papers` (which always writes a linked
`research_brief` row) or call `semantic_scholar.research_brief` directly
for a single-query lit review. The **`ideation` workflow always persists**
its seed papers via `save_papers` — do not treat that as optional. Skip
persistence on ad-hoc `search` turns ONLY when the user explicitly says
"just search", "don't save", "exploratory only", or otherwise signals they
do not want the result remembered.

**Pick the right surface:**

- "Find papers about X" → `semantic_scholar.search` + `save_papers` follow-up (brief + papers)
- "Summarize this paper" / DOI / arXiv ID / S2 ID → `semantic_scholar.get_paper` + `save_papers` follow-up (brief + paper)
- "What does this paper cite?" → `semantic_scholar.get_references`
- "Build a brief / lit review / writeup on X" → `semantic_scholar.research_brief` (atomic search + render + persist)
- "Research idea for BFTS" / topic before tree search → `ideation` workflow (always persists seed papers; see below)

## `ideation` workflow (BFTS prep)

Use when the user needs a structured research `idea` for `bfts_root`:

```bash
call workflow run '{
  "workflow_name": "ideation",
  "eager_start": true,
  "input": {"topic": "<one-sentence research question>"}
}'
```

On completion, `output_json` includes:

- `idea` — pass straight into `bfts_root` input
- `seed_papers` — S2 `paperId` list from the seed search
- `papers_persisted` — result of an automatic child `save_papers` run (brief + paper rows in `company_context_documents`)

**Do not** call `save_papers` again for the same seed IDs unless the user asks to save additional papers. See `bfts-experiments` for the `bfts_root` kickoff rules.

## Paper Search (`semantic_scholar.search`)

`search` queries the Semantic Scholar Graph API live. It does **not**
read from `company_context_documents` — there is no internal cache lane.

The response shape is `{status, query, limit, year_from, count, results}`.
Each entry in `results` is a standard S2 paper dict (`paperId`, `title`,
`authors`, `year`, `abstract`, `url`, `citationCount`, ...).

`search` does not auto-persist. After a successful search, call
`save_papers` on the `paperId`s you want remembered unless the user opted
out — `save_papers` always upserts a linked `research_brief` row plus
child paper rows. Use `research_brief` when one S2 query should drive the
whole writeup atomically.

`search_papers` is the lower-level sibling — same live API, raises on
failure instead of returning an error envelope. Prefer `search` for
agent turns; use `search_papers` only when you need exceptions or are
calling from code that already handles them.

## Output Expectations

For a Slack reply, return at most 5 papers unless the user asks for more.
For each paper include: title, first author + et al., year, citation count,
and one sentence on the contribution drawn from the abstract. Link to
`url` (or `openAccessPdf.url` when present) so the human can dive in.

### Optional: attach open-access PDFs in Slack

There is **no** one-shot workflow that bundles PDFs after `ideation` or
`research_brief` in this overlay. You can still attach PDFs for papers you
cite using **existing** sandbox tools when the user wants files (not just
links):

1. From `search` / `get_paper` / saved row metadata, read `openAccessPdf`
   (or construct an arXiv PDF URL from `externalIds.ArXiv` when present).
2. Download in the sandbox, e.g. `curl -fsSL -o /home/agent/uploads/paper.pdf '<pdf_url>'`.
3. Upload to the thread: `slack-upload /home/agent/uploads/paper.pdf 'Short comment'`
   or `call slack upload_file` with `content_base64` + `thread_ts` (see
   `call discover slack`).

**Limits:** only papers with a reachable open-access PDF; paywalled PDFs
get a link only. Cap attachments (e.g. top 3–5 papers the user cares about);
each file must stay under Slack/API size limits (~10 MB per attachment path).
`tools/archiver` `download` is for DocSend/Google Drive — **not** arXiv/S2
PDF URLs. Full-text archive + parse indexing is **not** in this repo today
(see `tmp/latest` `archive_papers` on other branches for that direction).

Do NOT fabricate titles, authors, or DOIs. If `search` returns
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
  `company_context_documents` for future retrieval via `company_context` or
  RAG. Use `semantic_scholar.research_brief` when the user asked for a
  brief / lit review / writeup.
- Don't fall back to `call workflow run` for `research_brief` — the
  workflow handler is now a thin back-compat wrapper around the tool method.
  External `/workflows/runs` callers (Justfile cluster smoke, etc.) still
  work, but in-Slack agent turns should always go through the tool.

## Persisting Research

Two on-demand surfaces turn ad-hoc Semantic Scholar lookups into durable
knowledge by upserting rows into `company_context_documents`: the
`save_papers` workflow and the `semantic_scholar.research_brief` tool
method. Both are content-hash idempotent — re-running with the same input
is safe and cheap (returns `noop` actions), so you don't have to track
whether you've already saved something.

### `save_papers` — Remember Specific Papers (+ brief)

The implicit default after every `search` / `search_papers` / `get_paper`
turn (see "Default flow" above). Also fires on explicit asks: "save these
papers", "remember these for later", "add these to context".

```bash
call workflow run '{"workflow_name":"save_papers","input":{"paper_ids":["173ba8ae...","abcd1234..."],"query":"diffusion models"}}'
```

Returns `{status, papers_inserted, papers_updated, papers_noop, papers_failed,
brief_document_id, brief_action, brief_query, results}`. Always writes a
`research_brief` row linking the saved papers. Pass the original user query
in the optional `query` field for traceability; when omitted, a stable
`save_papers:<hash>` query is synthesized from the paper ID set.

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
