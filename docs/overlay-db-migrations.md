# Overlay DB migrations playbook

Practical guide for adding org-specific schema in centaur-lab without forking upstream Centaur.

## TL;DR

- **Reuse `company_context_documents`** for org-specific *documents* (papers, briefs, notes): JSONB `metadata`, discriminated by `source` + `source_type`. centaur-lab already does this for Semantic Scholar.
- **Add overlay migrations** only for new tables/indexes/columns upstream will never ship (e.g. `lab_experiments`).
- **Layout:** SQL files live under `services/api/db/migrations/` at the repo root. They ship in the overlay image via `Dockerfile`'s `COPY . /overlay`, get mounted at `/app/overlay/org/services/api/db/migrations/` by the chart's overlay-bootstrap initContainer, and the API pod's startup applies pending migrations through dbmate.
- **Per migration:** `.centaur/contrib/scripts/dbmate --set overlay new <name>` -> edit SQL -> `dbmate --set overlay status|up` -> bump the overlay image tag in the centaur-lab-infra Helm values; API restart applies pending overlay migrations automatically.
- **Tracking:** core uses `schema_migrations`; overlay uses `schema_migrations_overlay` (separate version sequences, no collisions).

## Decision tree

1. **Is it org-specific document/knowledge data** (searchable text, metadata, parent/child links)?
   -> **Reuse `company_context_documents`**. Pick a new `source` (e.g. `semantic_scholar`) and `source_type` (e.g. `paper`).
2. **Does it need relational structure** (FKs, unique constraints across rows, non-document queries) that JSONB can't express cleanly?
   -> **Overlay migration** (new table).
3. **Could upstream plausibly add this in ~6 months?**
   -> File upstream issue first; avoid overlay schema that blocks upstream merges.
4. **Is it a column on an upstream-owned table?**
   -> Strong bias against overlay `ALTER TABLE`; prefer new overlay-owned table or JSONB in `company_context_documents`.

## Reuse path: `company_context_documents`

### Schema (upstream migration 022)

| Column | Type | Notes |
|--------|------|-------|
| `document_id` | TEXT PK | Stable id, e.g. `semantic_scholar:paper:{id}` |
| `source` | TEXT NOT NULL | Namespace, e.g. `semantic_scholar`, `slack` |
| `source_type` | TEXT NOT NULL | Discriminator within source |
| `source_document_id` | TEXT NOT NULL | External id |
| `source_chunk_id` | TEXT NOT NULL DEFAULT `''` | Chunk id (usually empty) |
| `parent_document_id` | TEXT FK -> self | Parent/child linking |
| `title`, `body`, `url` | TEXT | Searchable content |
| `author_id`, `author_name` | TEXT | |
| `access_scope` | TEXT DEFAULT `company` | |
| `occurred_at`, `source_updated_at` | TIMESTAMPTZ | Event vs sync time |
| `content_hash` | TEXT | Idempotent upserts |
| `metadata` | JSONB DEFAULT `{}` | Structured extras |
| `created_at`, `updated_at` | TIMESTAMPTZ | |

**Indexes:** `(source, source_type, occurred_at DESC)`, `parent_document_id`, `source_updated_at DESC`, GIN on `metadata`. BM25 index added in upstream migration 023.

**Unique:** `(source, source_type, source_document_id, source_chunk_id)`.

### `source_type` values in centaur-lab

| `source_type` | `source` | Where |
|---------------|----------|-------|
| `paper` | `semantic_scholar` | `tools/semantic_scholar/projections/paper.py` |
| `paper_fulltext` | `semantic_scholar` | `tools/semantic_scholar/projections/fulltext.py` |
| `research_brief` | `semantic_scholar` | `tools/semantic_scholar/projections/brief.py` |
| `slack_channel_day` | `slack` | `.centaur/workflows/company_context_documents.py` (upstream) |
| `slack_thread` | `slack` | `.centaur/workflows/company_context_documents.py` (upstream) |

### Code pattern (centaur-lab)

`save_papers` (in `workflows/save_papers.py`) calls `build_paper_document` (`source="semantic_scholar"`, `source_type="paper"`, `metadata={paperId, year, doi, query, ...}`) then `upsert_document` via asyncpg `INSERT ... ON CONFLICT (document_id) DO UPDATE WHERE content_hash IS DISTINCT FROM EXCLUDED.content_hash`. The follow-up brief workflow writes a `research_brief` parent row.

### When reuse is enough vs not

| Enough | Not enough |
|--------|------------|
| Searchable documents with JSONB metadata | Multi-table relational model |
| Parent/child via `parent_document_id` | Cross-row constraints beyond self-FK |
| BM25 retrieval via existing index | New index type upstream won't add |
| Org-specific `source`/`source_type` namespace | Mutating upstream-owned tables |

## New table path: overlay migrations

### Mechanics

| What | Detail |
|------|--------|
| Host repo path | `services/api/db/migrations/` |
| API env | `CENTAUR_OVERLAY_DIR` = chart `overlay.mountPath` (default `/app/overlay/org`), set in centaur-lab-infra Helm values |
| Resolved path in pod | `$CENTAUR_OVERLAY_DIR/services/api/db/migrations/` |
| Tracking table | `schema_migrations_overlay` |
| When applied | **API startup only** -- `create_pool()` -> `run_migrations()` runs dbmate `up` for core then overlay |
| Worker | Reuses API pool; does **not** run migrations independently |
| Deploy | Bump the overlay image tag in centaur-lab-infra; the chart's overlay-bootstrap initContainer copies the new image into `/app/overlay/org`, and API restart applies pending migrations |

