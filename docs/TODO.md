# centaur-lab backlog

Actionable items only. Completed audits and MVP plans live in git history.

## Infra / deploy

- [ ] **Fix GHCR overlay image path in `infra/argocd/values/centaur.yaml`**
  - Template currently says `ghcr.io/Mperhats/centaur-overlay` — **wrong**.
  - CI (`.github/workflows/overlay.yml`) publishes `ghcr.io/${{ github.repository }}/centaur-overlay` → **`ghcr.io/Mperhats/centaur-lab/centaur-overlay`**.
  - Package does **not exist** until workflow merges to `main` and a push touches `overlay/**`.
  - Also fix the same placeholder in `infra/argocd/application.yaml` `overlay.image.repository` parameter.

- [ ] **Merge `feat/deploy-alignment`** — values split, sha tags, CI, infra skeleton (worktree `.worktrees/deploy-alignment`).

## Safe to delete (done or recommended)

| Item | Why |
|------|-----|
| `docs/review.md` | 762-line one-shot audit; critical API-key finding already fixed; `_fakes.py` already gone |
| `docs/centaur/` | Offline mirror of `.centaur/docs/public/md/` — drifts on every submodule bump; link to [centaur.run](https://centaur.run) instead |
| `docs/superpowers/plans/2026-05-25-centaur-lab-mvp.md` | 1000+ line completed plan; keep `specs/` + deploy alignment plan only |

## Optional slim-down (only if annoying)

- [ ] Drop `just overlay::lint-tools` / `lint-workflows` — `just overlay::lint` already covers both
- [ ] Drop `just overlay::reload-api` / `reload-skills` — keep single `just reload` unless you use the split weekly
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
