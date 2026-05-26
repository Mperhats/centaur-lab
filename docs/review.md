# Overlay vs. Upstream Architecture Audit

**Branch:** `feat/research-persistence-and-briefs` · **Upstream HEAD:** `6a96324c` · **Mode:** read-only

**Methodology:** Four parallel `explore` subagents audited the tools, workflows, tests, and skills/configs surfaces against analogous upstream patterns. 48 candidate mismatches surfaced; cross-cutting themes called out in the summary.

---

## Meta-finding: the prior `Fake → Mock` rename was backwards

Worth flagging up front because it touches multiple files and was an explicit prior-audit decision: **every test double in `.centaur/services/api/tests/`** (verified across `test_company_context_documents.py:12`, `test_slack_sync.py:14`, `test_github_issue_triage_workflow.py:10`, `test_url_interceptor.py:91`, `test_retention.py:11`) **uses the `Fake*` naming convention**. There are zero `Mock*`-named classes anywhere in upstream tests. The prior overlay rename inverted the project-wide convention. See finding **A1** below; this is the highest-leverage decision the human should resolve before any other test-area work.

---

# Critical (2)

### [Critical] [Tools] — API key resolved at module load time; `x-api-key` header is never sent in production

**Upstream pattern** (`.centaur/tools/research/crunchbase/client.py:55-59`):
> ```python
> def _get_api_key(self) -> str | None:
>     if self._api_key:
>         return self._api_key
>     return secret("CRUNCHBASE_API_KEY", "")  # called lazily at request time
> ```

Confirmed by `.centaur/services/api/api/tool_manager.py:1664,1710-1716`: the `_client()` factory is invoked inside `_collect_methods()` with `ToolContext(secrets={})` — an empty context. Required secrets land in `ToolContext.secrets` only at per-call time (lines 2037-2070), not at load time.

**Overlay current state** (`overlay/tools/semantic_scholar/client.py:281-298`):
> ```python
> @staticmethod
> def _resolve_api_key() -> str:
>     return secret("SEMANTIC_SCHOLAR_API_KEY", "")  # runs at __init__ time
>
> def __init__(self, api_key=None, timeout=30.0):
>     self._api_key = api_key if api_key is not None else self._resolve_api_key()
>
> def _headers(self) -> dict[str, str]:
>     if self._api_key:  # self._api_key == "" at load time — always falsy
>         return {"x-api-key": self._api_key}
>     return {}
> ```

**Gap**: `_resolve_api_key()` runs during `__init__` at module-load time. At that moment `ToolContext.secrets` is `{}`, so `secret(...)` falls through to env and returns `""`. `self._api_key` is permanently `""`, so `_headers()` never writes the header, iron-proxy never replaces it, and the tool always runs anonymously — even when `SEMANTIC_SCHOLAR_API_KEY` is configured. The `optional_secrets` declaration in `pyproject.toml` is effectively dead.

**Recommendation**: Mirror crunchbase's lazy-fallback pattern:
```python
def _headers(self) -> dict[str, str]:
    api_key = self._api_key or secret("SEMANTIC_SCHOLAR_API_KEY", "")
    return {"x-api-key": api_key} if api_key else {}
```
Simplify `_client()` to `return SemanticScholarClient()` without the eager `api_key=secret(...)` argument.

---

### [Critical] [Tests] — `_fakes.py` is a dead, unreferenced duplicate of `_mocks.py`

**Upstream pattern**: no equivalent; upstream Fake* helpers are inline or per-file.

**Overlay current state** (`overlay/workflows/tests/_fakes.py:1-139`): the entire file is a line-for-line duplicate of `_mocks.py` under the old `Fake*` names.

**Gap**: `_fakes.py` is never imported anywhere in the overlay (zero matches for `from ._fakes`, `import _fakes`, `from _fakes`). The prior rename created `_mocks.py` but left the original in place. Any future editor will be confused which module is authoritative.

**Recommendation**: Delete `overlay/workflows/tests/_fakes.py`. (If you instead resolve **A1** by reverting to `Fake*`, delete `_mocks.py` and restore the names in `_fakes.py` — same net work, opposite direction.)

---

# Architectural (16)

### [A1] [Tests] — Overlay uses `Mock*` naming; upstream uses `Fake*` universally

**Upstream pattern** (across `test_company_context_documents.py:12`, `test_slack_sync.py:14`, `test_github_issue_triage_workflow.py:10`, `test_url_interceptor.py:91`, `test_retention.py:11`):
> `class FakeCtx`, `class FakeSlackClient`, `class FakeContext`, `class FakePool`

**Overlay current state** (`overlay/workflows/tests/_mocks.py:35,65`):
> `class MockPool`, `class MockContext`, `class MockSemanticScholarClient`

**Gap**: Every test double upstream uses `Fake*`. The prior overlay rename inverted this. When an upstream workflow test is copied or ported to the overlay (or vice versa), every class name must be touched.

**Recommendation**: Rename `MockPool → FakePool`, `MockContext → FakeContext`, `MockSemanticScholarClient → FakeSemanticScholarClient`. Rename `_mocks.py → _fakes.py` (after deleting the old). Update imports in `test_paper_document.py`, `test_research_brief.py`, `test_save_papers.py`, and both integration test files.

---

