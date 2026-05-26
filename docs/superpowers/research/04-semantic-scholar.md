---
title: "Research: Semantic Scholar (novelty + citations) for BFTS-on-Centaur"
date: 2026-05-25
status: draft
owner: perhats
related_docs:
  - docs/centaur-science.md
  - docs/superpowers/specs/2026-05-25-centaur-lab-mvp-design.md
---

# Research: Semantic Scholar (novelty + citations) for BFTS-on-Centaur

## TL;DR

- **Out of scope for the first BFTS-on-Centaur shipment** — the spec
  (`docs/centaur-science.md:41`) is correct: novelty + citation tooling
  should not block the controller-as-workflow port. None of the BFTS
  search logic touches Semantic Scholar in the upstream codebase
  (`.scientist/ai_scientist/treesearch/` has zero S2 references).
- **Significant overlay infrastructure already exists** and is well
  past prototype. `overlay/tools/semantic_scholar/client.py` is a
  ~560-line client with bounded retries, iron-proxy-friendly anonymous
  fallback, and a hybrid indexed-first `search` over
  `company_context_documents`; two on-demand workflows
  (`research_brief.py`, `save_papers.py`) drive it.
- **Sakana never wrote a "novelty-check" function.** Novelty is enforced
  indirectly: the ideation reflection loop in
  `perform_ideation_temp_free.py` exposes `SearchSemanticScholar` as one
  of two tool actions and the system prompt requires at least one
  literature search before `FinalizeIdea`
  (`.scientist/ai_scientist/perform_ideation_temp_free.py:96`). There is
  no `--skip-novelty-check` CLI flag; the README just says "if S2 is
  flaky, expect reduced novelty checking" (`.scientist/README.md:83`).
- **Citation tooling is concrete and self-contained.**
  `gather_citations()` in `.scientist/ai_scientist/perform_icbinb_writeup.py:745`
  is a 20-round loop (default `--num_cite_rounds=20`) that calls
  `search_for_papers` and emits a BibTeX `references.bib` block — a
  natural drop-in for a post-experiment step in the BFTS workflow.
- **Cheapest sequencing:** ship BFTS controller per spec (Phase 0),
  then add a *separate, optional* `ideation` workflow that consumes the
  existing client (Phase 1, ~1 workflow file), then add a
  `gather_citations` step at the end of the BFTS workflow (Phase 2, one
  extra `pyproject.toml` field + new workflow step). Phases 1 and 2 are
  independent — either can be cut without disturbing the other.
- **One concrete blocker for Phase 2:** the existing client's
  `DEFAULT_PAPER_FIELDS` does not include `citationStyles`
  (`overlay/tools/semantic_scholar/client.py:33`), which is the field
  Sakana relies on to get pre-formatted BibTeX. Adding `citationStyles`
  to a new `BIBTEX_PAPER_FIELDS` constant is a one-line change.

## Existing overlay state

The overlay directory listed in `git status` is in fact on disk and
substantially fleshed out. All files cited below exist as of this
research.

### Tool: `overlay/tools/semantic_scholar/`

File tree (8 files, no nested subpackages):

```
overlay/tools/semantic_scholar/
├── __init__.py                # docstring only
├── client.py                  # SemanticScholarClient (~560 lines)
├── cli.py                     # Typer CLI for local smoke tests
├── pyproject.toml             # deps + [tool.centaur] declaration
├── .env.example               # SEMANTIC_SCHOLAR_API_KEY=
└── tests/
    ├── __init__.py
    ├── conftest.py
    └── test_search_hybrid.py  # asyncpg + httpx stubs, no I/O
```

**Class:** `SemanticScholarClient` at
`overlay/tools/semantic_scholar/client.py:267`.

**Base URL + auth:**

```270:298:overlay/tools/semantic_scholar/client.py
    BASE_URL = "https://api.semanticscholar.org/graph/v1"

    # Anonymous Semantic Scholar IPs hit 429 quickly. A small bounded backoff
    # smooths over the common case without masking real failures.
    MAX_RETRIES = 4

    def __init__(self, api_key: str | None = None, timeout: float = 30.0):
        self._api_key = api_key if api_key is not None else self._resolve_api_key()
        self._timeout = timeout
        self._client: httpx.Client | None = None

    @staticmethod
    def _resolve_api_key() -> str:
        # The tool works anonymously; default to "" so callers don't have to
        # branch on None. Iron-proxy only injects the real value when the
        # header is actually present, so an empty string keeps requests
        # anonymous instead of breaking them.
        return secret("SEMANTIC_SCHOLAR_API_KEY", "")

    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=self._timeout)
        return self._client

    def _headers(self) -> dict[str, str]:
        if self._api_key:
            return {"x-api-key": self._api_key}
        return {}
```

