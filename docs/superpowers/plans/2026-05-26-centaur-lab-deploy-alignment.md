# centaur-lab Deploy Alignment Plan

**Goal:** Match how Centaur actually ships overlays (image copy + tagged deploy), delete MVP config soup, keep local dev fast.

**Principle:** Low lines, high reliability. No bind-mount forks, no duplicate repos, no Argo on the laptop.

**References:** [overlay.md](https://centaur.run/extend/overlay), [acme-example](https://centaur.run/extend/acme-example), `.centaur/contrib/chart/templates/workloads.yaml` (overlay-bootstrap init), `.centaur/services/api/api/app.py` (plugin watcher).

---

## How Centaur works (facts)

```text
Build overlay image  →  init container copies /overlay → emptyDir
                       →  API:   /app/overlay/org     (tools, workflows)
                       →  Sandbox: /home/agent/overlay/org  (skills)

Pod restart when:  Helm overlay values change (checksum/overlay hashes .Values.overlay)
                   NOT when :latest image content changes

Hot reload when:   Files change ON DISK inside the running API pod
                   (watcher + POST /admin/reload-tools — tools only)
                   Does NOT pull a rebuilt image; does NOT refresh sandbox skills
```

**Production (ACME):** overlay repo → `ghcr.io/.../centaur-overlay:sha-xxx` → infra repo bumps `overlay.image.tag` → Argo rolls pods.

**Upstream Justfile:** `build`, `deploy`, `up`. No overlay recipe.

---

## What centaur-lab added (and what to keep)

| Added | Verdict |
|-------|---------|
| `overlay/` + Dockerfile | **Keep** — matches Centaur |
| `values.local.yaml` (123 lines, mixed concerns) | **Split** — see Phase 1 |
| `overlay.tag: latest` + manual rollout | **Replace** — sha tag + deploy |
| `just overlay::reload` | **Keep thin** until Phase 2 proves deploy-only is enough |
| `pullPolicy: IfNotPresent` overrides | **Keep in local-only values** — Docker Desktop still needs them |
| Bind-mount dev mode | **Do not build** — not in chart |
| Local Argo / Image Updater | **Do not build** — prod-only |
| Separate overlay repo now | **Defer** — monorepo is fine until a second consumer exists |

---

## Phase 1 — Split values (~1 PR, ~30 min)

**Why:** Most “nonsense” is org config dressed as local config.

Create `values.org.yaml`:

- `defaultHarness`, Slackbot + Slack ETL knobs
- `ironProxy.secretSource: env` (dev)
- `overlay.image.repository: centaur-overlay` (name only; tag moves in Phase 2)
- `repoCache` (enabled, repos, token wiring)

Slim `values.local.yaml` to laptop-only:

- `api` / `ironProxy` / `sandbox` / `overlay.image.pullPolicy: IfNotPresent`
- `api.warmPoolEnabled: false`
- `repoCache.hostPath` (Docker Desktop)

Update root `Justfile` deploy:

```bash
helm upgrade ... -f values.dev.yaml -f values.org.yaml -f values.local.yaml
```

**Delete:** Long stale comments about upstream gaps; duplicated blocks moved to org.

**Do not add:** `values.prod.yaml` yet.

---

## Phase 2 — Sha-tagged overlay (~1 PR, ~1 hr)

**Why:** Centaur’s intended refresh mechanism is **tag change → pod roll**, not `:latest` + kubectl restart.

1. Change `overlay/Justfile` `build` to tag `centaur-overlay:sha-$(git rev-parse --short HEAD)`.
2. Write tag to a one-line file `overlay/.tag` (gitignored) or export for deploy.
3. Extend `just deploy` (or add `just overlay::publish-local`) to pass:
   `--set overlay.image.tag=sha-<short>`
4. Helm `checksum/overlay` restarts API; sandboxes pick up overlay on next spawn.

**Simplify `reload`:** becomes `build` + `deploy` (drop manual `rollout restart` if deploy rolls pods).

**Keep:** Sandbox delete step in reload **only when skills changed** — Centaur copies skills at sandbox entrypoint; tag bump alone does not fix already-running sandboxes.

Optional split (only if annoying in practice):

- `just reload-api` → build + deploy
- `just reload-skills` → delete sandbox-slack pods

**Do not add:** GHCR, CI, infra repo in this phase.

---

## Phase 3 — CI + registry (when prod cluster exists)

**One GitHub Action** on `overlay/**`:

```text
lint + test → docker build → push ghcr.io/<org>/centaur-overlay:sha-$GITHUB_SHA
```

**Do not add:** Matrix builds, multi-arch, Image Updater, until needed.

---

## Phase 4 — Infra repo skeleton (prod gate, not laptop)

Add `infra/` (or sibling repo) with **only**:

- Argo Application manifest
- `centaur.yaml` values pinning:
  - Centaur chart `targetRevision` = submodule SHA
  - `api` / `sandbox` / `slackbot` / `ironProxy` image tags
  - `overlay.image.repository` + `overlay.image.tag`
  - `ironProxy.secretSource: onepassword-connect` (or your prod secret backend)
  - `pluginWatcherEnabled: false` (prod)
  - `warmPoolEnabled: true` (if Slack latency matters)

**Delete from prod path:** shell `.env` bootstrap, `:latest`, centaur-lab `pullPolicy` hacks.

**Laptop unchanged:** still `just up` with local values files; no Argo locally.

---

## Explicit non-goals

- Host bind-mount overlay (chart fork)
- `POST /admin/reload-tools` as overlay deploy path
- Relying on plugin watcher for laptop file saves (copy model breaks this)
- Splitting overlay into its own repo before Phase 4
- `values.prod.yaml` in centaur-lab before infra exists
- Documenting bind-mount “dev mode”

---

## Success criteria

| Check | How |
|-------|-----|
| Overlay change reaches API tools | `kubectl exec … ls /app/overlay/org/tools`; logs show `semantic_scholar` loaded |
| Deploy rolls on overlay change | Change overlay → build → deploy → new pod without manual rollout |
| Skills refresh | After skill edit: delete slack sandboxes OR new thread |
| Config is maintainable | `values.local.yaml` < 30 lines; org knobs in one place |
| Prod path documented | Phase 4 infra pins sha tags; no `:latest` |

---

## Order of work

1. Phase 1 — values split (safe, immediate cleanup)
2. Phase 2 — sha tag + deploy (aligns with Centaur; may shrink reload)
3. Phase 3 — CI when you have GHCR + prod cluster
4. Phase 4 — infra/ when you deploy prod

**Stop after Phase 2** if laptop dev feels good and prod is months away.
