# centaur-lab Overlay

You are running with the centaur-lab overlay mounted. This deployment is
specialized for **academic research** — finding, summarizing, and persisting
peer-reviewed papers and preprints into durable, BM25-searchable
`company_context_documents` rows.

## Overlay surface

When the user asks about academic papers, citations, lit reviews, or
research briefs, prefer the overlay's S2 + workflow surface; otherwise
the upstream `archiver` and `websearch` tools handle generic document
ingestion and web research:

- `tools/semantic_scholar` — live Semantic Scholar Graph API client.
  Methods: `search`, `search_papers`, `get_paper`, `get_references`,
  `research_brief` (returns a bundle; does **not** persist).
- `workflows/save_papers.py` — persist S2 paper IDs as
  `source_type="paper"` rows; always writes a linked `research_brief`
  parent row.
- `workflows/research_brief.py` — search S2, render a Markdown lit-review
  brief, persist the brief plus each underlying paper as parent/child
  rows.
- `.agents/skills/academic-research/SKILL.md` — the canonical playbook
  for routing user requests across the tools and workflows above.
- `tools/archiver` — upstream tool (provided by the base API image, not
  this overlay). Download + parse arbitrary documents (web pages, PDFs,
  DocSend decks) into structured extractions via Reducto. Public methods:
  `download`, `parse`, `extract_manifest`, `extract_files`,
  `extract_source`. Use this for full-text ingestion of papers that
  `semantic_scholar` exposes only as metadata.
- `tools/websearch` — upstream tool (provided by the base API image, not
  this overlay). Exa-backed web search with optional Claude-cited
  synthesis, plus iterative `deep_research`. Use this for non-academic
  web queries or when `semantic_scholar` returns nothing for a topic.

## Operating rules

- Persistence happens through workflows, never directly through tool
  methods. The `semantic_scholar.research_brief` tool method returns a
  projection bundle; the workflow of the same name consumes it and
  upserts.
- Don't fabricate titles, authors, DOIs, or citation counts. If
  `search` returns nothing, say so — do not substitute web results.
- Default to persisting with a brief after every `search` /
  `search_papers` / `get_paper` turn unless the user explicitly says
  "don't save", "just search", or "exploratory only".
- For Slack replies, return at most 5 papers unless the user asks for
  more. Each entry: title, first author + et al., year, citation count,
  one-sentence contribution, link.

When a request is outside the academic-research domain (e.g. infra,
debugging, codebase questions), fall through to the base Centaur
guidance — the overlay does not displace the base agent's behavior.

## BFTS tree search (`bfts_root`)

Read `.agents/skills/bfts-experiments/SKILL.md` when the user mentions BFTS,
`num_seeds`, seed children, or tree search.

**Idea required:** run `ideation` (or get a full `idea` from the user) before
`bfts_root`. `ideation` **automatically** runs child `save_papers` on its seed
literature (`papers_persisted` in output) — do not treat that save as optional.
Prefer **`bfts_research`** for Slack science runs. Start it with thread context:

```bash
call bfts_runner start_research '{"topic": "<research question>"}'
```

Do **not** use bare `call workflow run` for `bfts_research` in Slack — the run
will miss `thread_key` and streaming will not appear in this thread. Reply
**once** with the returned `run_id`; do not duplicate the same kickoff paragraph.
The workflow posts the literature brief and research idea as plain thread
messages; only BFTS tree progress uses a separate streamed message.

After `ideation`, use `output_json.bfts_run_input` for manual `bfts_root` — do not omit
hyperparams. Do not start BFTS with only `num_seeds` / `num_drafts` — empty `idea` uses the
toy smoke fixture and cannot produce seed aggregates or `best_node_id`. Slack
runs without an `idea` are rejected by `bfts_root` when `thread_key` /
`delivery` is set.

Long-running experiments use the `bfts_root` workflow (hours, not
minutes). From Slack, start fire-and-forget and let workflows stream progress
— do not block the agent turn polling `workflow get`.

Manual `bfts_root` with a known idea (include `thread_key` + `delivery`):

```bash
call workflow run '{
  "workflow_name": "bfts_root",
  "eager_start": true,
  "input": {
    "idea": { "Name": "...", "Title": "...", "Short Hypothesis": "...", "Experiments": ["..."] },
    "thread_key": "'"$CENTAUR_THREAD_KEY"'",
    "delivery": {
      "platform": "slack",
      "channel": "<channel_id>",
      "thread_ts": "<thread parent ts>",
      "recipient_user_id": "<Slack user id to @-mention>"
    }
  }
}'
```

Omit `delivery` for operator-only runs (e.g. `just bfts-toy-run`).