Key contract notes:

- Secret resolution goes through `centaur_sdk.secret()`
  (`client.py:31`), so it works inside the API pod (iron-proxy
  injection) and falls back to the env var when the CLI runs
  out-of-cluster.
- The `x-api-key` header is sent **only if the key is non-empty** — a
  deliberate defense against iron-proxy reading a stale placeholder
  header and trying to inject (`client.py:295-298`).

**Retry posture** (`client.py:300-330`):

- Bounded exponential backoff (4 attempts, `min(8.0, 2**attempt)` sleep).
- Retries on 429/502/503/504 and `httpx.RequestError`.
- Any other 4xx is raised as `RuntimeError("Semantic Scholar API error
  ({status}): {body}")` *immediately* (no retry).
- The wrapper preserves error info but does not type errors — callers
  see `RuntimeError`. `save_papers.handler` catches this explicitly
  per-paper at `overlay/workflows/save_papers.py:54`.

**Methods exposed to the tool registry:**

| Method | Endpoint | Args | Returns |
|---|---|---|---|
| `search_papers(query, limit=10, year_from=None, fields=…)` | `GET /paper/search` | `client.py:332-361` | `list[dict]` (unwrapped from `data:`) |
| `get_paper(paper_id, fields=…)` | `GET /paper/{id}` | `client.py:363-376` | `dict` (S2 ID, DOI:…, arXiv:… all accepted) |
| `get_references(paper_id, limit=20, fields=…)` | `GET /paper/{id}/references` | `client.py:378-399` | `list[dict]` (flattened from `citedPaper:`) |
| `search(query, limit=10, year_from=None)` | hybrid: ParadeDB BM25 on `company_context_documents` + live top-up | `client.py:401-446` | `dict` (never raises) |

**Default fields** at `client.py:33-34`:

```33:34:overlay/tools/semantic_scholar/client.py
DEFAULT_PAPER_FIELDS = "title,authors,year,abstract,citationCount,url,openAccessPdf"
DEFAULT_REFERENCE_FIELDS = "title,authors,year,citationCount,url"
```

Notably **`citationStyles` is not included** — Sakana's citation loop
depends on this field (see Citation tooling below). A Phase-2 addition
would need to either widen `DEFAULT_PAPER_FIELDS` or add a sibling
constant + helper.

**Tool registration** at
`overlay/tools/semantic_scholar/pyproject.toml:47-55`:

```47:55:overlay/tools/semantic_scholar/pyproject.toml
[tool.centaur]
module = "client.py"
# x-api-key is the documented header for the Graph API. The tool also runs
# anonymously when the key is unset (heavily rate-limited), so the secret is
# declared optional — iron-proxy will only inject when the placeholder is
# actually present in the outbound request.
optional_secrets = [
    {type = "http", name = "SEMANTIC_SCHOLAR_API_KEY", match_headers = ["x-api-key"], hosts = ["api.semanticscholar.org"]},
]
```

This is a more sophisticated registration shape than the sibling tools
inspected (e.g. `.centaur/tools/research/googlenews/pyproject.toml:17-20`
just declares `hosts` + an empty `secrets` list). The
`optional_secrets` schema is forward-looking — it lets iron-proxy
selectively inject the key when the agent calls
`api.semanticscholar.org` without forcing the secret to exist in the
first place.

**Rate limiting:** none in the client. The retry layer is the only
backpressure. For BFTS, this would matter only if the citation phase
ran N parallel rounds; the existing `research_brief.py` is single
caller per workflow run.

### Workflow: `overlay/workflows/research_brief.py`

Handler at `overlay/workflows/research_brief.py:186-291`. Step-by-step:

1. Input validation — empty query or non-positive limit returns
   `{"status": "skipped"}` (`research_brief.py:188-194`).
2. Clamp `inp.limit` to `MAX_LIMIT=20` and log the clamp
   (`research_brief.py:196-202`).
3. Instantiate `SemanticScholarClient()` and call
   `client.search_papers(query, clamped_limit, inp.year_from)`
   (`research_brief.py:204-210`). **Not wrapped in `ctx.step`** — the
   network call replays on workflow resume; this is acceptable for
   on-demand workflows but would break durability if reused as-is
   inside the BFTS controller.
