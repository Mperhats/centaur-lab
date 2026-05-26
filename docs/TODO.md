# centaur-lab backlog

Actionable items only. Completed audits and pre-reorg backlog live in git history (see `chore/reorganize` branch tip).

## Infra / deploy

- [ ] **First successful GHCR publish from Overlay CI on `main`**
  - Workflow exists (`.github/workflows/overlay.yml`) but has **never pushed** — only two failed runs on the feature branch (Justfile parse error on `just` 1.36; no run recorded after merge to `main`).
  - Push step runs only on `push` to `main` after lint + tests pass.
  - **2026-05-26 blocker:** GitHub Actions in `major_outage` per [githubstatus.com](https://www.githubstatus.com/) — `workflow_dispatch` returns HTTP 500, push events arrive (visible in `/repos/.../events`) but no runs are scheduled. Workflows show `state: active`; this is platform-side, not config-side. Spent ~hour ruling out billing, visibility (public/private flip), token scopes, path filters, and workflow YAML before checking `https://www.githubstatus.com/api/v2/components.json` — **check that endpoint first next time.**
  - **2026-05-26 Node-20 deprecation bump:** all 7 action pins in `overlay.yml` proactively moved to latest Node-24-compatible majors ahead of GitHub's 2026-06-02 forced-migration cutoff (`actions/checkout` v4→v6, `actions/setup-python` v5→v6, `astral-sh/setup-uv` v6→v8, `docker/login-action` v3→v4, `docker/setup-buildx-action` v3→v4, `docker/metadata-action` v5→v6, `docker/build-push-action` v6→v7). Verified each new pin's `action.yml` declares `using: node24`. **Diverges from upstream Centaur** (`.centaur/.github/workflows/{ci,publish-images}.yml` still on Node-20 pins as of this commit); re-sync to upstream SHAs once they migrate.
  - When Actions recovers, verify with:
    ```bash
    gh workflow run overlay.yml --ref main
    gh run list --limit 5
    gh api users/Mperhats/packages/container/centaur-lab%2Fcentaur-overlay/versions \
      --jq '.[0] | {name, created_at, tags: .metadata.container.tags}'
    ```

- [ ] **Spin up `centaur-lab-infra`** (sibling GitOps repo) shaped after [`paradigmxyz/centaur-acme-infra`](https://github.com/paradigmxyz/centaur-acme-infra). Argo CD apps + pinned overlay image tags + Helm values live there, not in this overlay repo. Pre-reorg `infra/argocd/` and `clusters/centaur-lab/argocd/` directories were deleted; recover the values shape from the `chore/reorganize` history if you need a starting point.

## Do not build

- Bind-mount overlay dev mode (not in upstream chart)
- Moving `centaur_lab/` into a published package (org shared helpers in overlay image are fine)
- Second overlay repo until a second consumer exists

## Known upstream limitation: `centaur_sdk` is not a real installable package

**TLDR:** `from centaur_sdk import secret` only resolves if `centaur_sdk/` exists at a location already on `sys.path`. There is no install path (PyPI, git+subdirectory, editable) that makes the import work portably. We expose the SDK at the repo root via a symlink instead — `centaur_sdk` → `.centaur/centaur_sdk`.

**Reproducer (the broken install paths):**

```bash
cd /tmp && rm -rf t && mkdir t && cd t && uv venv --python 3.11 -q
uv pip install "centaur-sdk @ git+https://github.com/paradigmxyz/centaur.git#subdirectory=centaur_sdk"
cd / && /tmp/t/.venv/bin/python -c "from centaur_sdk import secret"
# ModuleNotFoundError: No module named 'centaur_sdk'
```

**Root cause:** `.centaur/centaur_sdk/pyproject.toml` declares `[tool.hatch.build.targets.wheel] packages = ["."]`. Hatchling installs the dir's contents at the wheel root, so `tool_sdk.py`, `cli_tables.py`, `__init__.py`, even `README.md` and `pyproject.toml` end up loose in `site-packages/` — there is no `centaur_sdk/` package directory. Upstream's own API gets away with it because its Dockerfile `COPY centaur_sdk/ centaur_sdk/` puts the source dir at `/app/centaur_sdk/` and runs with `WORKDIR /app`; the import resolves via cwd discovery, not via the install.

**Our fix — repo-root symlink:**

```bash
ln -s .centaur/centaur_sdk centaur_sdk
```

Tracked in git as a symlink blob. The repo root is already on every entrypoint's `sys.path` (pytest discovers via `[tool.pytest.ini_options] pythonpath = ["."]` in root `pyproject.toml`; `uv run python -m tools.semantic_scholar.cli` puts cwd first by default; IDEs / mypy / pyright respect the project root), so Python finds `centaur_sdk/__init__.py` directly through the symlink. Bumping the `.centaur` submodule pin updates the SDK in lockstep with the API runtime.

| Context | Mechanism |
|---|---|
| Runtime in API pod | Centaur API container has its own `/app/centaur_sdk/`; the overlay image excludes our symlink via `.dockerignore` |
| Pytest (local + CI) | Repo root on `pythonpath` → symlink resolves to `.centaur/centaur_sdk/` |
| Tool CLIs (`uv run python -m tools.semantic_scholar.cli ...`) | Same — cwd is repo root, symlink resolves |
| IDE / mypy / pyright | All see a real package directory at the repo root |

CI requires `submodules: recursive` on `actions/checkout` so the symlink target exists.

**Trade-offs:**

- macOS / Linux work natively. Windows clones need `git config core.symlinks true` (default on most modern setups; not a deploy target for centaur-lab).
- The symlink is a phantom `centaur_sdk/` directory at the repo root that is actually content from the submodule. Documented in the README repo-map.

**Upstream fix would let us drop the symlink:** move files into `.centaur/centaur_sdk/centaur_sdk/` and change `packages = ["centaur_sdk"]`. After that, declare `centaur-sdk` via `[tool.uv.sources]` and delete the symlink. Not pursued because we don't own the upstream repo.

- [ ] If upstream ever fixes the packaging, delete the `centaur_sdk` symlink and add `centaur-sdk` as a regular dep in root `pyproject.toml` (path-sourced from `.centaur/centaur_sdk` via `[tool.uv.sources]`).

## Tool discovery after dropping per-tool pyproject.toml

The acme-mirror reorg deleted `tools/<name>/pyproject.toml` files; the root `pyproject.toml` is now the single source of truth for dev/test deps. Upstream's `tool_manager` (`.centaur/services/api/api/tool_manager.py:1574-1683`) discovers tools by scanning each `tools/<name>/` for a `pyproject.toml` and reading its `[project] dependencies` plus `[tool.centaur]` block. **Tools with no per-tool `pyproject.toml` are silently skipped at API startup**, which means deploying this overlay as-is registers zero tools.

- [ ] Before first GHCR publish, decide: (a) restore minimal per-tool `pyproject.toml` files at `tools/<name>/` for runtime discovery (matches centaur-acme exactly), or (b) propose an upstream change to `tool_manager` that discovers tools from a centralized manifest. Option (a) is the lower-risk path.

## Overlay DB migrations (future)

Decision tree, schema reuse rules, dbmate workflow, and gotchas live in [`docs/overlay-db-migrations.md`](./overlay-db-migrations.md). The first real overlay-owned migration already ships at [`services/api/db/migrations/20260526000001_add_paper_archives.sql`](../services/api/db/migrations/20260526000001_add_paper_archives.sql). Open work below; do these only when a new overlay-owned table is actually needed.

- [ ] Verify locally: `CENTAUR_OVERLAY_HOST_DIR=$PWD .centaur/contrib/scripts/dbmate --set overlay status`
