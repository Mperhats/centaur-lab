# centaur-lab MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up `centaur-lab` so that `just up` then `just smoke` returns `PONG` from Claude Code on a local Kubernetes cluster, with no Slack, no overlay, no `infra/`, and no CI.

**Architecture:** Monorepo with the smallest possible local-deploy surface. Base `paradigmxyz/centaur` is consumed as a git submodule pinned at a specific commit SHA at `.centaur/`. We own only: one Helm values overlay (`values.local.yaml`), a passthrough `Justfile`, `.env.example`, `.gitignore`, and an MVP-focused `README.md`. Slackbot is disabled in chart values; `ironProxy.secretSource: env`; default harness override switches Codex → Claude Code.

**Tech Stack:** git (submodule pinning), Helm 3, Kubernetes (local cluster — Docker Desktop, kind, k3d, or minikube), Bash, Just, Anthropic API.

**Spec:** `docs/superpowers/specs/2026-05-25-centaur-lab-mvp-design.md`

**Branch:** `mvp-local-smoke-test` (already created; reference docs and spec already committed at `fb6f26e` and `11e9bec`).

---

## File Structure

| File | Status | Responsibility |
|------|--------|----------------|
| `.gitignore` | Create | Ignore `.env`, common Python/Node detritus, IDE files. Allow `.env.example`. |
| `.gitmodules` | Create (via `git submodule add`) | Pin `paradigmxyz/centaur` at `.centaur/`. |
| `.centaur/` | Submodule (created by git, never edited from this repo) | Vendored base platform pinned at a specific SHA. |
| `.env.example` | Create | Template for shell env vars: one real Anthropic key + four random hex placeholders. |
| `values.local.yaml` | Create | The ONLY chart customization: env-var secret source, Claude Code default, Slackbot disabled, warm pool off. |
| `Justfile` | Create | Thin recipe wrappers; delegates `build`/`bootstrap-secrets`/`smoke`/`status`/`logs` to `.centaur/Justfile`; owns the `up`/`down` recipes that layer in `values.local.yaml`. |
| `README.md` | Modify | Replace the centaur.run-mirror tagline with a one-page MVP onboarding guide. |
| `docs/superpowers/specs/mvp-verification-log.md` | Create (operator-driven, Task 7) | Records the human-run E2E verification result: pinned SHA, captured smoke output, observed gotchas, follow-up action items. |

Each file has one responsibility. No file does double duty. Each task changes one of these files (with `git submodule add` as a special case where Git itself touches `.gitmodules` + `.centaur/`).

**Order matters:** `.gitignore` first (so `.env` cannot leak the moment a contributor creates one), then submodule (so `helm` calls in subsequent tasks can resolve the chart at `.centaur/contrib/chart`), then `.env.example`, then `values.local.yaml`, then `Justfile`, then `README.md`, then operator E2E verification.

---

## Task 1: `.gitignore`

**Files:**
- Create: `.gitignore`

**Why first:** Prevents the very first contributor mistake — committing a `.env` containing a real `ANTHROPIC_API_KEY` — from being possible the moment someone does `cp .env.example .env`.

- [ ] **Step 1: Define the verification command**

The "test" for a `.gitignore` task is: a freshly-created `.env` does not appear in `git status`, while `.env.example` does. Run from the repo root:

```bash
( touch .env .env.example
  result_env=$(git status --porcelain .env)
  result_example=$(git status --porcelain .env.example)
  rm -f .env .env.example
  echo "env-status='${result_env}'"
  echo "example-status='${result_example}'"
  if [ -z "${result_env}" ] && [ -n "${result_example}" ]; then
    echo PASS
  else
    echo FAIL
  fi )
```

- [ ] **Step 2: Run the verification before creating `.gitignore` to confirm it fails**

Run the command from Step 1.

Expected output (FAIL because nothing is ignored yet):

```
env-status='?? .env'
example-status='?? .env.example'
FAIL
```

- [ ] **Step 3: Create `.gitignore`**

Create `.gitignore` with exactly this content:

```gitignore
# Local secrets — NEVER commit
.env
.env.local
.env.*.local
!.env.example

# Python detritus (in case anything escapes the .centaur/ submodule)
__pycache__/
*.py[cod]
.venv/
venv/

# Node detritus
node_modules/

# Editor / OS
.idea/
.vscode/
*.swp
.DS_Store
```

- [ ] **Step 4: Run the verification again to confirm it passes**

Run the command from Step 1.

Expected output (PASS — `.env` is suppressed, `.env.example` is allowed through the `!` exception):