4. Render Markdown via `_render_brief(...)` — pure helper at
   `research_brief.py:105-144`.
5. Build the `company_context_documents` row via
   `_build_brief_document(...)` with a stable
   `document_id = "semantic_scholar:research_brief:{hash16}"` keyed off
   `(query.lower(), year_from)` (`research_brief.py:52-61`, `147-183`).
6. Empty-results branch (`research_brief.py:221-238`): upsert the brief
   row, emit metrics, return early.
7. Happy path: upsert the brief row first
   (`research_brief.py:240-241`), then iterate papers and upsert each
   one via `build_paper_document` + `upsert_document`, stamping
   `parent_document_id` = brief document_id
   (`research_brief.py:243-267`). Per-paper `ValueError` is logged and
   the paper is skipped, not raised.
8. Return shape:

```283:291:overlay/workflows/research_brief.py
    return {
        "status": "completed",
        "brief_document_id": brief_doc["document_id"],
        "brief_action": brief_action,
        "results_count": len(papers),
        "papers_inserted": papers_inserted,
        "papers_updated": papers_updated,
        "papers_noop": papers_noop,
        "markdown": markdown,
    }
```

**No `SCHEDULE` constant** (`research_brief.py:34` declares
`WORKFLOW_NAME = "research_brief"` only) — explicitly on-demand. This
is the right shape for a workflow that another workflow can drive via
`ctx.run_workflow("research_brief", {...})`.

### Workflow: `overlay/workflows/save_papers.py`

Handler at `overlay/workflows/save_papers.py:42-103`. Simpler than
`research_brief`:

1. Empty `paper_ids` → `{"status": "skipped", "reason": "no_paper_ids"}`
   (`save_papers.py:44-46`).
2. For each `paper_id`, call `client.get_paper(paper_id)`. Catches
   `RuntimeError` per paper (the only failure type the client raises),
   logs `save_papers_paper_failed`, records the failure, and continues
   (`save_papers.py:51-68`).
3. Project via `build_paper_document(paper, query=inp.query)` and
   upsert (`save_papers.py:70-72`).
4. Aggregate counts and return
   `{status, papers_inserted, papers_updated, papers_noop, papers_failed,
   results}` (`save_papers.py:96-103`).

**Composability today.** Both workflows are loaded by the API's
`WORKFLOW_DIRS` discovery (see `.centaur/AGENTS.md:332-336`). A
hypothetical BFTS controller workflow could already invoke them via
`ctx.run_workflow("save_papers", {"paper_ids": [...]})` or
`ctx.run_workflow("research_brief", {"query": "...", "limit": 5})` —
no overlay shape changes required. Both already write into
`company_context_documents` (content-hash idempotent per
`_paper_document.upsert_document` at
`overlay/workflows/_paper_document.py:151-227`), so re-running is
cheap.

### Shared helpers

- `overlay/workflows/_paper_document.py:32-148` — `build_paper_document`
  projects an S2 paper dict into a `company_context_documents` row.
  Underscore-prefixed module name means the API workflow loader skips
  it (`_paper_document.py:1-7` comment).
- `overlay/workflows/_metrics.py:41-58` — `emit_document_metrics`
  bridges to `api.vm_metrics` inside the pod with no-op stubs for
  local tests.
- `overlay/.agents/skills/academic-research/SKILL.md` — Slack-facing
  skill that tells the agent persona when to reach for
  `semantic_scholar.search`, `search_papers`, `get_paper`,
  `get_references`, and the two workflows above. Establishes the
  conventional contract that the BFTS port can reuse.

## Sakana's original novelty-check

**There is no standalone novelty-check function in AI Scientist-v2.**
Novelty enforcement is structural, not algorithmic:

1. The ideation phase
   (`.scientist/ai_scientist/perform_ideation_temp_free.py`) sets up an
   LLM with two tool actions: `SearchSemanticScholar` and
   `FinalizeIdea`. The system prompt requires at least one literature
   search before finalizing:

   ```96:96:.scientist/ai_scientist/perform_ideation_temp_free.py
   Note: You should perform at least one literature search before finalizing your idea to ensure it is well-informed by existing research.
   ```

2. The reflection loop runs `num_reflections=5` rounds per idea
   (`perform_ideation_temp_free.py:111-125`). Each round asks the LLM
   to "carefully consider the quality, novelty, and feasibility" and
   "incorporate" any tool results from the previous round.

