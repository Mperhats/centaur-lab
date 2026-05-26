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

**TLDR:** `from centaur_sdk import secret` only resolves if `.centaur/` is on `sys.path`. There is no install path (PyPI, git+subdirectory, editable) that makes the import work portably.

**Reproducer:**

```bash
cd /tmp && rm -rf t && mkdir t && cd t && uv venv --python 3.11 -q
uv pip install "centaur-sdk @ git+https://github.com/paradigmxyz/centaur.git#subdirectory=centaur_sdk"
cd / && /tmp/t/.venv/bin/python -c "from centaur_sdk import secret"
# ModuleNotFoundError: No module named 'centaur_sdk'
```

**Root cause:** `.centaur/centaur_sdk/pyproject.toml` declares `[tool.hatch.build.targets.wheel] packages = ["."]`. Hatchling installs the dir's contents at the wheel root, so `tool_sdk.py`, `cli_tables.py`, `__init__.py`, even `README.md` and `pyproject.toml` end up loose in `site-packages/` — there is no `centaur_sdk/` package directory. Upstream's own API gets away with it because its Dockerfile `COPY centaur_sdk/ centaur_sdk/` puts the source dir at `/app/centaur_sdk/` and runs with `WORKDIR /app`; the import resolves via cwd discovery, not via the install.

We re-verified this against `uv add centaur-sdk = { path = ".centaur/centaur_sdk", editable = true }`: uv produces a working `dist-info` and `.pth` file pointing at `.centaur/centaur_sdk`, but because the `.pth` puts the package's *contents* on `sys.path` (not its parent), `from centaur_sdk import …` still fails — `tool_sdk` becomes a top-level module instead.

Neither the README's `pip install "centaur-sdk @ git+..."` snippet nor the AGENTS.md `pip install centaur-sdk` comment actually works — the package is not on PyPI ([paradigmxyz packages](https://github.com/orgs/paradigmxyz/packages?repo_name=centaur) lists 4 containers, no SDK; `pypi.org/simple/centaur-sdk/` 404s).

**Our current workaround** (`pyproject.toml`, `.github/workflows/overlay.yml`):

| Context | Mechanism |
|---|---|
| Runtime in API pod | Centaur tool loader puts `tools/` + overlay tool dirs on `sys.path`; `/app/centaur_sdk/` already on cwd path |
| Pytest (local + CI) | `[tool.pytest.ini_options] pythonpath = [".", ".centaur"]` in root `pyproject.toml` puts the submodule's parent on the path so `import centaur_sdk` resolves to `.centaur/centaur_sdk/` as a package |
| Tool CLIs (`uv run python -m tools.semantic_scholar.cli ...`) | The same root `pyproject.toml` `pythonpath` is honored by pytest only — for ad-hoc CLI runs, `PYTHONPATH=.centaur uv run …` |

CI requires `submodules: recursive` on `actions/checkout` so `.centaur/centaur_sdk/` is present for pytest.

**Upstream fix would collapse all of the above:** move files into `.centaur/centaur_sdk/centaur_sdk/` and change `packages = ["centaur_sdk"]`. After that, declare `centaur-sdk @ git+...` (or path-installed from the submodule) as a regular dep and delete every workaround. Not pursued because we don't own the upstream repo.

- [ ] If upstream ever fixes the packaging, drop the `pythonpath` entry and add `centaur-sdk` as a regular dep in root `pyproject.toml` (path-sourced from `.centaur/centaur_sdk` via `[tool.uv.sources]`).

## Overlay DB migrations (future)

Decision tree, schema reuse rules, dbmate workflow, and gotchas live in [`docs/overlay-db-migrations.md`](./overlay-db-migrations.md). The first real overlay-owned migration already ships at [`services/api/db/migrations/20260526000001_add_paper_archives.sql`](../services/api/db/migrations/20260526000001_add_paper_archives.sql). Open work below; do these only when a new overlay-owned table is actually needed.

- [ ] Verify locally: `CENTAUR_OVERLAY_HOST_DIR=$PWD .centaur/contrib/scripts/dbmate --set overlay status`