### [A2] [Workflows] — `_canonical_json` diverges from `api.runtime_control.canonical_json` (silent hash divergence on non-ASCII)

**Upstream pattern** (`.centaur/services/api/api/runtime_control.py:130`):
> `json.dumps(value, separators=(",", ":"), sort_keys=True, ensure_ascii=False)`

**Overlay current state** (`overlay/workflows/_paper_document.py:24`):
> `json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)`

**Gap**: Two behavioral differences. (1) Upstream uses `ensure_ascii=False`; non-ASCII Unicode (Japanese/CJK/Arabic titles, author names) is emitted literally, producing different byte sequences than the overlay's ASCII-escaped output. Content hashes for non-ASCII papers will differ from what upstream would compute for the same value, breaking cross-system hash identity. (2) Upstream has no `default=str` — non-serializable Python values raise `TypeError`, surfacing bugs. Overlay silently coerces.

**Recommendation**: Replace local `_canonical_json` with a direct import of `api.runtime_control.canonical_json` guarded by try/except (mirroring the existing `_metrics.py` shim pattern).

---

### [A3] [Workflows] — No `ctx.step()` wrapping; handlers are not checkpoint-durable

**Upstream pattern** (`.centaur/workflows/muesli_meeting_ingest.py:123`):
> `persisted = await ctx.step("persist_meeting", lambda: _persist(ctx, inp))`

**Overlay current state** (`overlay/workflows/save_papers.py:71`, `research_brief.py:227`):
> `action = await upsert_document(ctx._pool, document)`  *(bare, inside handler body)*

**Gap**: A process crash after partially processing 5 of 10 papers re-runs the entire handler: re-calls `client.search_papers()`, re-fetches all metadata, re-upserts all rows. Upsert idempotency keeps DB state correct, but Semantic Scholar API budget is wasted and partial-failure counts won't accumulate across crash-resume cycles. For `research_brief`, `search_papers()` and the brief upsert are natural step boundaries.

**Recommendation**: At minimum, wrap `client.search_papers()` and the brief upsert in `research_brief.py` as `ctx.step("search_papers", ...)` and `ctx.step("upsert_brief", ...)`. For `save_papers.py`, document the deliberate decision if loop-body steps are too invasive.

---

### [A4] [Workflows] — Compound `content_hash` divergence from upstream upsert convention (intentional?)

**Upstream pattern** (`.centaur/workflows/company_context_documents.py:448`): stores `content_hash = document["content_hash"]` (raw intrinsic hash).

**Overlay current state** (`overlay/workflows/_paper_document.py:173`):
> `effective_hash = _content_hash(document["content_hash"], effective_parent)` *(hash-of-hash persisted)*

**Gap**: Overlay stores a derived compound hash to make re-parenting trigger UPDATE even when content is unchanged. Documented in docstring. Risk: any upstream code that reads `company_context_documents.content_hash` for a `semantic_scholar` row and compares against a freshly computed intrinsic hash will always see mismatch. If upstream ever ships cross-source dedup or audit tooling that trusts `content_hash` to equal intrinsic, overlay rows fail silently.

**Recommendation**: Confirm as intentional. Add a `# OVERLAY: compound hash` comment at the relevant line so future readers don't "fix" it. Track a migration story if upstream standardizes.

---

### [A5] [Tools] — `time.sleep` blocks the asyncio event loop during retry backoff in `_search_async`

**Upstream pattern** (`.centaur/tools/research/websearch/client.py:448-449`):
> `await asyncio.sleep(self._backoff_seconds(attempt))`

Websearch separates sync (`_exa_search_sync`) and async (`_exa_search_async`) retry helpers using `time.sleep` and `await asyncio.sleep` respectively.

**Overlay current state** (`overlay/tools/semantic_scholar/client.py:313-315`):
> ```python
> if attempt < self.MAX_RETRIES - 1:
>     time.sleep(min(8.0, 2**attempt))   # blocking sleep inside _search_async coroutine
>     continue
> ```

**Gap**: `_search_async` is async; it calls sync `search_papers()` → `_request()` which uses `time.sleep`. Blocks the event loop thread for up to 8s per retry. Tool manager runs sync methods via `asyncio.to_thread` so impact is contained today, but the code smell will cause starvation if anything is ever refactored to share a real async context.

**Recommendation**: Introduce `async def _request_async(...)` using `await asyncio.sleep(...)`, called from `_search_async`. Keep sync `_request` for the synchronous methods. Mirror websearch's two-variant pattern.

---

### [A6] [Tools] — `DATABASE_URL` resolved per-call via module function; no `_connect()` / `_require_database_url()` helpers

**Upstream pattern** (`.centaur/tools/productivity/company_context/client.py:273-286`):
> ```python
> def __init__(self, database_url: str | None = None) -> None:
>     env_database_url = os.getenv("DATABASE_URL")  # noqa: TID251
>     self._database_url = (database_url or env_database_url or secret("DATABASE_URL", default="")).strip()
>
> def _require_database_url(self) -> str:
>     if not self._database_url:
>         raise RuntimeError("DATABASE_URL is required for company context search")
>     return self._database_url
>
> async def _connect(self) -> asyncpg.Connection:
>     return await asyncpg.connect(self._require_database_url(), command_timeout=30)
> ```