3. The tool itself is `SemanticScholarSearchTool` at
   `.scientist/ai_scientist/tools/semantic_scholar.py:19-98`. The
   important shape detail is sorting by citation count:

   ```57:85:.scientist/ai_scientist/tools/semantic_scholar.py
   def search_for_papers(self, query: str) -> Optional[List[Dict]]:
       if not query:
           return None
       
       headers = {}
       if self.S2_API_KEY:
           headers["X-API-KEY"] = self.S2_API_KEY
       
       rsp = requests.get(
           "https://api.semanticscholar.org/graph/v1/paper/search",
           headers=headers,
           params={
               "query": query,
               "limit": self.max_results,
               "fields": "title,authors,venue,year,abstract,citationCount",
           },
       )
       print(f"Response Status Code: {rsp.status_code}")
       print(f"Response Content: {rsp.text[:500]}")
       rsp.raise_for_status()
       results = rsp.json()
       total = results.get("total", 0)
       if total == 0:
           return None

       papers = results.get("data", [])
       # Sort papers by citationCount in descending order
       papers.sort(key=lambda x: x.get("citationCount", 0), reverse=True)
       return papers
   ```

4. The formatted result fed back into the LLM is a numbered list of
   `title, authors, venue, year, citationCount, abstract` blocks
   (`semantic_scholar.py:87-98`). The LLM is responsible for deciding
   whether anything in the list invalidates the proposed idea.

**README claims about novelty + skip behavior** at
`.scientist/README.md:83,99,188`:

- "you might encounter rate limits or **reduced novelty checking
  during ideation**" — fail-soft: ideation continues even when S2 is
  unreachable, but the model loses the literature feedback channel.
- "interacting with tools like Semantic Scholar to check for novelty"
  — confirms the loop above is the only novelty mechanism.
- "**you may be able to skip these phases**" — there is no
  `--skip-novelty-check`; you just don't run `perform_ideation_temp_free.py`
  and supply pre-made ideas to `--load_ideas` instead.

**Why this matters for BFTS.** The Sakana entrypoint
(`launch_scientist_bfts.py`) reads pre-generated ideas from
`--load_ideas` (`launch_scientist_bfts.py:51-56,191-195`) and never
touches Semantic Scholar during BFTS itself. The novelty layer is
strictly upstream of the tree search. Porting BFTS to Centaur without
ideation is faithful to Sakana's pipeline; ideation is its own
separately runnable thing.

## Sakana's citation tooling

**Location:** `gather_citations` at
`.scientist/ai_scientist/perform_icbinb_writeup.py:745-854`, plus its
inner driver `get_citation_addition` at
`perform_icbinb_writeup.py:337-530`. The "normal" 8-page writeup uses
the same `search_for_papers` import at
`.scientist/ai_scientist/perform_writeup.py:19,261`.

**Wiring:** `launch_scientist_bfts.py:271-302` calls
`gather_citations(idea_dir, num_cite_rounds=args.num_cite_rounds,
small_model=args.model_citation)` **after** experiments complete and
**before** the writeup model is invoked. The returned BibTeX string is
passed into `perform_writeup` / `perform_icbinb_writeup` as the
`citations_text` argument, which embeds it into the LaTeX
`\begin{filecontents}{references.bib} ... \end{filecontents}` block.

**Per-round contract (`get_citation_addition`):**

1. First prompt asks the LLM to identify the most important missing
   citation given the current report + existing `references.bib`
   (`perform_icbinb_writeup.py:365-401`). The LLM responds with JSON
   `{"Description": "...", "Query": "..."}`, or includes "No more
   citations needed" to terminate.
2. The reply triggers `papers = search_for_papers(query,
   result_limit=5)` (`perform_icbinb_writeup.py:450`).
3. Second prompt feeds the formatted papers list back and asks the LLM
   to pick indices and update the description
   (`perform_icbinb_writeup.py:403-424`). Response:
   `{"Selected": [0, 2], "Description": "..."}` or includes "Do not
   add any" to drop the round.
4. Selected papers' `citationStyles.bibtex` entries are extracted, the
   cite key line is cleaned via `remove_accents_and_clean`
   (`perform_icbinb_writeup.py:33-42,507-515`), and the BibTeX block
   is appended to the cumulative `citations_text`.

**Termination conditions:**

- LLM emits "No more citations needed" → done, write progress,
  break loop.
- `num_cite_rounds` exhausted (default 20).
- Exception in a round → save progress with `status=error` and
  continue to next round.