```
env-status=''
example-status='?? .env.example'
PASS
```

- [ ] **Step 5: Commit**

```bash
git add .gitignore
git commit -m "chore: ignore .env and standard editor/build artifacts"
```

---

## Task 2: pin `paradigmxyz/centaur` as a git submodule at `.centaur/`

**Files:**
- Create: `.gitmodules`
- Create: `.centaur/` (submodule directory; populated by git, never edited by hand)

**Why this approach:** The spec calls for bit-exact reproducibility between dev and (future) prod. Production GitOps will pin the chart at a commit SHA in Argo CD; a local submodule mirrors that pattern. Onboarding cost is one extra `git submodule update --init --recursive` command.

**Prerequisite check (one-time, abort the task if it fails):** the implementer must have either `gh` (GitHub CLI, authenticated) or network access to `git ls-remote https://github.com/paradigmxyz/centaur` so we can resolve a real SHA. If neither is available, surface this as a BLOCKED status to the human; do not invent a SHA.

- [ ] **Step 1: Resolve the SHA of `paradigmxyz/centaur`'s `main` HEAD**

Run:

```bash
CENTAUR_SHA=$(gh api repos/paradigmxyz/centaur/commits/main --jq '.sha' 2>/dev/null \
  || git ls-remote https://github.com/paradigmxyz/centaur.git refs/heads/main | awk '{print $1}')
echo "Pinning at: ${CENTAUR_SHA}"
```

Expected: a 40-character hex SHA prints. If `CENTAUR_SHA` is empty, do not proceed — report BLOCKED.

- [ ] **Step 2: Define the verification commands**

Two assertions: `.centaur/Justfile` exists, and `.centaur/contrib/chart/Chart.yaml` exists. Run from the repo root:

```bash
( [ -f .centaur/Justfile ] && [ -f .centaur/contrib/chart/Chart.yaml ] \
    && echo PASS || echo FAIL )
```

- [ ] **Step 3: Run the verification before adding the submodule to confirm it fails**

Run the command from Step 2.

Expected output (FAIL because `.centaur/` does not exist yet):

```
FAIL
```

- [ ] **Step 4: Add the submodule and pin it to the resolved SHA**

```bash
git submodule add https://github.com/paradigmxyz/centaur.git .centaur
git -C .centaur fetch --depth=1 origin "${CENTAUR_SHA}"
git -C .centaur checkout "${CENTAUR_SHA}"
```

If `git submodule add` fails because `.centaur/` already exists from a partial run, clean up with `git submodule deinit -f .centaur && git rm -f .centaur && rm -rf .git/modules/.centaur` before retrying.

- [ ] **Step 5: Run the verification again to confirm it passes**

Run the command from Step 2.

Expected output:

```
PASS
```

Also run a sanity grep — the chart values file should mention `ironProxy` and `slackbot` (these are the keys we override in `values.local.yaml` later):

```bash
grep -E '^(ironProxy|slackbot|api):' .centaur/contrib/chart/values.yaml
```

Expected: at minimum, lines for `api:`, `ironProxy:`, and `slackbot:` print. If any are missing, the pinned SHA is too old or has a different chart structure — report DONE_WITH_CONCERNS and surface the discrepancy. Do not proceed to Task 4 without resolving.

- [ ] **Step 6: Commit**

```bash
git add .gitmodules .centaur
git commit -m "$(cat <<EOF
chore: pin paradigmxyz/centaur at ${CENTAUR_SHA::12} as .centaur/ submodule

Adds the upstream Centaur platform as a git submodule pinned to a specific
commit SHA. The submodule is consumed by .centaur/Justfile recipes (build,
bootstrap-secrets, smoke, status, logs) and the Helm chart at
.centaur/contrib/chart. Bumping the SHA is a deliberate PR; this commit is
the initial pin.
EOF
)"
```

---

## Task 3: `.env.example`

**Files:**
- Create: `.env.example`

**Why:** This is the contract for shell env vars that `just bootstrap-secrets` (defined in `.centaur/Justfile`) will read into the `centaur-infra-env` Kubernetes Secret. The `export` prefix is non-cosmetic — bare `KEY=value` lines do not appear in `kubectl create secret`'s view of the shell environment.

- [ ] **Step 1: Define the verification command**

Three assertions: file exists, has all five expected variables, every line that defines one of those five has an `export ` prefix. Run from the repo root:

```bash
( [ -f .env.example ] || { echo FAIL: missing-file; exit 0; }
  required="ANTHROPIC_API_KEY SLACK_SIGNING_SECRET SLACKBOT_API_KEY SANDBOX_SIGNING_KEY IRON_MANAGEMENT_API_KEY"
  ok=true
  for key in $required; do
    if ! grep -qE "^export ${key}=" .env.example; then
      echo "FAIL: missing-export ${key}"
      ok=false
    fi
  done
  $ok && echo PASS )
```

- [ ] **Step 2: Run the verification before creating the file to confirm it fails**

Run the command from Step 1.

Expected output:

```
FAIL: missing-file
```

- [ ] **Step 3: Create `.env.example`**

Create `.env.example` with exactly this content:

```bash
# centaur-lab MVP environment variables.
#
# Onboarding:
#   1. cp .env.example .env
#   2. Replace the ANTHROPIC_API_KEY placeholder with a real Anthropic key.
#   3. For each "replace-with-random-hex", run `openssl rand -hex 32` and paste
#      the output. Generate ONCE per checkout and keep stable in your local
#      .env so values survive `just up` cycles. SANDBOX_SIGNING_KEY in
#      particular must persist across API restarts (per upstream docs).
#   4. source .env
#   5. just up
#
# The `export` prefix matters: `just bootstrap-secrets` (delegated to
# .centaur/Justfile) reads these via `kubectl create secret --from-literal`,
# which only sees variables that have been exported. Bare KEY=value would not
# satisfy it.

# Real credential — the only one you actually fill in.
export ANTHROPIC_API_KEY=sk-ant-replace-me

# Required by the API at boot but unused while Slackbot is disabled.
# Random hex is fine for milestone 1.
export SLACK_SIGNING_SECRET=replace-with-random-hex
export SLACKBOT_API_KEY=replace-with-random-hex

# Required by sandbox + iron-proxy. Generate once and keep stable.
export SANDBOX_SIGNING_KEY=replace-with-random-hex
export IRON_MANAGEMENT_API_KEY=replace-with-random-hex
```

- [ ] **Step 4: Run the verification again to confirm it passes**

Run the command from Step 1.

Expected output:

```
PASS
```

- [ ] **Step 5: Confirm `.env.example` is NOT ignored (Task 1 invariant still holds)**

```bash
git check-ignore -v .env.example && echo FAIL || echo PASS
```

Expected output:

```
PASS
```

(`git check-ignore` exits 0 when the file IS ignored, and non-zero when it is NOT — the inverted check makes the human-readable output match the rest of the plan.)

- [ ] **Step 6: Commit**

```bash
git add .env.example
git commit -m "feat: add .env.example template for local secret bootstrapping"
```

---

## Task 4: `values.local.yaml`

**Files:**
- Create: `values.local.yaml`

**Why:** This is the only chart customization the MVP owns. Everything else lives in the upstream chart. Four overrides: env-var secret source, Claude Code default harness, warm pool off (laptop resources), Slackbot disabled (no Slack surface in MVP).

**Important risk to verify:** the spec flags that `api.defaultHarness` may or may not be a real chart key at the pinned SHA. The verification step explicitly checks the rendered manifests for `claude-code` and BLOCKS if the override silently no-ops.

- [ ] **Step 1: Define the verification command**

Render the chart with `values.dev.yaml` + our `values.local.yaml` overlay using `helm template` (no live cluster needed). Then assert four things on the rendered output:

1. `helm template` succeeds (no template errors).
2. No Kubernetes object whose `metadata.name` contains `slackbot` is rendered (Slackbot is disabled).
3. The string `claude-code` appears at least once (harness override is wired through).
4. The string `env` appears as a value for some `secretSource`-related key (iron-proxy is in env mode).

Run from the repo root:

```bash
( set -e
  rendered=$(helm template centaur-mvp-verify .centaur/contrib/chart \
      -f .centaur/contrib/chart/values.dev.yaml \
      -f values.local.yaml 2>/tmp/helm.err) \
    || { echo "FAIL: helm-template-failed"; cat /tmp/helm.err; exit 0; }

  if echo "$rendered" | grep -E '^\s*name:\s*\S*slackbot' >/dev/null; then
    echo "FAIL: slackbot-still-present"; exit 0
  fi

  if ! echo "$rendered" | grep -q 'claude-code'; then
    echo "FAIL: claude-code-not-rendered"; exit 0
  fi

  if ! echo "$rendered" | grep -E "secretSource[^A-Za-z]+env|FIREWALL_MANAGER_SECRET_SOURCE.*env" >/dev/null; then
    echo "FAIL: iron-proxy-not-env-mode"; exit 0
  fi

  echo PASS )
```