**Overlay current state** (`overlay/tools/semantic_scholar/client.py:217-222, 455`):
> ```python
> def _resolve_database_url() -> str:   # module-level function
>     env_database_url = os.getenv("DATABASE_URL")  # noqa: TID251
>     return (env_database_url or secret("DATABASE_URL", default="")).strip()
> ...
> conn = await asyncpg.connect(database_url, command_timeout=30)  # inline in _search_async
> ```

**Gap**: (1) URL resolved every `search()` call instead of once in `__init__`; (2) no `_connect()` helper, so any future DB method duplicates connect logic; (3) no constructor injection, blocking the test-time direct-URL injection upstream exploits.

**Recommendation**: Move resolution into `__init__` (`self._database_url`), add `_require_database_url()` and `_connect()` methods matching upstream.

---

### [A7] [Tools] — `client.py` module docstring is 16 lines; all upstream tool docstrings are one sentence

**Upstream pattern** (verified across 5 upstream clients):
- `.centaur/tools/research/websearch/client.py:1`: `"""Websearch client powered by Exa and Claude."""`
- `.centaur/tools/research/newsapi/client.py:1`: `"""NewsAPI.org client."""`
- `.centaur/tools/research/crunchbase/client.py:1`: `"""Crunchbase Enterprise API client."""`
- `.centaur/tools/business/attio/client.py:1`: `"""Attio API client."""`
- `.centaur/tools/productivity/company_context/client.py:1`: `"""Fetch historical company context documents."""`

**Overlay current state** (`overlay/tools/semantic_scholar/client.py:1-16`): multi-paragraph preamble explaining anonymous vs. keyed operation, hybrid search design, and a "keep in sync" provenance note.

**Gap**: Upstream uses single-sentence module docstrings universally. Design rationale lives in comments and code.

**Recommendation**: Collapse to one sentence: `"""Semantic Scholar Graph API client with hybrid indexed/live search."""`. Move detail into class docstring or inline comments.

---

### [A8] [Tests] — No `pytest_configure` and no declared `integration` marker

**Upstream pattern** (`.centaur/services/api/tests/conftest.py:27-28`):
> ```python
> def pytest_configure(config):
>     config.addinivalue_line("markers", "sandbox: requires running sandbox container")
> ```

**Overlay current state** (`overlay/workflows/tests/conftest.py`, `integration/conftest.py`): no `pytest_configure`, no custom marker.

**Gap**: CI must exclude integration tests by directory path (`--ignore=tests/integration`) rather than marker predicate (`-m "not integration"`). Fragile if files move.

**Recommendation**: Add to `overlay/workflows/tests/integration/conftest.py`:
```python
def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "integration: requires CENTAUR_TEST_DATABASE_URL pointing at a real Postgres",
    )
```
Annotate each integration test with `@pytest.mark.integration`.

---

### [A9] [Tests] — Parallel test-double hierarchies for the same S2 client concept

**Upstream pattern**: each upstream test file is self-contained (`test_company_context_documents.py` defines `FakeCtx` locally).

**Overlay current state**:
- `test_research_brief.py:20-49` and `test_save_papers.py:20-51` each define inline `MockS2Client` with richer features (call recording, `raise_exc`, context-manager protocol)
- `_mocks.py:96-138` ships a `MockSemanticScholarClient` with only `papers_by_id` / `search_results`, used only by integration tests

**Gap**: Two parallel stubs with different APIs and different features for the same third-party interface. Any refactor must touch both. The shared class exists but is bypassed by unit tests.