**Persistence + resume:**

```759:780:.scientist/ai_scientist/perform_icbinb_writeup.py
    # Paths for storing progress
    citations_cache_path = osp.join(base_folder, "cached_citations.bib")
    progress_path = osp.join(base_folder, "citations_progress.json")

    # Initialize or load progress
    current_round = 0
    citations_text = ""

    if osp.exists(citations_cache_path) and osp.exists(progress_path):
        try:
            with open(citations_cache_path, "r") as f:
                citations_text = f.read()
            with open(progress_path, "r") as f:
                progress = json.load(f)
                current_round = progress.get("completed_rounds", 0)
            print(f"Resuming citation gathering from round {current_round}")
        except Exception as e:
            print(f"Error loading cached citations: {e}")
```

Note the file-based checkpointing — Centaur's `ctx.step` would replace
this entirely (each round becomes one durable checkpoint).

**Required field that the overlay client doesn't request today:**
Sakana's `search_for_papers` at
`.scientist/ai_scientist/tools/semantic_scholar.py:104-138` explicitly
asks for `"title,authors,venue,year,abstract,citationStyles,citationCount"`.
The `citationStyles.bibtex` subfield is consumed at
`perform_icbinb_writeup.py:507`:

```507:507:.scientist/ai_scientist/perform_icbinb_writeup.py
            bibtexs = [papers[i]["citationStyles"]["bibtex"] for i in selected_indices]
```

The overlay's `DEFAULT_PAPER_FIELDS`
(`overlay/tools/semantic_scholar/client.py:33`) omits `citationStyles`,
so a citation-gathering port either has to:

- Pass an explicit `fields` argument through to `search_papers`, or
- Add a `BIBTEX_PAPER_FIELDS` constant (or `get_papers_for_citation`
  helper) that includes `citationStyles`.

Either is a one-line change.

## Semantic Scholar API reference (relevant to this port)

Only the **Graph API v1** is in play. We do not need the Datasets API,
Recommendations API, or bulk endpoints.

| Endpoint | What | Used by |
|---|---|---|
| `GET /graph/v1/paper/search` | Free-text query → ranked papers (`data:` envelope, `total` count) | overlay `search_papers`, Sakana `search_for_papers`, Sakana `SemanticScholarSearchTool` |
| `GET /graph/v1/paper/{id}` | Single-paper metadata. `{id}` accepts S2 IDs, `DOI:10.x/y`, `arXiv:1234.5678`, others. | overlay `get_paper`, overlay `save_papers` workflow |
| `GET /graph/v1/paper/{id}/references` | Papers this paper cites; flattens `citedPaper:` envelope | overlay `get_references` |

**Fields parameter.** The Graph API requires explicit `fields=`;
unrequested fields return `null`. For citation gathering, the minimum
useful set is `title,authors,venue,year,abstract,citationStyles,citationCount`
(matches Sakana). For BFTS pre-tree novelty gating, you can drop
`citationStyles` and add `openAccessPdf` if you want PDF retrieval.

**Auth.**

- Header: `x-api-key: <key>` (the overlay's
  `optional_secrets.match_headers = ["x-api-key"]` is correct; Sakana
  uses `X-API-KEY`, but the official docs are case-insensitive). The
  overlay client at `client.py:295-298` is the canonical shape.
- Anonymous: no header. Requests proceed but are heavily throttled.

**Rate limits.** From the public S2 documentation
(`https://api.semanticscholar.org/api-docs`):

- Anonymous: **1 request/second**, shared globally across all
  anonymous IPs — practically you should expect frequent 429s under
  any concurrent load.
- API-key (free tier): documented at ~1 request/second per key, but
  not shared with the global anonymous pool.
- Partner / paid keys: ~10 req/sec; specific values come from S2
  directly and aren't published as a fixed SLA.

The existing client's MAX_RETRIES=4 with `min(8.0, 2**attempt)` (1, 2,
4, 8 seconds) handles a single transient 429 but not a sustained
contention pattern. A BFTS run that fan-outs `num_drafts=3` ideation
calls concurrently against the anonymous endpoint will see 429s; the
key is genuinely required for any production deployment.

## Integration proposal for BFTS-on-Centaur

The spec explicitly puts novelty + citation out of scope. The
question is how to leave a clean affordance for adding them later
without contorting the Phase-0 plan.

### Option A — Out of scope entirely (match the spec)

**What's cut:**