- [ ] **Step 2: Run the verification before creating the file to confirm it fails**

Run the command from Step 1.

Expected output:

```
FAIL: helm-template-failed
```

(or any FAIL message — the file doesn't exist, so `helm template` will error on missing values.local.yaml)

- [ ] **Step 3: Create `values.local.yaml`**

Create `values.local.yaml` with exactly this content:

```yaml
# centaur-lab MVP — the ONLY chart customization layered on top of
# .centaur/contrib/chart/values.dev.yaml. Keep this file minimal; every
# additional knob is one more thing that can drift across upstream bumps.

ironProxy:
  # Resolve credentials from the centaur-infra-env Kubernetes Secret
  # directly. No 1Password Connect or service account.
  secretSource: env

api:
  # Make `just smoke` and any future bare `@bot ...` mention use Claude Code
  # by default (chart default would be Codex, which we have no key for).
  defaultHarness: claude-code

  # Save laptop CPU/memory; sandboxes spawn cold per turn. Acceptable for
  # smoke and early development. Re-enable in production values.
  warmPoolEnabled: false

slackbot:
  # No Slack ingress in milestone 1. Drops the dependency on SLACK_BOT_TOKEN
  # and a public webhook URL. Re-enable when M2 lands.
  enabled: false
```

- [ ] **Step 4: Run the verification again to confirm it passes**

Run the command from Step 1.

Expected output:

```
PASS
```

**If the output is `FAIL: claude-code-not-rendered`:** the pinned `paradigmxyz/centaur` SHA does not expose `api.defaultHarness` as the chart key. Do NOT attempt to guess another key name and do NOT commit the file in this state. Stop, report `BLOCKED`, and surface the discrepancy to the human; resolution is one of: (a) bump the pinned SHA in Task 2 to a chart that supports it, (b) discover the actual chart key by reading `.centaur/contrib/chart/templates/api-deployment.yaml` and updating this file plus the spec's `values.local.yaml` block, or (c) accept the chart default and document `--claude` as the manual override in the README — this is a human decision, not an implementer one.

**If the output is `FAIL: slackbot-still-present`:** the chart key `slackbot.enabled` does not gate the Slackbot Deployment at the pinned SHA. Do not commit. Report `BLOCKED` with the same human-decision protocol as above.

**If the output is `FAIL: iron-proxy-not-env-mode`:** the iron-proxy chart template did not pick up `ironProxy.secretSource: env`. Do not commit. Report `BLOCKED` with the same protocol.

- [ ] **Step 5: Commit**

```bash
git add values.local.yaml
git commit -m "$(cat <<'EOF'
feat: add values.local.yaml chart overlay for MVP local deploy

Four overrides on top of .centaur/contrib/chart/values.dev.yaml:
- ironProxy.secretSource: env  (no 1Password)
- api.defaultHarness: claude-code  (drops OPENAI_API_KEY dependency)
- api.warmPoolEnabled: false  (laptop resource budget)
- slackbot.enabled: false  (no Slack surface in MVP)

This is the entirety of what centaur-lab owns at the chart layer.
EOF
)"
```

---

## Task 5: root `Justfile`

**Files:**
- Create: `Justfile`

**Why:** A thin shell over `.centaur/Justfile`. We do not reimplement `build`, `bootstrap-secrets`, `smoke`, `status`, or `logs` — those live upstream and we get their fixes for free as we bump the pinned SHA. The only thing we own is the `up` recipe, where we layer `values.local.yaml` on top of `values.dev.yaml`, and the `down` recipe.

**`just` recipe semantics note for the implementer:** by default `just` runs each line of a recipe in a *new* shell, so `cd .centaur` on its own line does not persist. Always combine `cd` with the next command using `&&` on the same logical line (continuation with `\` is fine).

- [ ] **Step 1: Define the verification command**

Three assertions: file exists, `just --list` resolves cleanly and includes the expected recipe set, and `just --evaluate` reports no syntax errors. Run from the repo root:

```bash
( [ -f Justfile ] || { echo "FAIL: missing-file"; exit 0; }
  list=$(just --list 2>/tmp/just.err) || { echo "FAIL: just-list-failed"; cat /tmp/just.err; exit 0; }
  required="up bootstrap-secrets smoke status logs down"
  ok=true
  for recipe in $required; do
    if ! echo "$list" | grep -qE "^\s+${recipe}( |$)"; then
      echo "FAIL: missing-recipe ${recipe}"; ok=false
    fi
  done
  just --evaluate >/dev/null 2>/tmp/just.err || { echo "FAIL: just-evaluate-failed"; cat /tmp/just.err; exit 0; }
  $ok && echo PASS )
```

- [ ] **Step 2: Run the verification before creating the file to confirm it fails**

Run the command from Step 1.

Expected output:

```
FAIL: missing-file
```

- [ ] **Step 3: Create `Justfile`**

Create `Justfile` with exactly this content:

```just
# centaur-lab MVP Justfile.
#
# Thin wrapper over .centaur/Justfile. The only recipes we own are `up`
# (which layers values.local.yaml on top of values.dev.yaml) and `down`.
# Everything else is a passthrough so we inherit upstream fixes when we
# bump the pinned SHA in Task 2.

# Default action when running bare `just`.
default: up

# Bootstrap secrets, build images, and deploy the chart with our overlay.
up: bootstrap-secrets
    cd .centaur && just build
    cd .centaur && helm upgrade --install centaur contrib/chart \
        --namespace centaur-system --create-namespace \
        -f contrib/chart/values.dev.yaml \
        -f ../values.local.yaml

# Create the centaur-infra-env Kubernetes Secret from your shell env.
# Requires: source .env first.
bootstrap-secrets:
    cd .centaur && just bootstrap-secrets

# Run the upstream smoke test (spawn -> message -> execute -> poll for PONG).
smoke:
    cd .centaur && just smoke

# Show pod / deployment status across the centaur namespace.
status:
    cd .centaur && just status

# Tail logs for a single component (api, iron-proxy, postgres, ...).
logs target="api":
    cd .centaur && just logs {{target}}

# Uninstall the chart but leave the namespace (next `just up` is then a
# clean re-install). Use `kubectl delete namespace centaur-system` for the
# nuke option.
down:
    helm uninstall centaur --namespace centaur-system
```

- [ ] **Step 4: Run the verification again to confirm it passes**

Run the command from Step 1.

Expected output:

```
PASS
```

- [ ] **Step 5: Confirm `just --list` looks human-friendly**

Run:

```bash
just --list
```

Expected output (recipe order may vary; the set must match):

```
Available recipes:
    bootstrap-secrets
    default
    down
    logs target="api"
    smoke
    status
    up
```

If a contributor sees this output and does not know what to type to boot the stack, the README in Task 6 fills that gap.

- [ ] **Step 6: Commit**

```bash
git add Justfile
git commit -m "$(cat <<'EOF'
feat: add root Justfile with passthrough + up/down recipes

`just up` bootstraps the centaur-infra-env Secret, builds upstream images,
and deploys the chart with values.local.yaml layered on values.dev.yaml.
`just down` uninstalls the chart but leaves the namespace.

All other recipes (bootstrap-secrets, smoke, status, logs) are passthroughs
to .centaur/Justfile so we inherit upstream behavior on SHA bumps.
EOF
)"
```

---

## Task 6: rewrite `README.md` as MVP onboarding guide

**Files:**
- Modify: `README.md` (currently 9 lines mirroring the centaur.run tagline; replace entirely)

**Why:** `git status` is clean and recipes are listed, but a fresh contributor still needs a one-page "what is this and how do I boot it" entry point. The README is the *only* in-repo onboarding artifact for the MVP.

- [ ] **Step 1: Define the verification command**

Six assertions matching the six Definition-of-Done items in the spec — each onboarding step from "submodule init" through "smoke returns PONG" must be findable in the README. Run from the repo root:

```bash
( [ -f README.md ] || { echo "FAIL: missing-file"; exit 0; }
  required_substrings=(
    "git submodule update --init --recursive"
    "cp .env.example .env"
    "openssl rand -hex 32"
    "source .env"
    "just up"
    "just smoke"
    "PONG"
    "claude-code"
  )
  ok=true
  for needle in "${required_substrings[@]}"; do
    if ! grep -qF -- "$needle" README.md; then
      echo "FAIL: missing-text \"$needle\""; ok=false
    fi
  done
  $ok && echo PASS )
```

- [ ] **Step 2: Run the verification before rewriting to confirm it fails**

Run the command from Step 1.

Expected output (the existing README is the centaur.run tagline mirror; none of the onboarding strings are there):

```
FAIL: missing-text "git submodule update --init --recursive"
FAIL: missing-text "cp .env.example .env"
FAIL: missing-text "openssl rand -hex 32"
FAIL: missing-text "source .env"
FAIL: missing-text "just up"
FAIL: missing-text "just smoke"
FAIL: missing-text "PONG"
FAIL: missing-text "claude-code"
```

- [ ] **Step 3: Rewrite `README.md`**

Replace the entire file with exactly this content:

````markdown
# centaur-lab

Local-first onboarding for [Centaur](https://github.com/paradigmxyz/centaur),
the production control plane for shared AI agents. This repo's milestone 1
goal is intentionally minimal: get a Claude Code agent to reply with `PONG`
through Centaur's durable agent API, on your laptop, with no Slack, no
overlay image, and no production GitOps.

The full design rationale lives in
[`docs/superpowers/specs/2026-05-25-centaur-lab-mvp-design.md`](docs/superpowers/specs/2026-05-25-centaur-lab-mvp-design.md).

## What this repo contains

| Path | Purpose |
|------|---------|
| `.centaur/` | Git submodule pinned at a specific `paradigmxyz/centaur` SHA. The base platform. |
| `values.local.yaml` | The only Helm chart customization: env-var secrets, Claude Code default, Slackbot disabled. |
| `Justfile` | Thin wrapper over `.centaur/Justfile`. `just up`, `just smoke`, `just down`. |
| `.env.example` | Template for the five shell env vars `bootstrap-secrets` reads. |
| `docs/centaur/` | Offline mirror of centaur.run reference docs. |
| `docs/superpowers/` | This repo's spec and implementation plan. |

## Prerequisites

- macOS or Linux
- Docker
- A local Kubernetes cluster reachable from `kubectl`. Docker Desktop with
  Kubernetes enabled is the simplest path; `kind`, `k3d`, and `minikube` also
  work — the upstream chart targets generic local k8s.
- `brew install just kubectl helm jq` (or your distro's equivalents)
- An Anthropic API key (Claude Code is the default harness)

## Setup

1. **Clone with submodules.**

   ```bash
   git clone https://github.com/<your-org>/centaur-lab
   cd centaur-lab
   git submodule update --init --recursive
   ```

   If you forget the submodule init, `just up` will fail with a missing
   `.centaur/Justfile` error.

2. **Create your local `.env`.**

   ```bash
   cp .env.example .env
   ```

   Fill in `ANTHROPIC_API_KEY` with a real Anthropic key. For each
   `replace-with-random-hex` placeholder, run `openssl rand -hex 32` and
   paste the output. **Generate these once and keep them stable** —
   regenerating `SANDBOX_SIGNING_KEY` between `just up` cycles breaks
   sandbox-signed tokens (per upstream docs).

3. **Source the env so the variables are exported into your shell.**

   ```bash
   source .env
   ```

   The `export` prefix on each line in `.env.example` matters — `just
   bootstrap-secrets` reads from your exported shell environment via
   `kubectl create secret --from-literal`.

## Boot the stack

```bash
just up
```

This runs in order:

1. `bootstrap-secrets` — creates the `centaur-infra-env` Kubernetes Secret
   in the `centaur-system` namespace from your shell env.
2. `build` — builds the upstream `centaur-api`, `centaur-iron-proxy`, and
   `centaur-agent` Docker images.
3. `helm upgrade --install` — deploys the chart with
   `.centaur/contrib/chart/values.dev.yaml` plus our `values.local.yaml`
   overlay.

Verify the pods are healthy:

```bash
just status
# or:
kubectl get pods -n centaur-system
```

Expected: `centaur-centaur-api`, `centaur-iron-proxy`, and Postgres pods are
running. **There is no Slackbot pod by design** — the MVP disables Slackbot
in `values.local.yaml`.

## Run the smoke test

```bash
just smoke
```

Expected (final JSON shape):

```json
{
  "status": "completed",
  "result_text": "...PONG..."
}
```

To confirm Claude Code (not the chart's Codex default) was the harness used:

```bash
helm get values centaur -n centaur-system | grep defaultHarness
# expected:
#   defaultHarness: claude-code
```

## Tear down

```bash
just down
```

This uninstalls the Helm release but leaves the `centaur-system` namespace
intact, so the next `just up` is a clean re-install. To fully remove:

```bash
kubectl delete namespace centaur-system
```

## Troubleshooting

| Symptom | What to check |
|---------|---------------|
| `just up` fails with "Justfile not found" inside `.centaur/` | Run `git submodule update --init --recursive`. |
| `bootstrap-secrets` complains about missing variables | Did you `source .env`? Did you fill in `ANTHROPIC_API_KEY`? |
| Pods crash-loop with `OOMKilled` | Local cluster is too small. Bump CPU/memory in Docker Desktop or kind config. |
| Smoke test never completes | `just logs api` for the API container; `kubectl get pods -n centaur-system -l centaur.ai/managed=true` for sandbox state. |
| `helm get values` does not show `defaultHarness` | The pinned base SHA may not expose the key yet — see [open question 2 in the spec](docs/superpowers/specs/2026-05-25-centaur-lab-mvp-design.md#open-questions-for-implementation). Pass `--claude` manually in the smoke prompt as a workaround. |

## What this repo intentionally does NOT contain (yet)

| Future milestone | What it adds |
|------------------|--------------|
| M2: Slack | Re-enable Slackbot, add `cloudflared` tunnel, document Slack app setup. |
| M3: Overlay | Add `overlay/` with one tool/skill/workflow + image build. |
| M4: First real use case | Slack ETL on, plus a thin retrieval tool. |
| M5: Production infra | `infra/` Argo CD bootstrap pinned at the same chart SHA. |
| M6: CI | Path-scoped GitHub Actions for overlay/infra changes. |
| M7: Pi Labs | Either swap default harness to `pi-mono` or wire pi.dev RPC SDK as a tool. |

Each is one focused PR away on top of the MVP. See the spec for the full
deferred-work table.

## License

See [`LICENSE`](LICENSE).
````

- [ ] **Step 4: Run the verification again to confirm it passes**

Run the command from Step 1.

Expected output:

```
PASS
```

- [ ] **Step 5: Sanity-read the rendered file**

```bash
wc -l README.md
```

Expected: 100–200 lines. If significantly larger, the README has bloated past "one page" and should be trimmed before commit.

- [ ] **Step 6: Commit**

```bash
git add README.md
git commit -m "$(cat <<'EOF'
docs: rewrite README as MVP onboarding guide

Replaces the centaur.run-tagline mirror with a one-page guide covering:
- Prerequisites (Docker, local k8s, just/kubectl/helm/jq, Anthropic key)
- Submodule-aware clone
- .env onboarding flow (cp, fill, source)
- just up / just smoke happy path
- Verification commands matching the spec's Definition of Done
- Troubleshooting table
- Pointer to the deferred-milestones table in the spec
EOF
)"
```

---

## Task 7: operator E2E verification + verification log

**Files:**
- Create: `docs/superpowers/specs/mvp-verification-log.md`

**Important — read before dispatching this task:** Tasks 1–6 produce verifiable file artifacts that an implementer subagent can validate via shell commands. Task 7 is different: it requires a live local Kubernetes cluster, a real Anthropic API key, and Docker — none of which a subagent has access to. **Dispatch this task to the human operator, not to a subagent.** The subagent-driven-development controller should mark Tasks 1–6 complete, then hand off to the human with the runbook below. The human runs through it on their laptop and reports back PASS/FAIL plus the captured output.

**Goal of this task:** prove the spec's Definition of Done items #1–#5 actually pass on a real machine, capture the result (including the pinned SHA, any gotchas, and the smoke-test output), commit the verification log, and merge the branch.

- [ ] **Step 1: Confirm prerequisites are installed**

```bash
which docker just kubectl helm jq openssl
docker info >/dev/null 2>&1 && echo "docker: PASS" || echo "docker: FAIL"
kubectl config current-context
kubectl get nodes
```

Expected: all binaries print a path; `docker info` prints PASS; `kubectl get nodes` lists at least one Ready node from your local cluster (Docker Desktop / kind / k3d / minikube).

- [ ] **Step 2: Verify clean checkout state**

From a fresh clone (not a workspace where Tasks 1–6 ran in-place):

```bash
git clone <your-fork-of-centaur-lab>
cd centaur-lab
git submodule update --init --recursive
[ -f .centaur/Justfile ] && echo "submodule: PASS" || echo "submodule: FAIL"
```

Expected: `submodule: PASS`.

- [ ] **Step 3: Onboard `.env`**

```bash
cp .env.example .env
# edit .env: set a real ANTHROPIC_API_KEY
# generate stable random hex once for each placeholder:
for var in SLACK_SIGNING_SECRET SLACKBOT_API_KEY SANDBOX_SIGNING_KEY IRON_MANAGEMENT_API_KEY; do
  hex=$(openssl rand -hex 32)
  # paste $hex into .env replacing the corresponding placeholder
  echo "$var=$hex"