The overlay's first real migration ships at [`services/api/db/migrations/20260526000001_add_paper_archives.sql`](../services/api/db/migrations/20260526000001_add_paper_archives.sql).

### Per-migration workflow

**1. Scaffold** (from repo root, with a local cluster running):

```bash
export CENTAUR_OVERLAY_HOST_DIR="$PWD"
export CENTAUR_NAMESPACE=centaur-system   # centaur-lab default

.centaur/contrib/scripts/dbmate --set overlay new add_lab_experiments
# -> creates services/api/db/migrations/<NNN>_add_lab_experiments.sql
```

**2. Edit file** -- numbered `NNN_snake_case.sql`; only `-- migrate:up` / `-- migrate:down` sections (no manual `BEGIN`/`COMMIT`; dbmate wraps each section in a transaction).

**3. Local validation**

```bash
.centaur/contrib/scripts/dbmate --set overlay status
.centaur/contrib/scripts/dbmate --set overlay up
.centaur/contrib/scripts/dbmate --set overlay rollback   # rolls back last
```

Without `DATABASE_URL`, the wrapper reads it from the running API pod via `kubectl exec`.

**4. Deploy**

1. Merge to `main` in this repo -> CI publishes a new overlay image tag (`sha-<sha>`).
2. In `centaur-lab-infra`, bump `overlay.image.tag` to the new SHA.
3. Argo CD reconciles -> API pod restarts -> overlay migrations apply at startup.

Verify in pod:

```bash
kubectl exec -n centaur-system deploy/centaur-centaur-api -- \
  ls -la /app/overlay/org/services/api/db/migrations/
```

### Local dev stack

Cluster bring-up + `kubectl port-forward` are owned by the centaur-lab-infra repo (the upstream `Justfile` lives in `.centaur/Justfile` and is invoked from there with deploy-time secrets). Once the stack is up, Postgres is accessible via:

```bash
kubectl port-forward -n centaur-system svc/centaur-centaur-postgres 5432:5432
# DSN: postgres://tempo:$PGPASSWORD@localhost:5432/ai_v2
# Password sourced from the centaur-infra-env Secret (set up by infra repo)
```

## Gotchas

| Topic | Detail |
|-------|--------|
| **dbmate host path default** | Wrapper default `CENTAUR_OVERLAY_HOST_DIR` = `.centaur/../centaur-paradigm` (upstream's reference layout), **not** centaur-lab's repo root. Always `export CENTAUR_OVERLAY_HOST_DIR=$PWD`. |
| **dbmate script path** | Real path is `.centaur/contrib/scripts/dbmate`. Upstream `AGENTS.md` references `./scripts/dbmate` (their layout). |
| **Overlay set visibility** | `--set overlay` only listed if host dir exists (`list_sets` checks directory). |
| **Transactions** | No core migration uses `BEGIN`/`COMMIT`. dbmate auto-wraps each `migrate:up`/`migrate:down` block. |
| **Down command** | SQL `-- migrate:down` section; CLI subcommand is `rollback` (not `down`). |
| **Numbering** | Auto-incremented `NNN_` prefix or timestamp; both work. The first overlay migration uses a timestamp (`20260526000001_`) so subsequent ones don't collide on a small numeric counter. |
| **Overlay dir gate** | API includes overlay set only when `CENTAUR_OVERLAY_DIR` is set **and** migrations dir exists in container. |
| **Missing dbmate binary** | API logs warning and skips migrations (`FileNotFoundError` caught) -- do not rely on this in prod. |

## References

| Claim | Source |
|-------|--------|
| `get_migration_sets`, path resolution, tracking tables | `.centaur/services/api/api/db.py:14-87` |
| `run_migrations`, dbmate invocation | `.centaur/services/api/api/db.py:115-174` |
| Migrations at API startup | `.centaur/services/api/api/db.py:99-100`, `.centaur/services/api/api/app.py:173` |
| `schema_migrations` / `schema_migrations_overlay` | `.centaur/services/api/api/db.py:15-16` |
| dbmate `--set overlay`, `new`, `status`, `up`, `rollback` | `.centaur/contrib/scripts/dbmate:18-215` |
| Overlay host/container path env vars | `.centaur/contrib/scripts/dbmate:10-13` |
| Default overlay host = `../centaur-paradigm` | `.centaur/contrib/scripts/dbmate:10` |
| Numbered file naming | `.centaur/contrib/scripts/dbmate:91-126` |
| `company_context_documents` schema | `.centaur/services/api/db/migrations/022_add_company_context_documents.sql` |
| BM25 index | `.centaur/services/api/db/migrations/023_add_company_context_documents_bm25.sql` |
| Overlay mount + migrations mention | `.centaur/docs/public/md/extend/overlay.md:45-46` |
| `CENTAUR_OVERLAY_DIR` chart wiring | `.centaur/contrib/chart/templates/workloads.yaml:204-207`, `.centaur/contrib/chart/values.yaml:92` |
| Overlay bootstrap copy | `.centaur/contrib/chart/templates/workloads.yaml:134-149` |
| centaur-lab Dockerfile (single `COPY . /overlay`) | [`Dockerfile`](../Dockerfile) |
| First centaur-lab overlay migration | [`services/api/db/migrations/20260526000001_add_paper_archives.sql`](../services/api/db/migrations/20260526000001_add_paper_archives.sql) |
| Paper upsert pattern | [`tools/semantic_scholar/projections/paper.py`](../tools/semantic_scholar/projections/paper.py), [`workflows/save_papers.py`](../workflows/save_papers.py) |