- No ideation workflow. The BFTS controller accepts ideas as input,
  matching `launch_scientist_bfts.py --load_ideas` semantics.
- No citation gathering. The BFTS report (Sakana's
  `generate_report: True` per `bfts_config.yaml:23`) is emitted
  without a `references.bib` block.
- The two existing overlay workflows (`research_brief`, `save_papers`)
  continue to exist as standalone Slack-callable tools; they are not
  wired into the BFTS workflow.

**Trade-off.** Final reports are unreviewable as "papers" — no
citations, no related work grounding. For an internal experiment
search engine, this is fine; for any output meant to be reviewed by
humans as scholarship, citations are table-stakes.

**Risk:** users may attempt to use BFTS for "external" ideas without
running ideation first, with no guardrail against duplicating prior
work. This is the same risk Sakana has when you skip
`perform_ideation_temp_free.py`.

### Option B — Novelty-only as a separately scoped follow-up sub-plan **(recommended)**

Ship Phase 0 per the spec. Phase 1 is a single follow-up sub-plan
that adds:

1. A new overlay workflow `overlay/workflows/ideation.py` modeled on
   Sakana's `generate_temp_free_idea` (`.scientist/ai_scientist/perform_ideation_temp_free.py:128-266`).
   Handler signature:

   ```python
   @dataclass
   class Input:
       workshop_description: str
       max_num_generations: int = 1
       num_reflections: int = 5
       prev_ideas: list[dict] | None = None

   async def handler(inp: Input, ctx: WorkflowContext) -> dict[str, Any]:
       # For each generation:
       #   For each reflection round:
       #     LLM emits ACTION: SearchSemanticScholar or FinalizeIdea
       #     If SearchSemanticScholar: ctx.step(...) → semantic_scholar.search_papers
       #     If FinalizeIdea: persist + break
       # Return {ideas: [...], iterations: ...}
   ```

   Each S2 call lives inside `ctx.step("search_round_N", lambda:
   client.search_papers(...))` so the reflection loop is durable.

2. The BFTS workflow takes `idea: dict` as input. If callers want
   ideation upstream, they `ctx.run_workflow("ideation", {...})` and
   pipe an output idea into `ctx.run_workflow("bfts_controller",
   {"idea": ideation_result["ideas"][0]})`. **No changes to the BFTS
   workflow itself.**

3. No new tool work — the existing `SemanticScholarClient.search_papers`
   is exactly what the LLM calls. The persona/skill prompt is one new
   `overlay/.agents/skills/idea-novelty/SKILL.md` file.

**Hook point in BFTS:** none. Novelty is *upstream* of BFTS as a
separate workflow. This matches Sakana's actual topology (ideation
runs first; `launch_scientist_bfts.py` consumes the JSON it produces).

**Trade-off.** No citation in the final report. If you need citations
later, Phase 2 is independent.

**Why recommended:** lowest cost, matches the upstream code's actual
factoring, doesn't require modifying the BFTS controller. The Phase-1
sub-plan is essentially "port `perform_ideation_temp_free.py` as one
Centaur workflow." That's bounded and reviewable in isolation.

### Option C — Novelty + citation, fully integrated into the BFTS workflow

Ship Phase 0 per the spec. Phase 2 adds, on top of Phase 1:

1. Extend `overlay/tools/semantic_scholar/client.py` with a
   `BIBTEX_PAPER_FIELDS = DEFAULT_PAPER_FIELDS + ",citationStyles"`
   constant and a `search_papers_for_citation(query, ...)` helper, OR
   change `search_papers` to accept the fields argument as-is (it
   already does — callers just need to pass the wider set).
2. Add a new step at the **end** of the BFTS workflow, after the
   report-generation step. Concretely, after the equivalent of
   Sakana's `generate_report: True` step
   (`.scientist/bfts_config.yaml:23-28`):

   ```python
   citations = await ctx.run_workflow(
       "gather_citations",
       {"report_markdown": report, "idea": inp.idea, "num_rounds": 20},
   )
   final_report = embed_bibliography(report, citations["bibtex"])
   ```

3. The new `overlay/workflows/gather_citations.py` is a port of
   Sakana's `gather_citations`:

   - One `ctx.step("citation_round_{N}", ...)` per round, mirroring
     the per-round LLM-call + S2-call protocol.
   - File-based checkpointing replaced by `ctx.step` checkpoints.
   - Terminate on "No more citations needed" or
     `num_rounds` exhausted, matching
     `.scientist/ai_scientist/perform_icbinb_writeup.py:805-814`.
   - Output: `{"bibtex": "<concatenated entries>", "rounds_used": N}`.

