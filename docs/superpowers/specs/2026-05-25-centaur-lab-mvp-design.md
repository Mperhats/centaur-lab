---
title: centaur-lab MVP — local smoke test only
date: 2026-05-25
status: approved
owner: perhats
related_docs:
  - https://centaur.run/quickstart
  - https://centaur.run/architecture
  - https://centaur.run/deploying-in-production
  - https://centaur.run/secrets/environment
---

# centaur-lab MVP — local smoke test only

## Goal

Stand up a `centaur-lab` repository whose only deliverable is:

> Run `just up` then `just smoke` on a laptop, and get a Claude Code agent to
> reply with `PONG` through Centaur's durable agent API.

That is the entire milestone 1 acceptance criterion. Slack, overlays, infra,
GitOps, CI, and any organization-specific tools/workflows/skills are explicitly
deferred to later milestones.

## Why this scope

Earlier brainstorming considered:

- A Pi Labs-flavored deployment (pi-mono harness, or a custom wrapper around the
  pi.dev RPC SDK).
- A "full local" milestone that already mirrored production: Slack working in a
  test channel, an overlay image mounted, a placeholder tool/skill/workflow, and
  Argo CD bootstrap files in `infra/`.
- A first real use case (Slack ETL plus a `slack_history_search` retrieval tool).

We collapsed to the smaller scope on purpose. None of the deferred work is
blocked by milestone 1; all of it is cleanly added on top once the kernel is
known-good. Shipping a working MVP first removes the largest sources of
"is anything wrong yet?" noise (Slack app misconfiguration, overlay image
discovery, iron-proxy injection paths, GitOps reconciliation) so future
milestones get to debug one new surface at a time.

## Non-goals (milestone 1)

The following are intentionally out of scope:

- No Slack ingress. `slackbot.enabled: false` in chart values; no Slack app
  created; no public webhook URL.
- No overlay image. No `overlay/` directory in this repo. Tools, workflows,
  skills, sandbox prompt overrides — all deferred.
- No infra repo. No `infra/` directory, no Argo CD bootstrap manifests, no
  cluster values. Production deploy is a later milestone.
- No CI. No `.github/workflows/`. We do not build images on push, we do not run
  helm-lint, we do not rebuild an overlay we don't have.
- No 1Password. `ironProxy.secretSource: env` everywhere; credentials live in a
  single Kubernetes Secret rendered from a `.env` file.
- No pi-mono / pi.dev. Default harness is `claude-code`. Pi Labs integration is
  a future milestone (and is decoupled from the repo skeleton).

## Repository layout

```text
centaur-lab/
├── docs/                # reference docs mirrored from centaur.run; already present
├── .centaur/            # git submodule -> paradigmxyz/centaur, pinned at a SHA
├── values.local.yaml    # the ONLY chart customization for milestone 1
├── Justfile             # thin wrappers; delegates to .centaur/Justfile
├── .env.example         # 5 env vars (1 real credential, 4 random hex)
├── .gitignore           # ignores .env and standard Python/Node detritus
└── README.md            # one-page "how to boot the smoke test"
```

Rationale for what's *not* in the tree:

- No `overlay/`, `infra/`, or `.github/workflows/`. Carrying empty scaffolding
  ahead of need makes the repo look "in progress" and tempts contributors to
  fill it with placeholders that don't earn their keep.
- No vendored copy of `paradigmxyz/centaur`. We pin via git submodule; this
  mirrors the production pattern of pinning the chart to a commit SHA in
  Argo CD, so dev and (future) prod track the same exact base code.

## Base Centaur source: git submodule, pinned SHA

Add `paradigmxyz/centaur` as a submodule at `.centaur/`:

```bash
git submodule add https://github.com/paradigmxyz/centaur.git .centaur
cd .centaur && git checkout <SHA-of-known-good-main> && cd ..
git add .gitmodules .centaur && git commit -m "Pin centaur base at <SHA>"
```

Onboarding for new contributors:

```bash
git clone https://github.com/<your-org>/centaur-lab
cd centaur-lab
git submodule update --init --recursive
```

The pinned SHA is the only knob that decides which version of the base platform
we run. Bumping it is a deliberate PR.

**Open: which SHA to pin initially?** Default policy: pick the latest commit on
`paradigmxyz/centaur`'s `main` at the moment milestone 1 is implemented. Bumps
afterward are explicit PRs with a one-line "what changed upstream" note in the
commit body.

## `values.local.yaml` — the only chart customization

```yaml
ironProxy:
  secretSource: env

api:
  defaultHarness: claude-code
  warmPoolEnabled: false

slackbot:
  enabled: false
```

