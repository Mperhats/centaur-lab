# ACME-mirror reorganization (option B)

Move the centaur-lab repo so its surface matches `paradigmxyz/centaur-acme`
(overlay extension points at the root) and the `infra/` shape matches
`paradigmxyz/centaur-acme-infra` (`clusters/<name>/argocd/{bootstrap,values,apps}/`).

Single-repo target. Dev/ops tooling (`db/`, `cloudflared/`, `docs/`,
`Justfile`, `values.*.yaml`, `.centaur/` and `.scientist/` submodules)
stays at root and is excluded from the overlay image via `.dockerignore`.

## Decisions (already locked in)

| Question | Answer |
|----------|--------|
| Layout approach | B — promote `overlay/*` to root, single repo |
| `.scientist/` | Keep |
| `services/sandbox/SYSTEM_PROMPT.md` | Add now with real content |
| Root `pyproject.toml` | Add (workspace-style, mirrors centaur-acme) |
| Tests during reorg | Skip — verify only `docker build .` and `just --list` |
| Aggressive deletion | Yes — drop duplicative `docs/centaur/`, transitional `infra/`, empty `.agents/skills/` at root |

## Phases

### Phase 1 — pre-flight (DONE)

- [x] `.centaur` submodule pinned to `0656aeb5` (was `6a96324c`, 8 commits behind).

### Phase 2 — promote overlay extension points to root

`git mv` (preserves history):

- `overlay/tools/` → `tools/`
- `overlay/workflows/` → `workflows/`
- `overlay/.agents/skills/academic-research/` → `.agents/skills/academic-research/`
  (the empty `.agents/skills/` at root already exists; rmdir first if needed)
- `overlay/services/api/db/migrations/*.sql` → `services/api/db/migrations/*.sql`
- `overlay/Dockerfile` → `Dockerfile`
- `overlay/.dockerignore` → `.dockerignore`
- `overlay/ruff.toml` → `ruff.toml`

### Phase 3 — reshape `infra/` to `clusters/centaur-lab/argocd/`

Match `centaur-acme-infra` layout:

- `mkdir -p clusters/centaur-lab/argocd/{bootstrap,values,apps}`
- `git mv infra/argocd/application.yaml clusters/centaur-lab/argocd/bootstrap/centaur.yaml`
- `git mv infra/argocd/values/centaur.yaml clusters/centaur-lab/argocd/values/centaur.yaml`
- `git mv infra/README.md clusters/centaur-lab/README.md` (and refresh content)

### Phase 4 — new files

- Root `pyproject.toml` — minimal workspace tying `tools/` and `workflows/`
  together (pytest configuration, dev deps for ruff). No `uv.lock` — per-tool
  pyproject.toml stays the source of resolution.
- `services/sandbox/SYSTEM_PROMPT.md` — real content. Centaur-lab is a
  research-paper agent: the prompt should describe the academic-research
  domain, point at `tools/semantic_scholar`, `tools/pdf`, the four
  `workflows/*.py`, and the `.agents/skills/academic-research` playbook.

### Phase 5 — update path references

Files that hardcode `overlay/...`:

- `.dockerignore` — extend to exclude `.centaur/`, `.scientist/`, `db/`,
  `cloudflared/`, `docs/`, `clusters/`, `infra/` (transitional),
  `values.*.yaml`, `Justfile`, `.env*`, plus the existing entries.
- `.gitignore` — drop `overlay/` path prefixes, replace with root equivalents
  (`tools/*/uv.lock`, `workflows/uv.lock`, `.tag`).
- `values.org.yaml` — comment block referring to `overlay/.tag` → `.tag`.
- `.github/workflows/overlay.yml`:
  - `paths:` filter `overlay/**` → root paths (`tools/**`, `workflows/**`,
    `.agents/**`, `services/**`, `Dockerfile`, `.dockerignore`, `pyproject.toml`)
  - All `working-directory: overlay/...` → root-relative.
  - `context: overlay` → `context: .`
- Root `Justfile` — absorb every recipe from `overlay/Justfile` into the root
  with the same `[group('...')]` labels. Drop `mod overlay 'overlay/Justfile'`.
  Adjust internal callers (`just overlay::build` → `just build`,
  `just overlay::reload` → `just reload`, etc.). Old `up`'s
  `just overlay::build` step → `just build`.
- `README.md` — full path refresh (every `overlay/foo` reference in tables,
  examples, troubleshooting, and CI section).

### Phase 6 — aggressive deletions

- `docs/centaur/` — vendored copy of upstream centaur public docs;
  `.centaur/docs/public/md/` is the source of truth.
- `infra/` directory tree (post-reshape).
- `overlay/` directory tree (post-promote).
- `overlay/Justfile` (absorbed into root).
- Empty top-level `.agents/skills/` if not already replaced by the promoted
  academic-research skill.
- `overlay/.tag` if tracked (gitignored — should be a no-op).

### Phase 7 — verification (tests deferred per user direction)

- `git status` — every change should be either a `git mv`, a tracked
  modification, or a deletion of a known artifact.
- `docker build -t centaur-overlay:reorg-smoke .` from repo root succeeds.
- `just --list` runs cleanly and shows the absorbed recipes.

## Out of scope

- Running pytest / integration tests — explicitly deferred.
- Updating individual tool/workflow contents.
- `.scientist/` cleanup — kept.
- New skills, tools, or workflows.

## Followup (separate PRs)

- Verify `services/sandbox/SYSTEM_PROMPT.md` actually loads in the sandbox
  (`echo "$CENTAUR_OVERLAY_DIR"` + path inspection per the overlay docs).
- Re-run the workflow + tool test suites from their new paths.
- Pin `clusters/centaur-lab/argocd/bootstrap/centaur.yaml`'s `targetRevision`
  to the new `.centaur` SHA (`0656aeb5`).