**Hook point in BFTS:** exactly one — a tail step after report
generation, conditional on a `gather_citations: bool = False` field
on the BFTS input. No middle-of-loop changes; the controller stays
unchanged.

**Trade-off.** Adds ~300 lines of workflow code and the LLM cost of
20 citation rounds per run (in Sakana that's ~$5 with `gpt-4o`). For
internal-only outputs that's overkill; for outputs meant to be shown
to other researchers it's required.

### Recommendation

**Option B for the master plan.** Phase-2 (Option C) is documented
here but not committed; the plan author can promote it to a sibling
sub-plan once Phase 0 + Phase 1 have shipped and there's evidence
people actually want citations in BFTS reports.

Concrete hook points to record in the master plan:

- BFTS workflow's `Input` dataclass takes `idea: dict` (the Sakana
  shape: `Name`, `Title`, `Short Hypothesis`, `Related Work`,
  `Abstract`, `Experiments`, `Risk Factors and Limitations`). It does
  not take a `query` — the ideation workflow produces the idea.
- The BFTS workflow does **not** call `semantic_scholar` directly.
- A separate `ideation` workflow (Phase 1) is the only piece that
  calls `SemanticScholarClient.search_papers`. It can run
  standalone (`POST /workflows/runs` with `workflow_name=ideation`) or
  be invoked from a higher-level driver workflow that fans out to BFTS
  for each surviving idea.
- A future `gather_citations` workflow (Phase 2) calls
  `SemanticScholarClient.search_papers` with explicit
  `fields=…,citationStyles` and is invoked from the BFTS workflow as
  the final step. It is a single `ctx.run_workflow` call — easy to
  feature-flag.

## Gotchas

- **Anonymous rate limit is global, not per-IP.** Concurrent
  `num_drafts=3` runs against the anonymous endpoint will trip 429s
  even with the existing retry layer. An API key
  (`SEMANTIC_SCHOLAR_API_KEY` secret already declared in the overlay's
  `pyproject.toml:53-54`) is effectively required for production.
- **Iron-proxy injection is declared but not verified end-to-end.**
  The `[tool.centaur].optional_secrets` block is the right shape, but
  there is no integration test that exercises a real
  `api.semanticscholar.org` call through the proxy. The current tests
  (`overlay/tools/semantic_scholar/tests/test_search_hybrid.py`) stub
  both asyncpg and httpx. A first-class smoke test would `kubectl exec`
  into the API pod and call `/tools/semantic_scholar/search_papers`
  with a known query — the existing CLI at
  `overlay/tools/semantic_scholar/cli.py` is the local equivalent.
- **The hybrid `search` method depends on the
  `company_context_documents` table.** If the BFTS overlay deploys
  into a stack without that table (a stripped-down lab deployment),
  the hybrid path returns `{"status": "error", "error":
  "DATABASE_URL is required..."}`. The raw `search_papers` /
  `get_paper` / `get_references` are unaffected and are the right
  primitives for the BFTS port.
- **Empty results are not failures.** `search_papers` returns `[]`,
  the hybrid `search` returns `{"status":"ok", "results":[]}`. Sakana's
  `search_for_papers` returns `None` when `total == 0`
  (`.scientist/ai_scientist/tools/semantic_scholar.py:79`). A
  Centaur-side ideation port should treat the empty case as "no
  literature found, proceed with the LLM's own knowledge" rather than
  raising — Sakana's prompt handles this gracefully because it logs
  `last_tool_results = "No papers found."` and moves on
  (`perform_ideation_temp_free.py:222`).
- **False-negative novelty signals.** S2 lookup ranking is plain
  text-similarity; a paper using completely different terminology
  for the same idea will not surface. Sakana mitigates this by giving
  the LLM 5 reflection rounds and counting on it to issue varied
  queries. Any BFTS port should preserve that loop structure rather
  than turning novelty into a single yes/no gate.
- **Citation hallucination.** Sakana's per-round protocol forces the
  LLM to pick indices from the *immediately-returned* search results
  (`perform_icbinb_writeup.py:498-506`), with an `assert all([0 <= i <
  len(papers) for i in selected_indices])` guard. A port should
  preserve this: never accept a freeform paper claim from the LLM,
  only references that S2 just returned to it.
- **`citationStyles.bibtex` is not in the overlay's default fields.**
  Phase 2 must add it. See client.py:33 above.