| Key | Why |
|-----|-----|
| `ironProxy.secretSource: env` | Resolve credentials from the `centaur-infra-env` Secret directly. No 1Password Connect or service account. |
| `api.defaultHarness: claude-code` | `just smoke` and any future bare `@bot ...` mention runs Claude Code by default, so we don't need `OPENAI_API_KEY` (the chart's default would otherwise pick Codex). |
| `api.warmPoolEnabled: false` | Save laptop CPU/memory; sandboxes spawn cold per turn. Acceptable for smoke and for early development. |
| `slackbot.enabled: false` | No Slack ingress in milestone 1. Drops the dependency on `SLACK_BOT_TOKEN` and a public webhook URL. |

We deploy by passing this file *after* the base dev values, so it overrides
without forking:

```bash
helm upgrade --install centaur .centaur/contrib/chart \
  --namespace centaur-system --create-namespace \
  -f .centaur/contrib/chart/values.dev.yaml \
  -f values.local.yaml
```

## Environment / secrets

The `centaur-infra-env` Kubernetes Secret holds everything. `just
bootstrap-secrets` (delegated to the base submodule's recipe) reads these from
your shell and creates the Secret.

`.env.example`:

```bash
# Real credential — the only one you actually fill in
export ANTHROPIC_API_KEY=sk-ant-...

# Required by the API at boot but unused while Slackbot is disabled.
# Random hex is fine for milestone 1.
export SLACK_SIGNING_SECRET=replace-with-random-hex
export SLACKBOT_API_KEY=replace-with-random-hex

# Required by sandbox + iron-proxy. Generate once and keep stable in your
# local .env so they survive `just up` cycles (especially SANDBOX_SIGNING_KEY,
# which the docs explicitly note must persist across API restarts).
export SANDBOX_SIGNING_KEY=replace-with-random-hex
export IRON_MANAGEMENT_API_KEY=replace-with-random-hex
```

`export` prefixes matter: the base submodule's `bootstrap-secrets` recipe
reads from your shell environment via `kubectl create secret --from-literal`,
which only sees variables that have been exported. Bare `KEY=value` would
not satisfy it.

Onboarding flow:

```bash
cp .env.example .env
# Fill ANTHROPIC_API_KEY with a real Anthropic key.
# For each `replace-with-random-hex`, run: openssl rand -hex 32
# and paste the output. Generate once, do not regenerate per session.
source .env
```

`.env` is git-ignored. We add it to `.gitignore` explicitly even though most
toolchains do that by default.

We do *not* set `SLACK_BOT_TOKEN`. With `slackbot.enabled: false`, the chart
does not require it.

We do *not* set `OPENAI_API_KEY`, `AMP_API_KEY`, or any other harness
credential. With `defaultHarness: claude-code` and no overlay tools, only
Anthropic is needed.

## `Justfile`

```just
default: up

up: bootstrap-secrets
    cd .centaur && just build
    cd .centaur && helm upgrade --install centaur contrib/chart \
        --namespace centaur-system --create-namespace \
        -f contrib/chart/values.dev.yaml \
        -f ../values.local.yaml

bootstrap-secrets:
    cd .centaur && just bootstrap-secrets

smoke:
    cd .centaur && just smoke

status:
    cd .centaur && just status

logs target="api":
    cd .centaur && just logs {{target}}

down:
    helm uninstall centaur --namespace centaur-system
```

The Justfile is intentionally a thin shell over the base submodule's recipes.
We do not reimplement `build`, `bootstrap-secrets`, `smoke`, `status`, or
`logs` — those exist upstream and we want their fixes for free as we bump the
pinned SHA. The only thing we own is the deploy step, where we layer
`values.local.yaml` on top of the base `values.dev.yaml`.

## Prerequisites (documented in README)

Hard requirements:

- macOS or Linux
- Docker
- A local Kubernetes cluster reachable from `kubectl` (Docker Desktop with
  Kubernetes enabled is the simplest; `kind`, `k3d`, and `minikube` are also
  fine — the upstream Justfile and chart support all of them)
- `brew install just kubectl helm jq`
- An Anthropic API key

We do not prescribe a specific local-cluster choice; we list the supported
options in the README and let the user pick. The chart's `values.dev.yaml`
already targets a generic local cluster.

## Definition of done

The MVP is complete when, on a fresh checkout:

1. `git submodule update --init --recursive` populates `.centaur/` at the
   pinned SHA without errors.
2. `cp .env.example .env`, fill `ANTHROPIC_API_KEY`, generate the four random
   hex values, then `source .env` succeeds.
3. `just up` completes without errors. `kubectl get pods -n centaur-system`
   shows `centaur-centaur-api`, `centaur-iron-proxy`, and Postgres healthy.
   No `centaur-slackbot` pod (by design).
4. `kubectl exec -n centaur-system deploy/centaur-centaur-api -- curl -fsS
   http://localhost:8000/health` returns `{"status":"ok"}`.
5. `just smoke` returns a payload whose JSON includes `"status": "completed"`
   and `"result_text"` containing `PONG`. To confirm Claude Code was the
   harness used (and not the chart's Codex default), inspect the rendered
   chart values with `helm get values centaur -n centaur-system | grep
   defaultHarness` and confirm `claude-code`. (We do not assume an
   undocumented `harness` field on the execution row; if such a field exists
   in the pinned base SHA, we can additionally check it during
   implementation.)
6. The README walks a new contributor through 1–5 in under 30 minutes given
   prerequisites are installed.

## What we explicitly defer

| Future milestone | What it adds | Why deferred |
|------------------|--------------|--------------|
| M2: Slack | Re-enable Slackbot, add `SLACK_BOT_TOKEN`, add `cloudflared` tunnel recipe, document Slack app setup. | Independent surface; works against an already-known-good kernel. |
| M3: Overlay | Add `overlay/` with one tool/skill/workflow. Build and load the overlay image. Update `values.local.yaml` to mount it. | Requires the kernel to be green so overlay-discovery failures are unambiguous. |
| M4: First real use case | Slack ETL on, plus a `slack_history_search` overlay tool reading `company_context_documents`. | Builds on M2 (Slack tokens) and M3 (overlay shape). |
| M5: Production infra | `infra/` directory with Argo CD bootstrap and prod values pointing at the same chart at the same pinned SHA. | Makes no sense before there is local-known-good behavior to deploy. |
| M6: CI | Path-scoped GitHub Actions for overlay image builds and infra validation. | Makes no sense before there is an `overlay/` and `infra/` to gate. |
| M7: Pi Labs | Either swap default harness to `pi-mono` (drop-in, already supported by the chart) or wire pi.dev RPC SDK as a tool/workflow capability. | Independent of the kernel; the original ambiguity (pi-mono CLI vs pi.dev RPC) gets re-decided when the use case is concrete. |

Each of these is one focused PR away on top of the MVP — none of them require
reshaping milestone 1's choices.

## Risks and assumptions

- **The base submodule SHA might bit-rot.** If the upstream chart or Justfile
  recipes change in a backward-incompatible way, our `values.local.yaml`
  overrides could stop working. Mitigation: bumps are explicit PRs; we read
  the upstream changelog before bumping; values keys are minimal so the
  surface area for breakage is small.
- **Docker Desktop's Kubernetes is not free for orgs > 250 employees.** If
  this becomes a blocker, the documented alternatives (`kind`, `k3d`,
  `minikube`) all work; the chart targets local k8s generically.
- **`SLACK_SIGNING_SECRET` and `SLACKBOT_API_KEY` are required at API boot
  even with Slackbot off.** This is a quirk of the current chart, documented
  in `quickstart.md`. We satisfy them with random hex placeholders. If a
  future chart change makes them strictly optional when `slackbot.enabled:
  false`, we remove them from `.env.example` at that bump.
- **`api.defaultHarness: claude-code` may not be a chart key in older base
  SHAs.** If the chosen pinned SHA doesn't expose this knob, we fall back to
  always passing `--claude` in the smoke prompt and document this in the
  README. We re-enable the override at the next chart-supporting bump.
  *(Action item for implementation: verify this knob exists at the SHA we
  pin before merging the MVP.)*
- **Submodule onboarding friction.** New contributors must remember to run
  `git submodule update --init --recursive`. Mitigation: README documents
  this prominently; `Justfile`'s `up` recipe could optionally check that
  `.centaur/Justfile` exists and instruct on the missing init step otherwise.

## Open questions for implementation

1. Pin SHA: which `paradigmxyz/centaur` commit do we pin at? Pick at
   implementation time; record the chosen SHA in the README.
2. Verify `api.defaultHarness: claude-code` is exposed at the pinned SHA.
3. Decide whether `Justfile`'s `up` recipe should pre-flight check that the
   submodule is initialized, or whether the README is sufficient.
4. Decide whether `down` should also delete the namespace. Default proposal:
   keep namespace so `helm upgrade --install` on next `up` is a no-op
   namespace-wise; explicit `kubectl delete namespace centaur-system` is the
   nuke option.

These are answered during implementation, not now.
