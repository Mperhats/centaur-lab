# centaur-lab backlog

Actionable items only. Completed audits and MVP plans live in git history.

## Infra / deploy

- [x] **Fix GHCR overlay image path in `infra/argocd/values/centaur.yaml`**
  - CI publishes `ghcr.io/Mperhats/centaur-lab/centaur-overlay` (see `.github/workflows/overlay.yml`).
  - Fixed in `infra/argocd/values/centaur.yaml` and `infra/argocd/application.yaml`.

- [x] **Merge `feat/deploy-alignment`** — values split, sha tags, CI, infra skeleton.

## Safe to delete (done or recommended)

| Item | Why |
|------|-----|
| ~~`docs/review.md`~~ | Deleted — one-shot audit; critical API-key finding already fixed |
| ~~`docs/centaur/`~~ | Deleted — offline mirror of `.centaur/docs/public/md/`; link to [centaur.run](https://centaur.run) instead |
| ~~`docs/superpowers/plans/2026-05-25-centaur-lab-mvp.md`~~ | Deleted — completed plan; keep `specs/` + deploy alignment plan only |

## Optional slim-down (only if annoying)

- [x] Drop `just overlay::lint-tools` / `lint-workflows` — `just overlay::lint` already covers both
- [x] Drop `just overlay::reload-api` / `reload-skills` — keep single `just reload` unless you use the split weekly
- [ ] Rename test doubles `Mock*` → `Fake*` to match upstream — cosmetic, 5 files

## Do not build

- Bind-mount overlay dev mode (not in upstream chart)
- Root `pyproject.toml` / uv workspace wrapping overlay (Centaur expects per-tool `pyproject.toml`)
- Moving `centaur_lab/` into a published package (org shared helpers in overlay image are fine)
- Second overlay repo until a second consumer exists

## Python / uv / ruff — current shape vs Centaur

**Matches upstream (keep as-is):**

- One `pyproject.toml` per tool under `overlay/tools/<name>/` with `[tool.centaur]` block
- Workflows are loose `.py` files; `overlay/workflows/pyproject.toml` exists **only for local pytest deps** (`package = false`) — same pattern as upstream workflow tests
- `overlay/ruff.toml` mirrors `.centaur/tools/ruff.toml` — lint via `uvx ruff check .` from tool/workflow dirs
- `db/pyproject.toml` — separate local notebook helper; not part of overlay image (correct)

**No changes needed unless you want one convenience:**

- Point overlay lint at shared config explicitly: `uvx ruff check --config ruff.toml .` from `overlay/` root (today each subdir inherits when run from `tools/` / `workflows/`)

**Real bug already fixed:** lazy `secret()` in `SemanticScholarClient._get_api_key()` (was in old audit as critical).

## Overlay DB migrations (future)

**Reuse core schema vs add overlay migrations**

- **Reuse `company_context_documents`** for org-specific *data* (JSONB `metadata`, `source` / `source_type`). centaur-lab already does this for Semantic Scholar — table defined in `.centaur/services/api/db/migrations/022_add_company_context_documents.sql`.
- **Add overlay migrations** only for *new* tables/indexes/columns upstream will never ship (e.g. a dedicated `lab_experiments` table).

**Centaur mechanics** (see `.centaur/services/api/api/db.py`: `get_migration_sets`, `run_migrations`)

| What | Where |
|------|-------|
| Overlay migration dir | `$CENTAUR_OVERLAY_DIR/services/api/db/migrations/` → repo path `overlay/services/api/db/migrations/` |
| Tracking table | `schema_migrations_overlay` (core uses `schema_migrations`) |
| When applied | API startup — `create_pool()` runs dbmate `up` for both sets |
| File format | Numbered `*.sql` with `-- migrate:up` / `-- migrate:down` (same as core) |
| Local CLI | `.centaur/contrib/scripts/dbmate --set overlay new|status|up` (`.centaur/AGENTS.md`) |
| Mount context | `.centaur/docs/public/md/extend/overlay.md` |

**centaur-lab today:** no `overlay/services/api/db/migrations/`; `overlay/Dockerfile` copies `tools/`, `workflows/`, `centaur_lab/`, `.agents/` only.

**Skeleton if we ever need a new table:**

```sql
-- overlay/services/api/db/migrations/001_add_lab_experiments.sql
-- migrate:up
CREATE TABLE IF NOT EXISTS lab_experiments (
    experiment_id TEXT PRIMARY KEY,
    title         TEXT NOT NULL DEFAULT '',
    metadata      JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
-- migrate:down
DROP TABLE IF EXISTS lab_experiments;
```

- [ ] Add `overlay/services/api/db/migrations/` + first migration
- [ ] Extend `overlay/Dockerfile`: `COPY services /overlay/services`
- [ ] Verify: `.centaur/contrib/scripts/dbmate --set overlay status`