- **No `ctx.step` boundary in the existing workflows.** Both
  `research_brief.py` and `save_papers.py` call the client
  unconditionally in the handler body. This is fine for on-demand
  one-shot workflows (resume = redo the network call), but if BFTS
  starts depending on these via `ctx.run_workflow`, intermediate
  failure modes (the S2 API succeeds, the upsert fails) will replay
  the network call on retry. Acceptable for idempotent reads;
  worth a note in the Phase-1 ideation workflow design.
- **`get_paper` accepts arbitrary IDs** (S2, DOI:…, arXiv:…) per
  `client.py:370-372`. The BFTS port might want to whitelist IDs to
  the S2 prefix only if it's surfacing IDs to a user — DOIs from
  arbitrary sources can be used to probe S2 for arbitrary papers,
  which is harmless but worth flagging if the report ever surfaces
  user-supplied identifiers.

## Open questions for the master plan

1. **Does Phase 0 ship without ideation entirely?** Sakana's BFTS
   consumes a JSON ideas file (`--load_ideas`); the port can do the
   same and accept `idea: dict` as workflow input. Confirm this is
   the intended shape before drafting Phase-0 tasks.
2. **Where do ideas come from in Phase 0?** Options:
   (a) hand-authored JSON committed to the repo,
   (b) a one-shot LLM call inside the BFTS workflow's first step
       (no S2),
   (c) deferred — Phase 0 only accepts `idea` as input.
   Recommend (c); (a) and (b) can be added incrementally.
3. **Is the final BFTS report a "paper" or a "report"?** Sakana
   distinguishes `--writeup-type normal` (8-page paper with full
   citations and LaTeX compilation) vs `icbinb` (4-page workshop
   paper). Both call `gather_citations` regardless. The Centaur port
   probably wants a third option: structured Markdown report with no
   LaTeX, no PDF, no citations. Confirm before sizing Phase 2.
4. **Should Phase 1 ideation also use the hybrid `search` (indexed
   cache + live)?** The cache lane will be empty in fresh deployments
   and the indexed-cutoff-year logic
   (`overlay/tools/semantic_scholar/client.py:508-512`) is correct
   only when `company_context_documents` has prior S2 rows. Safest
   default: ideation calls raw `search_papers`. Switch to hybrid in a
   later iteration once enough prior runs exist.
5. **API key sourcing.** The `optional_secrets` declaration lets the
   tool run anonymously, but for production the key must be
   provisioned. Decide whether to require `SEMANTIC_SCHOLAR_API_KEY`
   in the centaur-lab `.env.example` (a real key, requires sign-up at
   semanticscholar.org/product/api) or leave it optional with an
   explicit "expect rate-limit failures without this" note.
6. **`citationStyles` field addition.** Add to
   `DEFAULT_PAPER_FIELDS` (slightly larger payloads on every search,
   even when not used for citations) or add a sibling
   `BIBTEX_PAPER_FIELDS` constant + helper (cleaner separation but
   one more method). The latter is more in keeping with the
   tool's narrow-surface convention.

## Sources

- `docs/centaur-science.md:39-41` — spec's explicit non-goal.
- `overlay/tools/semantic_scholar/client.py:33-34,267-330,332-399,401-446`
- `overlay/tools/semantic_scholar/pyproject.toml:47-55`
- `overlay/tools/semantic_scholar/cli.py:1-145`
- `overlay/workflows/research_brief.py:34-291`
- `overlay/workflows/save_papers.py:31-103`
- `overlay/workflows/_paper_document.py:32-227`
- `overlay/.agents/skills/academic-research/SKILL.md`
- `.scientist/launch_scientist_bfts.py:51-56,191-195,271-302`
- `.scientist/ai_scientist/perform_ideation_temp_free.py:17,21,96,113,128-266`
- `.scientist/ai_scientist/tools/semantic_scholar.py:19-138`
- `.scientist/ai_scientist/perform_icbinb_writeup.py:22,337-530,745-854`
- `.scientist/ai_scientist/perform_writeup.py:19,261`
- `.scientist/README.md:83,99,188`
- `.scientist/bfts_config.yaml:23-28,73-76`
- `.centaur/AGENTS.md:303-335,352-393` — tool/workflow conventions.
- `.centaur/tools/research/googlenews/pyproject.toml:17-20` — sibling
  tool registration shape for comparison.
- `.centaur/centaur_sdk/tool_sdk.py:47` — `secret()` API contract.
