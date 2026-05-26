# centaur-lab backlog

Actionable items only. Completed audits and MVP plans live in git history.

## Infra / deploy

- [x] **Fix GHCR overlay image path in `infra/argocd/values/centaur.yaml`**
  - Target image: `ghcr.io/Mperhats/centaur-lab/centaur-overlay:sha-<git>` (see `.github/workflows/overlay.yml`).
  - Path fixed in `infra/argocd/values/centaur.yaml` and `infra/argocd/application.yaml`.

- [ ] **First successful GHCR publish from Overlay CI on `main`**
  - Workflow exists (`.github/workflows/overlay.yml`) but has **never pushed** — only two failed runs on the feature branch (Justfile parse error on `just` 1.36; no run recorded after merge to `main`).
  - Push step runs only on `push` to `main` after lint + tests pass.
  - **2026-05-26 blocker:** GitHub Actions in `major_outage` per [githubstatus.com](https://www.githubstatus.com/) — `workflow_dispatch` returns HTTP 500, push events arrive (visible in `/repos/.../events`) but no runs are scheduled. Workflows show `state: active`; this is platform-side, not config-side. Spent ~hour ruling out billing, visibility (public/private flip), token scopes, path filters, and workflow YAML before checking `https://www.githubstatus.com/api/v2/components.json` — **check that endpoint first next time.**
  - When Actions recovers, verify with:
    ```bash
    gh workflow run overlay.yml --ref main
    gh run list --limit 5
    gh api users/Mperhats/packages/container/centaur-lab%2Fcentaur-overlay/versions \
      --jq '.[0] | {name, created_at, tags: .metadata.container.tags}'
    ```

- [x] **Merge `feat/deploy-alignment`** — values split, sha tags, CI workflow, infra skeleton.

## Safe to delete (done or recommended)

| Item | Why |
|------|-----|
| ~~`docs/review.md`~~ | Deleted — one-shot audit; critical API-key finding already fixed |
| ~~`docs/centaur/`~~ | Deleted — offline mirror of `.centaur/docs/public/md/`; link to [centaur.run](https://centaur.run) instead |
| ~~`docs/superpowers/plans/2026-05-25-centaur-lab-mvp.md`~~ | Deleted — completed plan; keep `specs/` + deploy alignment plan only |

## Optional slim-down (only if annoying)

- [x] Drop `just overlay::lint-tools` / `lint-workflows` — `just overlay::lint` already covers both
- [x] Drop `just overlay::reload-api` / `reload-skills` — keep single `just reload` unless you use the split weekly
- [x] ~~Rename test doubles `Mock*` → `Fake*`~~ — **won't do**; review A9 kept inline `Mock*` stubs (`e18bee5`) instead of reviving upstream `Fake*` hierarchy

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

## Known upstream limitation: `centaur_sdk` is not a real installable package

**TLDR:** `from centaur_sdk import secret` only resolves if `.centaur/` is on `sys.path`. There is no install path (PyPI, git+subdirectory, editable) that makes the import work portably.

**Reproducer:**

```bash
cd /tmp && rm -rf t && mkdir t && cd t && uv venv --python 3.11 -q
uv pip install "centaur-sdk @ git+https://github.com/paradigmxyz/centaur.git#subdirectory=centaur_sdk"
cd / && /tmp/t/.venv/bin/python -c "from centaur_sdk import secret"
# ModuleNotFoundError: No module named 'centaur_sdk'
```

**Root cause:** `.centaur/centaur_sdk/pyproject.toml` declares `[tool.hatch.build.targets.wheel] packages = ["."]`. Hatchling installs the dir's contents at the wheel root, so `tool_sdk.py`, `cli_tables.py`, `__init__.py`, even `README.md` and `pyproject.toml` end up loose in `site-packages/` — there is no `centaur_sdk/` package directory. Upstream's own API gets away with it because its Dockerfile `COPY centaur_sdk/ centaur_sdk/` puts the source dir at `/app/centaur_sdk/` and runs with `WORKDIR /app`; the import resolves via cwd discovery, not via the install.

Neither the README's `pip install "centaur-sdk @ git+..."` snippet nor the AGENTS.md `pip install centaur-sdk` comment actually works — the package is not on PyPI ([paradigmxyz packages](https://github.com/orgs/paradigmxyz/packages?repo_name=centaur) lists 4 containers, no SDK; `pypi.org/simple/centaur-sdk/` 404s).

**Our workaround (`.github/workflows/overlay.yml`, `overlay/*/pyproject.toml`, `overlay/tools/semantic_scholar/cli.py`):**

| Context | Mechanism |
|---|---|
| Runtime in API pod | Centaur tool loader puts `overlay/` on `sys.path`; `/app/centaur_sdk/` already on cwd path |
| Pytest (local + CI) | `[tool.pytest.ini_options] pythonpath = ["..", "../..", "../../../.centaur"]` in tool pyproject (+ analogous in workflows pyproject) |
| `uv run python cli.py` from a tool dir | 9 lines of `sys.path.insert` bootstrap in `cli.py` (documented in the file) |

CI requires `submodules: recursive` on `actions/checkout` so `.centaur/centaur_sdk/` is present for pytest.

**Upstream fix would collapse all of the above:** move files into `.centaur/centaur_sdk/centaur_sdk/` and change `packages = ["centaur_sdk"]`. After that, declare `centaur-sdk @ git+...` as a regular dep and delete every workaround. Not pursued because we don't own the upstream repo.

- [ ] If upstream ever fixes the packaging, drop pythonpath/cli bootstrap and add `centaur-sdk @ git+...` as a regular dep in overlay tool/workflow pyprojects

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