done
source .env
```

Expected: every required variable is set in your shell:

```bash
for var in ANTHROPIC_API_KEY SLACK_SIGNING_SECRET SLACKBOT_API_KEY SANDBOX_SIGNING_KEY IRON_MANAGEMENT_API_KEY; do
  if [ -z "${!var}" ]; then echo "$var: MISSING"; else echo "$var: set"; fi
done
```

Expected: every var prints `: set`. None should print `MISSING`.

- [ ] **Step 4: `just up` — boot the stack**

```bash
just up
```

Expected: completes without errors after pulling/building images. Then:

```bash
just status
kubectl get pods -n centaur-system
```

Expected: pods for `centaur-centaur-api`, `centaur-iron-proxy`, and Postgres are `Running` (or `ContainerCreating` briefly). **No `centaur-slackbot` pod** — by design.

If a pod is in `CrashLoopBackOff` or `Error`, run `just logs api` (or substitute the failing component) and investigate before proceeding. Most common failures at this stage: missing env var (re-source `.env`), Docker build OOM (increase Docker resource limits), or chart key mismatch (see Task 4 BLOCKED protocol).

- [ ] **Step 5: Health probe**

```bash
kubectl exec -n centaur-system deploy/centaur-centaur-api -- curl -fsS http://localhost:8000/health
```

Expected:

```
{"status":"ok"}
```

- [ ] **Step 6: Confirm Claude Code is the wired default harness**

```bash
helm get values centaur -n centaur-system | grep defaultHarness
```

Expected:

```
  defaultHarness: claude-code