**Recommendation**: Pick one of: (a) enrich `MockSemanticScholarClient` with call recording / `raise_exc` and have unit tests import it (DRY), or (b) remove it and document each test file owns its own S2 stub (upstream's self-contained convention). Eliminate the bifurcation.

---

### [A10] [Tests] — Integration `conftest.py` helpers are verbatim copies of upstream

**Upstream pattern** (`.centaur/services/api/tests/conftest.py:37-104`): defines `_dsn_with_db`, `_can_connect`, `_ensure_database`, `_extract_up_sql`, `_run_migrations_async`.

**Overlay current state** (`overlay/workflows/tests/integration/conftest.py:56-119`): identical implementations, word-for-word.

**Gap**: If upstream changes the dbmate migration regex (e.g., to handle `-- migrate:up (no-transaction)`), the overlay silently falls out of sync. Duplication has no mechanical link.

**Recommendation**: Either import upstream helpers (if package-importable from this context), or factor into a shared module both sides can reference. If neither is practical, add a comment block at the top citing the upstream source file and flagging the duplication for future sync.

---

### [A11] [Tests] — TRUNCATE coupled into `db_pool` setup; upstream uses a separate `autouse` fixture

**Upstream pattern** (`.centaur/services/api/tests/test_company_context_documents.py:22-29`):
> ```python
> @pytest_asyncio.fixture(autouse=True)
> async def _clear_company_context_tables(db_pool):
>     await db_pool.execute("TRUNCATE TABLE company_context_documents, ...")
>     yield
> ```

**Overlay current state** (`overlay/workflows/tests/integration/conftest.py:156-170`):
> ```python
> @pytest_asyncio.fixture
> async def db_pool(_test_dsn: str):
>     pool = await asyncpg.create_pool(...)
>     try:
>         await pool.execute("TRUNCATE TABLE company_context_documents CASCADE")
>         yield pool
>     finally:
>         await pool.close()
> ```

**Gap**: Baking TRUNCATE into `db_pool` couples lifecycle and isolation. Adding new tables to truncate requires editing the shared fixture. Upstream keeps these orthogonal.

**Recommendation**: Extract TRUNCATE into a separate `@pytest_asyncio.fixture(autouse=True)` in `integration/conftest.py`, depending on `db_pool`. `db_pool` then only manages pool lifecycle.

---

### [A12] [Skills] — `description` frontmatter is unquoted

**Upstream pattern** (`.centaur/.agents/skills/creating-tools/SKILL.md:3`):
> `description: "Scaffold and build new tool integrations in tools/. Use when asked to create a new tool, add an API integration, or build a new client for an external service."`

5 of 6 upstream skills use quoted YAML.

**Overlay current state** (`overlay/.agents/skills/academic-research/SKILL.md:3`):
> `description: Use when answering questions about academic papers, citations, or research literature — search Semantic Scholar, surface paper metadata, walk the citation graph.`

**Gap**: Unquoted YAML containing an em-dash and prose punctuation is one future edit away from a parse error (colon, bracket, etc.).

**Recommendation**: Wrap in double quotes.

---

### [A13] [Skills] — `## When to Use` body section duplicates frontmatter `description` (no upstream analogue)

**Upstream pattern** (`.centaur/.agents/skills/qa/SKILL.md:59`): trigger guidance is inline in opening prose. None of 6 upstream skills have a dedicated `## When to Use` H2.

**Overlay current state** (`overlay/.agents/skills/academic-research/SKILL.md:13`): `## When to use` as an H2 section.

**Gap**: Creates a second source of truth for trigger spec; can drift from frontmatter; adds no value if router consumes `description`.

**Recommendation**: Remove the section. Consolidate the default-flow note into the H1 overview paragraph.

---

### [A14] [Configs] — `.dockerignore` missing `.git` exclusion

**Upstream pattern** (`.centaur/.dockerignore:2`): `.git`

**Overlay current state** (`overlay/.dockerignore`): no `.git` entry.

**Gap**: Full git history sent to Docker daemon every build. In a repo with significant history this dwarfs the other 3 COPY layers.

**Recommendation**: Add `.git` as the first entry.

---

### [A15] [Configs] — `.dockerignore` missing `*.md` with `!.agents/skills/**` exception

**Upstream pattern** (`.centaur/.dockerignore:32–36`):
> `*.md` / `!centaur_sdk/README.md` / `!services/sandbox/SYSTEM_PROMPT.md` / `!.agents/skills/**`

**Overlay current state**: no `*.md` exclusion; READMEs and other markdown under `tools/` and `workflows/` ship in the image.

**Recommendation**: Add `*.md` with `!.agents/skills/**` negation (and any other runtime-needed markdown).

---

### [A16] [Configs] — `Justfile` smoke recipes hardcode namespace/release

**Upstream pattern** (root `Justfile:3-4`):
> `export CENTAUR_NAMESPACE := env_var_or_default("CENTAUR_NAMESPACE", "centaur-system")`
> `export CENTAUR_RELEASE := env_var_or_default("CENTAUR_RELEASE", "centaur")`

Every root kubectl recipe substitutes these.

**Overlay current state** (`overlay/Justfile:74, 85`):
> `kubectl exec -n centaur-system deploy/centaur-centaur-api -- sh -c \`

**Gap**: Justfile module variables don't inherit `export`ed values from the parent. If namespace or release name changes (staging deployment, renamed release), overlay smoke recipes silently target wrong resources while the root recipes continue to work.

**Recommendation**: Define `namespace` and `release` at the top of `overlay/Justfile` via `env_var_or_default(...)`; substitute `{{namespace}}` and `{{release}}-centaur-api` in all kubectl calls.

---

# Stylistic (21)

### [S1] [Tools] — `_resolve_api_key` is a `@staticmethod`; upstream uses instance-method `_get_api_key()`

**Upstream pattern** (`.centaur/tools/research/crunchbase/client.py:55-59`; same in newsapi `:26-47`):
> ```python
> def _get_api_key(self) -> str | None:
>     if self._api_key:
>         return self._api_key
>     return secret("CRUNCHBASE_API_KEY", "")
> ```

**Overlay current state** (`overlay/tools/semantic_scholar/client.py:281-287`):
> `@staticmethod  def _resolve_api_key() -> str: return secret("SEMANTIC_SCHOLAR_API_KEY", "")`

**Gap**: `@staticmethod` can't short-circuit on `self._api_key`. Hinders fixing the Critical finding cleanly.

**Recommendation**: Convert to instance method `_get_api_key(self) -> str`, check `self._api_key` first, fall through to `secret(...)`. Match upstream naming.

---

### [S2] [Tools] — `close()` missing docstring; upstream consistently has `"""Close the HTTP client."""`

**Upstream pattern** (`.centaur/tools/research/crunchbase/client.py:329-339`; newsapi same).

**Overlay current state** (`overlay/tools/semantic_scholar/client.py:548-557`): no docstring on `close()`.

**Recommendation**: Add `"""Close the HTTP client."""`.

---

### [S3] [Tools] — `cli.py` uses `find_dotenv(usecwd=True)` and lazy `_make_client()` wrapper

**Upstream pattern** (`.centaur/tools/research/websearch/cli.py:9-13`):
> `from .client import _client` ... `load_dotenv()` ... `client = _client()`

**Overlay current state** (`overlay/tools/semantic_scholar/cli.py:28-51`): `load_dotenv(find_dotenv(usecwd=True))`; lazy `_make_client()` wrapping an absolute import.

**Gap**: Absolute import + sys.path manipulation is a structural necessity of overlay layout (mark **intentional?**). The `find_dotenv` walk-up is an undocumented divergence — repo-root `.env` is already on the bare `load_dotenv()` search path.

**Recommendation**: Mark import workaround intentional. Either add a comment explaining `find_dotenv`, or simplify to bare `load_dotenv()`.

---

### [S4] [Tools] — `_client()` factory has a verbose docstring; upstream is terse or absent

**Upstream pattern** (`.centaur/tools/research/websearch/client.py:1053-1054`):
> `def _client() -> WebSearchClient:  """Factory for tool loader."""`

newsapi/crunchbase have no docstring at all.

**Overlay current state** (`overlay/tools/semantic_scholar/client.py:560-562`):
> `"""Factory the Centaur tool loader calls to instantiate the tool."""`

**Recommendation**: Shorten to `"""Factory for tool loader."""` or remove.

---

### [S5] [Tools] — `pyproject.toml` contains `[dependency-groups]`, `[tool.pytest.ini_options]`, `[tool.uv]` sections absent upstream

**Upstream pattern** (`.centaur/tools/research/websearch/pyproject.toml`, newsapi, crunchbase): only `[project]`, `[build-system]`, `[tool.centaur]`.

**Overlay current state** (`overlay/tools/semantic_scholar/pyproject.toml:34-44`): three additional sections.

**Gap**: Overlay sections serve real purposes (`asyncio_mode = "strict"` for pytest-asyncio collection; `[tool.uv] package = false` for local `uv run` without wheel build).

**Recommendation**: Keep — they're needed. Add a brief comment on `asyncio_mode` explaining why it's present.

---

### [S6] [Tools] — No `logging` import; tool methods silently swallow exceptions into result dicts

**Upstream pattern** (`.centaur/tools/productivity/composio/client.py:5,9,57`):
> `import logging` / `log = logging.getLogger(__name__)` / `log.warning("composio list_tools failed", exc_info=True)`

**Overlay current state** (`overlay/tools/semantic_scholar/client.py`): no `import logging`, no logger.

**Gap**: Database connection failures, S2 API errors, and retry exhaustion produce no server-side log entries. Production debugging requires inspecting tool result payloads.

**Recommendation**: Add module-level `log = logging.getLogger(__name__)`. Emit `log.warning("semantic_scholar live api error", exc_info=True)` inside the `except Exception as exc: live_error = str(exc)` block (line 529-530), and `log.warning("semantic_scholar search failed", exc_info=True)` in the outer `except` in `search()` (line 444-445).

---

### [S7] [Workflows] — `vm_metrics` size observation happens after upsert, not before

**Upstream pattern** (`.centaur/workflows/company_context_documents.py:511-521`):
> `observe_company_context_document_size(...)` → `_upsert_document(...)` → `record_company_context_documents_changed(...)`

**Overlay current state** (`overlay/workflows/save_papers.py:71-72`, `research_brief.py:240-241`):
> `action = await upsert_document(...)` → `emit_document_metrics(document, action)`

**Gap**: Upstream observes size unconditionally before the upsert; if upsert raises, the document was still "seen" by the size histogram. Overlay only counts documents that survived the upsert — undercounts on failures, diverging from the dashboard model.

**Recommendation**: Restructure call sites to call `_observe_size` before `upsert_document` and `_record_changed` after. Likely requires splitting `emit_document_metrics` or inlining both calls.

---

### [S8] [Workflows] — `markdown` blob included in result dict (~20 KB into `workflow_runs.output_json`)

**Upstream pattern** (`.centaur/workflows/company_context_documents.py:558-567`): returns only compact scalars.

**Overlay current state** (`overlay/workflows/research_brief.py:229-238, 282-291`):
> `"markdown": markdown` in both result paths

**Gap**: For a 20-paper brief at 500-char abstract truncation, markdown can reach ~20 KB per run. Persisted in `workflow_runs.output_json`. Repeated runs compound. The brief is already recoverable from `company_context_documents` by `brief_document_id` (which is already returned).

**Recommendation**: Remove `"markdown": markdown` from the result dict. If a caller (Slack delivery, agent consumption) needs inline Markdown, pass it via a `ctx.step` side-effect rather than persisting in run output.

---

### [S9] [Workflows] — Private underscore symbols imported across module boundary

**Upstream pattern** (`.centaur/workflows/slack_sync.py:20-37`): imports only public-named helpers from `slack_sync_shared`.

**Overlay current state** (`overlay/workflows/research_brief.py:25-30`):
> `from _paper_document import (_canonical_json, _content_hash, build_paper_document, upsert_document,)`

**Gap**: `_canonical_json` and `_content_hash` are underscore-prefixed implementation details. Exporting them creates an informal inter-module API; future refactor of `_paper_document` (e.g., switching to `api.runtime_control.canonical_json`) silently breaks `research_brief.py`'s `_brief_id_for`.

**Recommendation**: Expose a public `content_hash_for(*parts)` from `_paper_document.py` and have `_brief_id_for` call that. Or inline the brief-ID hash in `research_brief.py`.

---

### [S10] [Workflows] — `source_updated_at` set to publication year, not sync time

**Upstream pattern** (`.centaur/workflows/company_context_documents.py:315-317`):
> `"source_updated_at": last_updated` — `max(row["updated_at"]...)`

**Overlay current state** (`overlay/workflows/_paper_document.py:143-147`):
> `"source_updated_at": occurred_at` — `datetime(year_int, 1, 1, tzinfo=UTC)`

**Gap**: `source_updated_at` is read by `vm_metrics.refresh_etl_metrics` for ETL lag (`COMPANY_CONTEXT_PROJECTION_LAG_SECONDS`). Setting it to publication year means any future `source = 'semantic_scholar'` gauge would report lags of 5-10 years.

**Recommendation**: Set to `datetime.now(UTC)` at upsert time (or to S2's `publicationDate` if returned). Publication year should remain only in `occurred_at`.

---

### [S11] [Workflows] — `metadata` None-filtering diverges from upstream explicit-null convention

**Upstream pattern** (`.centaur/workflows/company_context_documents.py:294-301, 369-378`): metadata dicts include all keys.

**Overlay current state** (`overlay/workflows/_paper_document.py:129`):
> `metadata: dict[str, Any] = {k: v for k, v in metadata_raw.items() if v is not None}`

**Gap**: In JSONB, missing key vs. null-valued key are distinct: `metadata ? 'doi'` returns `false` for absent, `true` for null. Downstream key-presence checks behave differently for `semantic_scholar` rows than for other document types.

**Recommendation**: Either (a) store nulls explicitly and filter at display, or (b) document the absent-key-for-null convention and ensure all downstream consumers use `metadata->>'doi' IS NOT NULL`.

---

### [S12] [Tests] — `sys.path.insert` guard inconsistent between unit and integration tests

**Upstream pattern** (`.centaur/services/api/tests/test_url_interceptor.py:6`): unconditional insert.

**Overlay current state**:
- `test_paper_document.py:16`, `test_research_brief.py:13`, `test_save_papers.py:12`: unconditional insert
- `integration/test_save_papers_integration.py:24-26`: guarded insert (`if str(_WORKFLOWS_DIR) not in sys.path`)

**Gap**: Inconsistent guarding makes intent unclear.

**Recommendation**: Either apply the guard everywhere, or consolidate into `tests/conftest.py` under a single guarded insert and drop the per-file boilerplate.

---

### [S13] [Tests] — `pytest-asyncio` floor two minor versions behind upstream

**Upstream pattern** (`.centaur/services/api/pyproject.toml:33`): `"pytest-asyncio>=0.25.0"`

**Overlay current state** (`overlay/workflows/pyproject.toml:17`): `"pytest-asyncio>=0.23.0"`

**Recommendation**: Bump to `>=0.25.0`.

---

### [S14] [Tests] — `_test_dsn` returns string rather than yielding; no session teardown path

**Upstream pattern** (`.centaur/services/api/tests/conftest.py:107-203`): session-scoped fixtures `yield` with `finally`.

**Overlay current state** (`overlay/workflows/tests/integration/conftest.py:122-153`): `return test_dsn`.

**Gap**: No structural handle for future teardown; arguably intentional (overlay doesn't own external Postgres).

**Recommendation**: Convert to `yield test_dsn` with an empty `finally` plus comment explaining why no teardown. Aligns with upstream fixture style and makes future teardown trivial.

---

### [S15] [Skills] — H1 title uses raw slug `# academic-research` instead of title case

**Upstream pattern** (6/6 upstream skills): `# Creating Tools`, `# Centaur QA`, `# Dogfood`, etc.

**Overlay current state** (`overlay/.agents/skills/academic-research/SKILL.md:6`): `# academic-research`

**Recommendation**: Change to `# Academic Research`.

---

### [S16] [Skills] — H2 headings in sentence case instead of title case

**Upstream pattern** (`.centaur/.agents/skills/learning-synthesis/SKILL.md:17, 86, 136`): `## What To Look For`, `## Output Contract`, `## What Not To Do`.

**Overlay current state** (`overlay/.agents/skills/academic-research/SKILL.md:13, 46, 57, 68, 81`): `## When to use`, `## Cache-aware search (...)`, etc.

**Recommendation**: Title-case all H2 headings.

---

### [S17] [Skills] — `## Anti-patterns` diverges from upstream `## What Not To Do` vocabulary

**Upstream pattern** (`.centaur/.agents/skills/learning-synthesis/SKILL.md:136`): `## What Not To Do`.

**Overlay current state** (`overlay/.agents/skills/academic-research/SKILL.md:68`): `## Anti-patterns`.

**Recommendation**: Rename to `## What Not To Do`.

---

### [S18] [Skills] — `call workflow run '...'` code blocks missing `bash` language tag

**Upstream pattern** (`.centaur/.agents/skills/auth-failure-log-triage/SKILL.md:16`): every command fence tagged `bash`.

**Overlay current state** (`overlay/.agents/skills/academic-research/SKILL.md:95-97, 117-119`): bare fences.

**Recommendation**: Add `bash` tag.

---

### [S19] [Configs] — `.dockerignore` has no grouping comments

**Upstream pattern** (`.centaur/.dockerignore:1, 4, 7, 13, 18, 22, 30, 32, 38`): section comments (`# Version control`, `# Virtual environments`, etc.).

**Overlay current state**: 10 bare patterns.

**Recommendation**: Add section comment headers matching upstream grouping.

---

### [S20] [Configs] — `.dockerignore` does not self-exclude

**Upstream pattern** (`.centaur/.dockerignore:31`): `.dockerignore`

**Overlay current state**: no self-exclusion.

**Recommendation**: Add `.dockerignore`.

---

### [S21] [Configs] — `**/.env` pattern misses `.env.*` variants

**Upstream pattern** (`.centaur/.dockerignore:39-40`): `.env` / `.env.*`

**Overlay current state** (`overlay/.dockerignore:6`): `**/.env` only.

**Gap**: Misses `.env.local`, `.env.example`, `.env.production`, `.env.test` at any depth.

**Recommendation**: Add `**/.env.*` alongside `**/.env`.

---

# Tribal (9)

### [T1] [Tools] — `tests/conftest.py` isolates sys.path; upstream inlines per test file

**Upstream pattern** (`.centaur/tools/productivity/company_context/tests/test_client.py:9-11`): inline `sys.path.insert` per file.

**Overlay current state** (`overlay/tools/semantic_scholar/tests/conftest.py:1-31`): dedicated conftest.

**Gap**: Overlay's approach is strictly better — single point of change. Mark **intentional improvement**.

---

### [T2] [Tools] — `MockAsyncpgConn` test helper missing leading underscore

**Upstream pattern** (`.centaur/tools/productivity/company_context/tests/test_client.py:16`): `class _FakeConnection`.

**Overlay current state** (`overlay/tools/semantic_scholar/tests/test_search_hybrid.py:23`): `class MockAsyncpgConn`.

**Gap**: Inconsistent underscore discipline within the test file itself (`_install_mock_conn`, `_indexed_row`, `_live_paper` all have `_`).

**Recommendation**: Rename `_MockAsyncpgConn` (or `_FakeAsyncpgConn` if A1 is resolved that direction).

---

### [T3] [Workflows] — `sys.path.insert(0, ...)` duplicated per workflow file

**Upstream pattern** (`.centaur/workflows/company_context_documents.py:1-18`): no sys.path mutation; `from api.workflow_engine import WorkflowContext` resolves via engine module discovery.

**Overlay current state** (`overlay/workflows/save_papers.py:21`, `research_brief.py:19`): both files inject overlay dir at position 0.

**Gap**: If engine already adds `WORKFLOW_DIRS` to `sys.path`, the inserts are redundant. If it doesn't, inserting at position 0 could shadow system modules.

**Recommendation**: Determine engine behavior; if it manages paths, remove inserts. Otherwise centralize in the engine loader.

---

### [T4] [Workflows] — `Input` dataclasses lack pass-through `metadata` field

**Upstream pattern** (`.centaur/workflows/company_context_documents.py:70-76`, `muesli_meeting_ingest.py:37`):
> `metadata: dict[str, Any] = field(default_factory=dict)`

**Overlay current state** (`overlay/workflows/save_papers.py:35-40`, `research_brief.py:44-50`): no `metadata` field.

**Recommendation**: Add `metadata: dict[str, Any] = field(default_factory=dict)` to both Inputs.

---

### [T5] [Workflows] — `[dependency-groups]` vs upstream `[tool.uv] dev-dependencies`

**Upstream pattern** (`.centaur/services/api/pyproject.toml:30-35`): `[tool.uv] dev-dependencies = [...]`

**Overlay current state** (`overlay/workflows/pyproject.toml:8-18`): `[dependency-groups] dev = [...]` (PEP 735).

**Gap**: Both work; PEP 735 is the direction uv is standardizing on. Inconsistency may surprise contributors.

**Recommendation**: Track for alignment when upstream migrates, or convert proactively.

---

### [T6] [Tests] — Explicit `asyncio_mode = "strict"` in overlay but absent upstream

**Upstream pattern** (`.centaur/services/api/pyproject.toml`): not set (effective default since 0.21 is `strict`).

**Overlay current state** (`overlay/workflows/pyproject.toml:26-27`): explicitly `asyncio_mode = "strict"`.

**Recommendation**: Keep — explicit is better than implicit. Add a brief comment so it isn't removed in a future cleanup.

---

### [T7] [Tests] — Integration test names prefix workflow name (`test_save_papers_writes_...`)

**Upstream pattern** (`.centaur/services/api/tests/test_company_context_documents.py:117`): description-only.

**Overlay current state** (`overlay/workflows/tests/integration/test_save_papers_integration.py:47`): workflow-prefixed.

**Gap**: Overlay test files explicitly justify the prefix in comments (workflow-name in failure logs).

**Recommendation**: Confirm as **intentional**. No change.

---

### [T8] [Skills] — No `allowed-tools` frontmatter field

**Upstream pattern** (`.centaur/.agents/skills/dogfood/SKILL.md:4`):
> `allowed-tools: Bash(agent-browser:*), Bash(npx agent-browser:*)`

**Overlay current state** (`overlay/.agents/skills/academic-research/SKILL.md`): no `allowed-tools`.

**Gap**: If the runtime enforces `allowed-tools`, constraining the skill to `semantic_scholar.*` and the two workflows would mechanically enforce the documented "prefer semantic_scholar over web search" policy.

**Recommendation**: **Intentional?** Confirm whether runtime honors `allowed-tools`. If yes, add `allowed-tools: Bash(call semantic_scholar:*), Bash(call workflow:*)`.

---

### [T9] [Configs] — `[group(...)]` decorators in overlay Justfile but not in `.centaur/Justfile`

**Upstream pattern** (`.centaur/Justfile`): no `[group]` decorators.

**Overlay current state** (`overlay/Justfile:15, 21, 25, 30, 36, 44, 49, 54, 63, 72, 83`): uses `[group('image')]`, `[group('dev')]`, `[group('cluster')]`.

**Gap**: Root org `Justfile` (which imports the overlay) uses `[group(...)]`, so the overlay aligns with the org root rather than the upstream submodule. Visual mismatch between the two files in the same repo.

**Recommendation**: **Intentional** — overlay correctly follows root Justfile pattern. Optionally document with a comment.

---

# Summary

## Counts by severity

| Severity | Count |
|---|---|
| Critical | 2 |
| Architectural | 16 |
| Stylistic | 21 |
| Tribal | 9 |
| **Total** | **48** |

## Recommended fix ordering

**Tier 1 — Correctness (do first, both are one-line code changes):**
1. **Critical [Tools]** — Fix lazy API key resolution in `_headers()` (~3 lines)
2. **Critical [Tests]** — `rm overlay/workflows/tests/_fakes.py`

**Tier 2 — Naming convention resolution (blocks T1 & ripples through tests):**

3. **A1 [Tests]** — Decide Mock vs Fake. Strong recommendation: **revert to Fake** to match upstream universality. This is a mechanical rename across 5 files; if A1 is reverted, the `_fakes.py` deletion in Tier 1 becomes a "rename `_mocks.py` → `_fakes.py`" instead.

**Tier 3 — Architectural integration risks (highest leverage for future upstream sync):**

4. **A2 [Workflows]** — Import `api.runtime_control.canonical_json` via shim. Eliminates hash divergence on non-ASCII content.
5. **A5 [Tools]** — Add `_request_async` with `await asyncio.sleep` for backoff.
6. **A6 [Tools]** — Move `DATABASE_URL` resolution into `__init__`; add `_connect()` / `_require_database_url()` helpers.
7. **A14 [Configs]** — Add `.git` to `.dockerignore`.
8. **A16 [Configs]** — Replace hardcoded namespace/release with `env_var_or_default` in Justfile.
9. **A8 [Tests]** — Register `integration` marker; annotate integration tests.

**Tier 4 — Architectural cleanups (good hygiene, lower urgency):**

10. **A3 [Workflows]** — Wrap top-level operations in `ctx.step(...)` for checkpoint durability.
11. **A9 [Tests]** — Eliminate dual S2 stub hierarchy (pick DRY vs self-contained).
12. **A10 [Tests]** — De-duplicate copy-pasted conftest helpers (or comment-cite upstream).
13. **A11 [Tests]** — Extract TRUNCATE into `autouse` fixture.
14. **A7 [Tools]** + **A12 [Skills]** + **A13 [Skills]** + **A15 [Configs]** — Docstring/frontmatter/dockerignore polish.

**Tier 5 — Stylistic & tribal:** batch in a single sweep.

## Cross-cutting themes

1. **The `Mock → Fake` rename inverted upstream's universal convention.** Resolve before any further test work (A1, Critical-2).
2. **Verbose vs. terse docstrings.** Overlay tool/workflow modules write multi-paragraph docstrings; upstream uses single-sentence. The terse style is the dominant convention (A7, S4, S10-adjacent).
3. **`sys.path` manipulation.** Both tools and workflows mutate `sys.path` at import time. Upstream uses none (T3, S12, partial T1).
4. **Drift through duplication.** Three places re-implement upstream logic verbatim: `_canonical_json` (A2), integration `conftest.py` helpers (A10), and the dockerignore (A14/A15/S19-S21). The existing `_metrics.py` try/except-ImportError shim is the proven pattern — apply it to `canonical_json` next.
5. **Result-dict shape.** Returning large blobs in workflow results (S8) and storing non-intrinsic `content_hash` (A4) both affect how upstream consumers will perceive overlay rows.

## Items the human should explicitly confirm or override (intentional?)

- **A1** — accept upstream's `Fake` convention, or document and keep `Mock`
- **A4** — compound `content_hash` (overlay deliberately stores hash-of-hash for re-parenting UPDATE)
- **S3** — `find_dotenv(usecwd=True)` in `cli.py` (probably not needed; bare `load_dotenv()` likely suffices)
- **T1** — `tests/conftest.py` for sys.path (strict improvement over upstream's inline pattern; keep)
- **T6** — explicit `asyncio_mode = "strict"` (keep; recommend a comment)
- **T7** — workflow-name-prefixed integration test function names (intentional per overlay comments)
- **T8** — adding `allowed-tools` to `academic-research/SKILL.md` (depends on whether the runtime enforces it)
- **T9** — `[group(...)]` decorators (overlay matches the root `Justfile`, not the upstream submodule; keep)