```

If this line is absent, the chart did not pick up `api.defaultHarness` — see Task 4 BLOCKED protocol. **Stop here** until that is resolved; running smoke without the override would fall back to the chart's Codex default and fail with "missing OPENAI_API_KEY."

- [ ] **Step 7: Run the smoke test and capture the result**

```bash
just smoke 2>&1 | tee /tmp/centaur-lab-smoke.json
```

Expected: the captured JSON includes:

```json
"status": "completed"
```

and

```json
"result_text": "...PONG..."
```

Verify both with one command:

```bash
jq -r '.status, .result_text' /tmp/centaur-lab-smoke.json
```

Expected first line: `completed`. Expected second line: contains `PONG` (case may vary; the upstream smoke recipe is documented to look for it).

- [ ] **Step 8: Capture the verification log**

Create `docs/superpowers/specs/mvp-verification-log.md` with the following content (substitute the bracketed values with what you actually observed):

````markdown
# centaur-lab MVP — verification log

| Field | Value |
|-------|-------|
| Date verified | [YYYY-MM-DD] |
| Operator | [your name / handle] |
| Local cluster | [Docker Desktop / kind / k3d / minikube] + version |
| Pinned `paradigmxyz/centaur` SHA | [40-char SHA from `git -C .centaur rev-parse HEAD`] |
| Helm version | [`helm version --short`] |
| `kubectl` version | [`kubectl version --client --short`] |

## Spec DoD checklist

- [x] 1. `git submodule update --init --recursive` populated `.centaur/`
- [x] 2. `.env` onboarding completed; all 5 vars sourced
- [x] 3. `just up` succeeded; no `centaur-slackbot` pod
- [x] 4. `/health` returned `{"status":"ok"}`
- [x] 5. `just smoke` returned `"status": "completed"` with `PONG`
- [x] 6. README walked through the above in [time] minutes

## Captured smoke output

```json
[paste the JSON from /tmp/centaur-lab-smoke.json]
```

## Gotchas observed (if any)

[Free-form notes. Examples: "had to bump Docker memory to 6GB", "chart key
`api.defaultHarness` was named `api.harness` at this SHA — patched
values.local.yaml accordingly", "Postgres pod took 90s to become Ready on
first boot".]

## Action items

[Anything the verification surfaced that should become a follow-up issue or
PR. Empty list is fine.]
````

- [ ] **Step 9: Commit the verification log and merge the branch**

```bash
git add docs/superpowers/specs/mvp-verification-log.md
git commit -m "$(cat <<'EOF'
docs: capture MVP verification log

Records the operator E2E run-through of the milestone-1 Definition of Done.
Includes pinned SHA, captured smoke output, observed gotchas, and any
follow-up action items.
EOF
)"
```

Then merge `mvp-local-smoke-test` into `main` per your team's normal workflow.

- [ ] **Step 10: Tear down (optional, when you are done)**

```bash
just down
# or, to fully reset:
kubectl delete namespace centaur-system
```

---
