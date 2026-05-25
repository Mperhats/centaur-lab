# Slack-to-PR Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire `centaur-lab` so a Slack user can ask Centaur to scaffold a new overlay tool, and the sandbox agent autonomously branches, commits, pushes, and opens a PR against `Mperhats/centaur-lab` — closing the feedback loop from "@centaur scaffold a polygon tool" to a reviewable PR with one human approval and one `just up` away from live.

**Architecture:** Enable the chart's `repoCache` DaemonSet to maintain a read-only mirror of `Mperhats/centaur-lab` on a node-local `hostPath`. Mount that path into every sandbox pod at `/home/agent/github/Mperhats/centaur-lab` by setting `sandbox.reposPath` (chart wires `REPOS_PATH` into sandbox env, which the kubernetes backend uses to attach the `repos` volume — see `.centaur/services/api/api/sandbox/kubernetes.py:1081`). Add a new overlay persona `lab-eng` whose `[tool.centaur] default_repo = "Mperhats/centaur-lab"` causes the API to set `AGENT_REPO` for any turn that selects it, triggering the entrypoint's `git clone --shared` from the read-only mount into `workspace/`. Add an overlay `creating-tools` skill that overrides upstream's by pointing scaffolds at `overlay/tools/<name>/` (so the agent edits the overlay, not the read-only base). Push + PR creation reuse the `GITHUB_TOKEN` already patched into `centaur-infra-env` by `bootstrap-secrets`, with scope widened to include `Contents` and `Pull requests`.

**Tech Stack:** Helm 3, Kubernetes (Docker Desktop), Centaur Helm chart (`repoCache` DaemonSet, `sandbox.reposPath`), Python tool/persona discovery (`[tool.centaur]` TOML in `pyproject.toml`), bash sandbox entrypoint, `gh` CLI (PR creation from sandbox), GitHub fine-grained PAT.

**Branch:** Create `slack-to-pr-loop` via `superpowers:using-git-worktrees` before executing this plan.

**Spec source:** Inline (no separate spec). The brainstorm conversation that produced this plan is the spec. Acceptance criteria live under "Verification" inside each task and under "Final Smoke" at the end.

---

## File Structure

| File | Status | Responsibility |
|------|--------|----------------|
| `.env.example` | Modify | Expand the `GITHUB_TOKEN` documentation block to list the scopes the sandbox needs (`Contents`, `Pull requests`, plus the existing `Issues`/`Metadata`) and call out that the same token is now also consumed by the repo-cache DaemonSet via `repoCache.githubToken.existingSecretName`. |
| `values.local.yaml` | Modify | Enable `repoCache` for `Mperhats/centaur-lab`, point its `githubToken.existingSecretName` at the existing `centaur-infra-env` Secret with key `GITHUB_TOKEN` (no second Secret needed). Set `sandbox.reposPath: /var/lib/centaur/repos` to match. |
| `overlay/tools/personas/lab-eng/pyproject.toml` | Create | Persona registration: `[tool.centaur] type = "persona"`, `engine = "codex"`, `prompt = "PROMPT.md"`, `default_repo = "Mperhats/centaur-lab"`. Discoverable via `TOOL_DIRS=/app/tools:/app/overlay/org/tools` walking. |
| `overlay/tools/personas/lab-eng/PROMPT.md` | Create | Persona body. Tells the agent: "you are in lab-eng, you can edit centaur-lab via `git-branch Mperhats/centaur-lab`, overlay tools live under `overlay/tools/<name>/`, open a PR when scaffolding is complete." References the overlay `creating-tools` skill explicitly so users don't have to mention it. |
| `overlay/.agents/skills/creating-tools/SKILL.md` | Create | Overlay override of the baked-in upstream `creating-tools` skill. Same file paths and conventions, except scaffolds land under `overlay/tools/<name>/`. Adds a "PR Workflow" section explaining `git-branch` → commit → `gh pr create` → human review → `just up`. The entrypoint's overlay-skills copy step (`OVERLAY_TREE_SKILLS`) layers this over the baked-in version. |
| `Justfile` | Modify | Add `just slack-loop-smoke` recipe: spawns a turn against the local API with `harness=lab-eng` and the prompt "scaffold a tool called probe that returns the string 'ok'"; polls `/agent/executions/{id}` until completion; verifies a PR was opened on `Mperhats/centaur-lab` via `gh pr list`. |
| `README.md` | Modify | Add an "Extend Centaur from Slack" section between "Run the smoke test" and "Tear down". Walks the user through `@centaur --lab-eng "scaffold a tool called …"` and what to expect (PR notification, review, `just up`, hot reload). |

**Order constraint:** the chart values must accept the new schema before any sandbox can spawn with `AGENT_REPO`. The persona must exist before the smoke recipe selects it. The skill override must exist before the agent scaffolds anything (otherwise it writes to the wrong path). Execute in the order Task 1 → Task 7.

## Task 1: Expand `GITHUB_TOKEN` scope documentation

**Files:**
- Modify: `.env.example` (lines 51-63 — the existing `GITHUB_TOKEN` block)

**Why first:** The repo-cache DaemonSet refuses to start without a valid token, and the chart's `repoCache.githubToken` validation runs at `helm upgrade` time (see `.centaur/contrib/chart/templates/repo-cache-secret.yaml:2-3`). If a contributor follows the README before reading this plan, they need to know — before Task 2 — that their existing token needs broader scope. Documentation lands first so Task 2's `helm upgrade` doesn't fail with a confusing "permission denied" later.

The token already lives in `centaur-infra-env` (patched by `bootstrap-secrets` in the root Justfile:59), so Task 2 will reuse it via `existingSecretName: centaur-infra-env, secretKey: GITHUB_TOKEN`. This task only edits docs — no new env vars.

- [ ] **Step 1: Define the verification command**

The "test" is: a regex check that the updated documentation mentions every required scope and the dual-use (sandbox-PRs + repo-cache + issue-triage). Run from the repo root:

```bash
( missing=()
  for term in 'Contents' 'Pull requests' 'Metadata' 'Issues' 'repoCache' 'sandbox' 'git push'; do
    if ! grep -q "$term" .env.example; then
      missing+=("$term")
    fi
  done
  if [ ${#missing[@]} -eq 0 ]; then
    echo PASS
  else
    echo "FAIL — missing terms in .env.example: ${missing[*]}"
  fi )
```

- [ ] **Step 2: Run the verification before editing to confirm it fails**

Expected (FAIL because the current block only mentions `Issues (read+write) + Metadata (read)`):

```
FAIL — missing terms in .env.example: Contents Pull requests repoCache sandbox git push
```

- [ ] **Step 3: Replace the existing `GITHUB_TOKEN` block in `.env.example`**

Use this exact replacement. The block to replace runs from the comment "github_issue_triage workflow" through the `export GITHUB_TOKEN=…` line.

Find this block (currently lines 51-63):

```
# github_issue_triage workflow. Both keys are OPTIONAL — leave unset to
# disable the workflow; bootstrap-secrets skips unset keys.
#
# GITHUB_WEBHOOK_SECRET verifies HMAC signatures on inbound GitHub webhooks
# at https://centaur.local-labs.xyz/api/webhooks/github-issue-triage.
# Generate once: `openssl rand -hex 32`. The same value must be configured
# in each GitHub repo's webhook settings (Settings -> Webhooks -> Secret).
#
# GITHUB_TOKEN is the agent's PAT for actually posting the triage comment.
# Use a fine-grained token scoped to: Issues (read+write) + Metadata (read).
# Create at https://github.com/settings/personal-access-tokens/new.
export GITHUB_WEBHOOK_SECRET=replace-with-random-hex
export GITHUB_TOKEN=github_pat_replace-me
```

Replace with:

```
# github_issue_triage workflow + sandbox PR creation + repo-cache.
# GITHUB_WEBHOOK_SECRET is OPTIONAL (disables only the github_issue_triage
# webhook). GITHUB_TOKEN is REQUIRED if you want Centaur to extend itself
# via Slack (Task 2 turns on `repoCache.enabled: true`, which refuses to
# start without it).
#
# GITHUB_WEBHOOK_SECRET verifies HMAC signatures on inbound GitHub webhooks
# at https://centaur.local-labs.xyz/api/webhooks/github-issue-triage.
# Generate once: `openssl rand -hex 32`. Configure the same value in each
# GitHub repo's webhook settings (Settings -> Webhooks -> Secret).
#
# GITHUB_TOKEN is the agent's PAT, now used by THREE consumers:
#   1. github_issue_triage workflow — posts triage comments on issues
#   2. repo-cache DaemonSet — clones Mperhats/centaur-lab into a hostPath
#      mount that every sandbox can read from at ~/github/Mperhats/centaur-lab
#   3. sandbox itself — when a turn selects the lab-eng persona, the
#      sandbox does `git-branch Mperhats/centaur-lab` and uses this token
#      via `gh auth login --with-token` (see .centaur/services/sandbox/
#      entrypoint.sh:228-234) to push branches and run `gh pr create`.
#
# Fine-grained PAT scoped to repo Mperhats/centaur-lab with:
#   - Contents:       read & write  (clone, fetch, push)
#   - Pull requests:  read & write  (gh pr create)
#   - Issues:         read & write  (github_issue_triage)
#   - Metadata:       read          (auto-granted)
# Create at https://github.com/settings/personal-access-tokens/new.
export GITHUB_WEBHOOK_SECRET=replace-with-random-hex
export GITHUB_TOKEN=github_pat_replace-me
```

- [ ] **Step 4: Run verification to confirm it passes**

Run the command from Step 1.

Expected:

```
PASS
```

- [ ] **Step 5: Commit**

```bash
git add .env.example
git commit -m "docs(env): expand GITHUB_TOKEN scope for sandbox PR loop

GITHUB_TOKEN now has three consumers — issue-triage workflow,
repo-cache DaemonSet, and sandbox git-branch + gh pr create.
Document the required fine-grained PAT scopes (Contents,
Pull requests, Issues, Metadata) and the dual-use so
contributors don't reprovision a too-narrow token in Task 2."
```

## Task 2: Enable `repoCache` and `sandbox.reposPath` in `values.local.yaml`

**Files:**
- Modify: `values.local.yaml` (append two new top-level blocks: `repoCache` and extend the existing `sandbox` block)

**Why this approach:** the chart's `repoCache.enabled: true` spins up a DaemonSet (`.centaur/contrib/chart/templates/repo-cache.yaml`) that periodically `git fetch`s every repo in `repoCache.repositories` into a `hostPath` volume. `sandbox.reposPath` then mounts that same `hostPath` into every sandbox at `/home/agent/github` read-only (`.centaur/services/api/api/sandbox/kubernetes.py:1205-1208`). The sandbox entrypoint's `git-branch <org>/<repo>` script creates a writable `--shared` clone at `~/branches/<org>/<repo>` from that read-only mount. Everything downstream of this is already wired upstream — this task just turns the switches on.

The chart's `repoCache.githubToken` block can either generate a fresh Secret (`token: …`) or reuse an existing one (`existingSecretName: …`). We reuse `centaur-infra-env`/`GITHUB_TOKEN` so contributors don't need to remember a second variable. The chart's helper `centaur.repoCacheGithubTokenSecretName` (`.centaur/contrib/chart/templates/_helpers.tpl:58-64`) honors `existingSecretName` when present.

- [ ] **Step 1: Define the verification command**

The test is: (a) `helm template` accepts the new values; (b) the rendered DaemonSet references the correct Secret and repo list; (c) the rendered sandbox-creation env vars include `REPOS_PATH`. Run from the repo root:

```bash
( set -e
  rendered=$(cd .centaur && helm template centaur contrib/chart \
    -f contrib/chart/values.dev.yaml \
    -f ../values.local.yaml 2>&1)
  printf '%s' "$rendered" | grep -q 'kind: DaemonSet' || { echo FAIL-no-daemonset; exit 1; }
  printf '%s' "$rendered" | grep -q 'name: repo-cache' || { echo FAIL-no-repo-cache-container; exit 1; }
  printf '%s' "$rendered" | grep -q '"Mperhats/centaur-lab"' || { echo FAIL-no-repo-in-env; exit 1; }
  printf '%s' "$rendered" | grep -q 'secretName: centaur-infra-env' || { echo FAIL-no-existing-secret; exit 1; }
  printf '%s' "$rendered" | grep -q 'name: REPOS_PATH' || { echo FAIL-no-repos-path-env; exit 1; }
  printf '%s' "$rendered" | grep -q '"/var/lib/centaur/repos"' || { echo FAIL-no-hostpath; exit 1; }
  echo PASS )
```

- [ ] **Step 2: Run the verification before editing to confirm it fails**

Expected: `FAIL-no-daemonset` (chart default is `repoCache.enabled: false`).

- [ ] **Step 3: Extend the existing `sandbox` block in `values.local.yaml`**

Find the existing block (currently lines 43-49):

```yaml
sandbox:
  # Same image-pull fix: sandbox pods spawn per agent turn, get the same
  # default `pullPolicy: Always`, and silently fail with "sandbox readiness
  # timed out" after 60s because kubelet can't pull centaur-agent:latest
  # from any registry. Without this override, every Slack mention errors out.
  image:
    pullPolicy: IfNotPresent
```

Replace with:

```yaml
sandbox:
  # Same image-pull fix: sandbox pods spawn per agent turn, get the same
  # default `pullPolicy: Always`, and silently fail with "sandbox readiness
  # timed out" after 60s because kubelet can't pull centaur-agent:latest
  # from any registry. Without this override, every Slack mention errors out.
  image:
    pullPolicy: IfNotPresent
  # Mount path inside each sandbox pod where the repo-cache DaemonSet's
  # hostPath volume gets attached read-only at /home/agent/github. Setting
  # this also makes the chart export REPOS_PATH into the API deployment so
  # api/sandbox/kubernetes.py can validate that AGENT_REPO has a backing
  # mount. Must match repoCache.hostPath below.
  reposPath: /var/lib/centaur/repos
```

- [ ] **Step 4: Append the `repoCache` block to `values.local.yaml`**

Append at the end of the file (after the existing `overlay:` block):

```yaml
repoCache:
  # Spin up the repo-cache DaemonSet that periodically `git fetch`s every
  # repo listed in .repositories into .hostPath. Every sandbox pod gets
  # that hostPath mounted read-only at ~/github/<org>/<repo>, which is the
  # source the sandbox entrypoint uses for `git clone --shared` (cheap,
  # CoW-style writable clone via `git-branch`). Without this enabled, any
  # sandbox spawned with AGENT_REPO set will exit during entrypoint with
  # "not a valid git repository".
  enabled: true

  # The DaemonSet writes here on each cluster node; sandbox pods mount the
  # same path read-only. On Docker Desktop, /var/lib/* paths are inside
  # the host VM's filesystem and work without any extra config. The
  # `type: DirectoryOrCreate` in repo-cache.yaml means the path is created
  # if missing on first start.
  hostPath: /var/lib/centaur/repos

  # Mirror only the repo the sandbox needs to edit. Each entry is fetched
  # by the DaemonSet in a loop every syncIntervalSeconds. Add more here
  # when you want the agent to be able to edit additional repos (e.g.,
  # an infra repo for Argo CD bootstrap once you ship it).
  repositories:
    - Mperhats/centaur-lab

  # 5 minutes is upstream's default; tune lower if you want PRs to see
  # the latest main quickly after merge. Each cycle is a `git fetch`, not
  # a full clone, so the cost is low.
  syncIntervalSeconds: 300

  # Reuse the GITHUB_TOKEN already living in centaur-infra-env (patched
  # by bootstrap-secrets). The chart's repoCache.githubToken supports
  # `existingSecretName + secretKey` to avoid creating a second Secret.
  # See .centaur/contrib/chart/templates/_helpers.tpl:58-64 for the
  # helper that resolves the secret name.
  githubToken:
    existingSecretName: centaur-infra-env
    secretKey: GITHUB_TOKEN
```

- [ ] **Step 5: Run verification to confirm it passes**

Run the command from Step 1.

Expected:

```
PASS
```

- [ ] **Step 6: Apply the values and observe the DaemonSet come up**

Run:

```bash
just deploy
kubectl -n centaur-system rollout status daemonset/centaur-centaur-repo-cache --timeout=120s
kubectl -n centaur-system get pods -l app.kubernetes.io/component=repo-cache
```

Expected: the DaemonSet's pod reaches `1/1 Running` within ~30s after the chart updates and the image is cached locally.

- [ ] **Step 7: Verify the read-only clone landed on the host**

The hostPath volume is on the cluster node, not your laptop directly. For Docker Desktop, exec into the repo-cache pod itself:

```bash
kubectl -n centaur-system exec -it daemonset/centaur-centaur-repo-cache -- \
  ls /cache/Mperhats/centaur-lab/.git
```

Expected: standard git directory contents (`HEAD`, `refs`, `objects`, etc.). If you see "No such file or directory", check the pod logs for clone errors (usually a token-scope issue).

- [ ] **Step 8: Commit**

```bash
git add values.local.yaml
git commit -m "chart(local): enable repoCache + sandbox.reposPath for centaur-lab

Mirror Mperhats/centaur-lab into /var/lib/centaur/repos via the
repo-cache DaemonSet and mount that hostPath into every sandbox at
/home/agent/github read-only. The sandbox entrypoint's git-branch
script uses this as the source for writable --shared clones when
AGENT_REPO is set (Task 3 wires that via the lab-eng persona).

Reuses centaur-infra-env/GITHUB_TOKEN as the auth source so no
second Secret is required."
```

## Task 3: Add the `lab-eng` overlay persona

**Files:**
- Create: `overlay/tools/personas/lab-eng/pyproject.toml`
- Create: `overlay/tools/personas/lab-eng/PROMPT.md`

**Why it's a persona, not a flag:** the chart's API resolves `default_repo` per-persona inside `.centaur/services/api/api/agent.py:762-812` — only persona records propagate `default_repo` to the spawn env (`AGENT_REPO`). If a user mentions `@centaur` without a persona, no repo is attached and the sandbox stays in scratch mode. If they say `@centaur --lab-eng "scaffold a polygon tool"`, the API attaches `AGENT_REPO=Mperhats/centaur-lab`, the entrypoint does `git clone --shared "/home/agent/github/Mperhats/centaur-lab" "$WORKSPACE_DIR"`, and the agent has a writable working copy from the first turn.

**Naming:** the upstream `eng` persona is shipped at `.centaur/tools/personas/eng/`. We do NOT want to shadow it (you still want a generic eng persona for repos other than centaur-lab). `lab-eng` is a sibling: same engine, similar prompt, but pinned to this repo.

- [ ] **Step 1: Define the verification command**

The test is: (a) `pyproject.toml` parses cleanly with the right registration block; (b) after a redeploy, `GET /personas` returns lab-eng with `default_repo` set. Run from the repo root:

```bash
( set -e
  python3 -c "import tomllib, sys; \
    d = tomllib.load(open('overlay/tools/personas/lab-eng/pyproject.toml', 'rb')); \
    tc = d['tool']['centaur']; \
    assert tc['type'] == 'persona', 'wrong type'; \
    assert tc['engine'] == 'codex', 'wrong engine'; \
    assert tc['prompt'] == 'PROMPT.md', 'wrong prompt file'; \
    assert tc['default_repo'] == 'Mperhats/centaur-lab', 'wrong default_repo'; \
    print('toml-ok')"
  test -s overlay/tools/personas/lab-eng/PROMPT.md && echo "prompt-ok"
  echo PASS )
```

- [ ] **Step 2: Run the verification before creating files to confirm it fails**

Expected (FAIL — neither file exists yet):

```
... FileNotFoundError: ...overlay/tools/personas/lab-eng/pyproject.toml
```

- [ ] **Step 3: Create `overlay/tools/personas/lab-eng/pyproject.toml`**

Write exactly this:

```toml
[project]
name = "lab-eng"
description = "Eng persona pinned to Mperhats/centaur-lab — can edit the overlay and open PRs from inside a sandbox"
version = "0.1.0"

[tool.centaur]
type = "persona"
engine = "codex"
prompt = "PROMPT.md"
# Selecting this persona causes the API to set AGENT_REPO=Mperhats/centaur-lab
# on the sandbox spawn, which makes entrypoint.sh `git clone --shared` from
# /home/agent/github/Mperhats/centaur-lab (read-only mount, populated by the
# repo-cache DaemonSet — see values.local.yaml::repoCache).
default_repo = "Mperhats/centaur-lab"
```

- [ ] **Step 4: Create `overlay/tools/personas/lab-eng/PROMPT.md`**

Write exactly this:

```markdown
# Lab-Eng Persona

You are in the **lab-eng** persona. The base system prompt still applies in full.

You have read+write access to `Mperhats/centaur-lab`. Your job is to extend this Centaur deployment — add new overlay tools, new overlay skills, new overlay workflows — in response to user requests, and open a PR with the change.

## Where things live in this repo

This is an overlay repo on top of upstream Centaur (mounted as a git submodule at `.centaur/`). You **must** put new code under `overlay/`, not under `tools/` or `.agents/` directly — those root paths are for the upstream Centaur submodule and you should treat them as read-only.

| Want to add | Path | Discovery |
|-------------|------|-----------|
| A new tool | `overlay/tools/<name>/` | `TOOL_DIRS=/app/tools:/app/overlay/org/tools` walks both, with overlay last |
| A new persona | `overlay/tools/personas/<name>/` | Same `TOOL_DIRS` walk; `[tool.centaur] type = "persona"` registers it |
| A new skill | `overlay/.agents/skills/<name>/` | Sandbox entrypoint copies overlay skills LAST so they override the baked-in ones |
| A new workflow | `overlay/workflows/<name>.py` | `WORKFLOW_DIRS=/app/workflows:/app/overlay/org/workflows` |

## Standard workflow for any change

1. **Branch off main into a writable clone:**

   ```bash
   git-branch Mperhats/centaur-lab
   cd ~/branches/Mperhats/centaur-lab
   ```

   `git-branch` is the sandbox helper that turns the read-only mount at `~/github/Mperhats/centaur-lab` into a `--shared` clone at `~/branches/…`. NEVER commit inside `~/github/`.

2. **Make the change.** For tool scaffolding specifically, follow the `creating-tools` skill — the overlay version of it directs you at `overlay/tools/<name>/`.

3. **Validate locally:**

   ```bash
   cd ~/branches/Mperhats/centaur-lab/overlay
   uvx ruff check tools
   ```

4. **Commit and push:**

   ```bash
   git add overlay/
   git commit -m "feat(overlay): <descriptive>"
   git push -u origin "$(git branch --show-current)"
   ```

   The branch name is auto-generated by entrypoint (`agent-<timestamp>-<rand>-<rand>`). Push uses the `GITHUB_TOKEN` baked into the sandbox via `gh auth login --with-token` during entrypoint.

5. **Open the PR:**

   ```bash
   gh pr create --title "feat(overlay): <descriptive>" --body "$(cat <<'EOF'
   ## Summary
   <1-3 bullets describing what was added or changed>

   ## Loop
   Asked by: <user, if known from the thread>
   Persona: lab-eng
   Auto-generated by: Centaur sandbox

   ## To deploy
   After merge: `just up` rebuilds the overlay image and the API hot-reloads.
   EOF
   )"
   ```

6. **Report the PR URL back in the Slack thread.** The user owns review and merge.

## What you should NOT do

- Do not edit anything under `.centaur/` — it is a submodule pinned at a specific upstream SHA. Submodule bumps are a separate concern.
- Do not deploy directly from the sandbox (`just up` requires kubectl access the sandbox doesn't have). Always go through PR + human merge + local `just up`.
- Do not commit secrets. The token in `~/.git-credentials` is for git operations only; never reference it in code or PR bodies.
- Do not create files outside `overlay/` unless the user explicitly asked you to update upstream's submodule pin, a chart value, or root-level metadata (README, Justfile).

## Response style (inherits from eng)

- Start with the outcome: "Opened PR #N: <title> — <URL>".
- Then a 1-3 bullet "what changed" summary.
- Then any caveats (untested code paths, follow-ups needed).
```

- [ ] **Step 5: Run verification to confirm it passes**

Run the command from Step 1.

Expected:

```
toml-ok
prompt-ok
PASS
```

- [ ] **Step 6: Rebuild the overlay image and redeploy so the API picks up the persona**

```bash
just overlay::build
just deploy
kubectl -n centaur-system rollout status deployment/centaur-centaur-api --timeout=120s
```

- [ ] **Step 7: Verify the API registered the persona**

```bash
API_KEY=$(kubectl -n centaur-system get secret centaur-infra-env -o jsonpath='{.data.SLACKBOT_API_KEY}' | base64 -d)
kubectl -n centaur-system exec deploy/centaur-centaur-api -- \
  curl -s http://localhost:8000/personas -H "X-Api-Key: $API_KEY" \
  | python3 -c "import sys, json; \
    personas = json.load(sys.stdin); \
    lab_eng = next((p for p in personas if p.get('name') == 'lab-eng'), None); \
    assert lab_eng, 'lab-eng persona not registered'; \
    assert lab_eng['default_repo'] == 'Mperhats/centaur-lab', f'wrong default_repo: {lab_eng[\"default_repo\"]}'; \
    print('OK: lab-eng registered with default_repo =', lab_eng['default_repo'])"
```

Expected:

```
OK: lab-eng registered with default_repo = Mperhats/centaur-lab
```

- [ ] **Step 8: Commit**

```bash
git add overlay/tools/personas/lab-eng/
git commit -m "feat(overlay): add lab-eng persona pinned to centaur-lab

Selecting --lab-eng causes the API to set AGENT_REPO=Mperhats/
centaur-lab on the sandbox spawn, which triggers entrypoint.sh's
git clone --shared from the read-only repo-cache mount. The
persona prompt directs the agent at overlay/ paths and the
git-branch → commit → gh pr create workflow."
```

## Task 4: Add the overlay `creating-tools` skill override

**Files:**
- Create: `overlay/.agents/skills/creating-tools/SKILL.md`

**Why an override, not an addition:** the upstream `creating-tools` skill (`.centaur/.agents/skills/creating-tools/SKILL.md`) tells the agent to create files under `tools/<name>/` — that's the right path for the upstream Centaur repo but the WRONG path for centaur-lab where the agent has read-only access to `tools/` (it's inside the submodule). The sandbox entrypoint copies skills in priority order (`.centaur/services/sandbox/entrypoint.sh:179-184`):

```
BAKED_IN_CENTAUR_SKILLS  →  MOUNTED_CENTAUR_SKILLS  →  CENTAUR_SKILLS  →  MOUNTED_ORG_SKILLS  →  OVERLAY_TREE_SKILLS
```

Each source `cp -r`s on top of the previous, so the last source wins. `OVERLAY_TREE_SKILLS` is `$CENTAUR_OVERLAY_DIR/.agents/skills` — exactly where this task lands a file. Result: in any sandbox with the centaur-lab overlay mounted, the overlay `SKILL.md` shadows the upstream one transparently.

**Content strategy:** don't rewrite the whole upstream skill — it's 269 lines of well-tested scaffolding guidance. Override only the path conventions, hot-reload section, and add an explicit "PR Workflow" section that the upstream skill doesn't have.

- [ ] **Step 1: Define the verification command**

The test is: (a) the file exists with the right frontmatter; (b) it mentions `overlay/tools/` and does NOT instruct creating files at the root `tools/`; (c) it references `git-branch` and `gh pr create`. Run from the repo root:

```bash
( set -e
  f=overlay/.agents/skills/creating-tools/SKILL.md
  test -s "$f" || { echo "FAIL: $f missing or empty"; exit 1; }
  grep -q '^name: creating-tools$' "$f" || { echo "FAIL: wrong frontmatter name"; exit 1; }
  grep -q 'overlay/tools/<name>/' "$f" || { echo "FAIL: doesn't reference overlay path"; exit 1; }
  grep -q 'git-branch Mperhats/centaur-lab' "$f" || { echo "FAIL: missing git-branch step"; exit 1; }
  grep -q 'gh pr create' "$f" || { echo "FAIL: missing gh pr create step"; exit 1; }
  if grep -E '^Every tool lives at `tools/<name>/`' "$f"; then
    echo "FAIL: still says tools/<name>/ (should be overlay/tools/<name>/)"
    exit 1
  fi
  echo PASS )
```

- [ ] **Step 2: Run the verification before creating the file to confirm it fails**

Expected:

```
FAIL: overlay/.agents/skills/creating-tools/SKILL.md missing or empty
```

- [ ] **Step 3: Create `overlay/.agents/skills/creating-tools/SKILL.md`**

Write exactly this:

````markdown
---
name: creating-tools
description: "Scaffold and build new tool integrations in overlay/tools/ for the centaur-lab deployment. Use when asked to create a new tool, add an API integration, or build a new client for an external service. Overrides the upstream creating-tools skill — same conventions, except scaffolds live under overlay/tools/<name>/ and the workflow ends with a PR against Mperhats/centaur-lab."
---

# Creating Tools (centaur-lab overlay)

This is the centaur-lab override of the upstream `creating-tools` skill. The scaffolding conventions are identical to upstream's — what changes is **where files land** and the **delivery loop** (PR instead of direct hot-reload).

## File Structure

Every tool lives at `overlay/tools/<name>/` (NOT the root `tools/` — that belongs to the upstream Centaur submodule and is read-only in this repo). Each tool needs exactly these files:

```
overlay/tools/<name>/
├── __init__.py        # Empty file
├── .env.example       # Document required secrets (one per line: KEY=description)
├── client.py          # API client class + _client() factory function
├── cli.py             # Typer CLI for standalone use
└── pyproject.toml     # Package metadata + [tool.centaur] section
```

Reference example: `overlay/tools/semantic_scholar/` is a working tool that follows this exact layout, including the `[tool.centaur] optional_secrets = [...]` block for iron-proxy credential injection.

## PR Workflow (the part that's different from upstream)

You are running inside a sandbox with read-only access to `~/github/Mperhats/centaur-lab` and write access via `git-branch`. Before any file changes:

1. **Branch off main into a writable clone:**

   ```bash
   git-branch Mperhats/centaur-lab
   cd ~/branches/Mperhats/centaur-lab
   ```

2. **Scaffold the tool under `overlay/tools/<name>/`** following the file structure above and the upstream conventions described below.

3. **Validate locally:**

   ```bash
   cd ~/branches/Mperhats/centaur-lab/overlay
   uvx ruff check tools
   ```

4. **Commit and push** the auto-generated branch (`entrypoint.sh` already created a branch named `agent-<ts>-<rand>-<rand>`):

   ```bash
   git add overlay/tools/<name>/
   git commit -m "feat(overlay): add <name> tool"
   git push -u origin "$(git branch --show-current)"
   ```

5. **Open the PR:**

   ```bash
   gh pr create --title "feat(overlay): add <name> tool" --body "$(cat <<'EOF'
   ## Summary
   - Adds `overlay/tools/<name>/` with client + CLI + pyproject.toml.
   - Required secrets: <list, or "none">.
   - Hot-reloads after merge + `just up`.

   ## Test plan
   - [ ] Reviewer runs `just overlay::lint` locally.
   - [ ] Reviewer merges; runs `just up`; verifies `GET /tools` lists `<name>`.
   - [ ] Reviewer invokes a sample method via `curl POST /tools/<name>/<method>`.
   EOF
   )"
   ```

6. **Report the PR URL back to the user.** Do not attempt to merge or deploy yourself.

## Deployment (what happens after merge)

Hot-reload depends on files actually appearing in the API pod's `/app/overlay/org/tools/<name>/` directory. The path looks like this:

1. PR merged on GitHub.
2. The maintainer (or CI, when wired) runs `just up` locally:
   - `just overlay::build` rebuilds `centaur-overlay:latest` baking in the new tool.
   - `just deploy` does `helm upgrade --install`; the API pod restarts with the new image.
3. The chart's overlay-bootstrap initContainer copies `/overlay/tools/<name>/` into the API pod's `/app/overlay/org/tools/<name>/`.
4. The API's plugin watcher (`PLUGIN_WATCHER_ENABLED`) registers the new tool, OR — since the pod just restarted — startup discovery picks it up.
5. `POST /tools/<name>/<method>` now works.

You do NOT need to `POST /admin/reload-tools` after a fresh deploy — startup discovery already loaded it. That endpoint is for editing files inside a long-lived pod, which is not the path we use here.

## Tool Implementation (unchanged from upstream)

The rest of the file follows the upstream `creating-tools` skill verbatim — read `~/.agents/skills/creating-tools/SKILL.md` (the baked-in upstream version) for the full `client.py`/`cli.py`/`pyproject.toml`/secrets resolution sections. Specifically:

- `client.py` rules: no `load_dotenv()`, import `secret` from `shared.tool_sdk`, class-based, `_client()` factory at the bottom, public methods get type hints.
- `cli.py` rules: `load_dotenv()` at the top, thin wrapper around the client, typer for the CLI, support `--json` and `--markdown` on every command.
- `pyproject.toml`: the **`[tool.centaur] module = "client.py"`** registration is required — without it the tool manager won't discover the package. (Note: upstream still uses the older `[tool.ai-v2]` name in places; centaur-lab and current upstream both use `[tool.centaur]`. See `overlay/tools/semantic_scholar/pyproject.toml` for the canonical shape.)
- `.env.example`: one key per line, with a short description.

If you find conflicting guidance between this file and the upstream skill, this file wins for path/registration/PR concerns; the upstream skill wins for client/CLI shape.

## Reference: existing overlay tool

`overlay/tools/semantic_scholar/` is the closest in-repo example. It demonstrates:

- The `[tool.centaur] optional_secrets = [...]` block for iron-proxy credential injection.
- `package = false` in `[tool.uv]` so `uv` skips wheel-building.
- A CLI that runs standalone via `uv run python cli.py …` and is also exposed as a `just overlay::smoke-semantic-scholar` recipe.

Mirror that shape for any new tool unless the user explicitly wants something different.
````

- [ ] **Step 4: Run verification to confirm it passes**

Run the command from Step 1.

Expected:

```
PASS
```

- [ ] **Step 5: Rebuild the overlay image so the new skill ships in the next sandbox**

```bash
just overlay::build
```

The skill is mounted into sandbox pods at `/home/agent/overlay/org/.agents/skills/creating-tools/SKILL.md` and copied into the workspace by `entrypoint.sh:179-184`. No API redeploy is needed — sandboxes pick it up on next spawn.

- [ ] **Step 6: Verify the override is actually visible inside a fresh sandbox**

Spawn a throwaway sandbox and inspect its `.agents/skills/creating-tools/SKILL.md`:

```bash
API_KEY=$(kubectl -n centaur-system get secret centaur-infra-env -o jsonpath='{.data.SLACKBOT_API_KEY}' | base64 -d)
THREAD_KEY="verify-skill-override-$(date +%s)"

kubectl -n centaur-system exec deploy/centaur-centaur-api -- curl -s -X POST \
  http://localhost:8000/agent/spawn \
  -H "X-Api-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d "{\"thread_key\":\"$THREAD_KEY\",\"harness\":\"lab-eng\"}" | jq

# Wait ~10s for the pod to be ready, then grep its skill file
sleep 10
POD=$(kubectl -n centaur-system get pods -l centaur.ai/thread-key=$THREAD_KEY -o jsonpath='{.items[0].metadata.name}')
kubectl -n centaur-system exec "$POD" -- \
  grep -c 'overlay/tools/<name>/' /home/agent/workspace/.agents/skills/creating-tools/SKILL.md
```

Expected output: a positive integer (at least `3` based on the file we wrote). If you see `0`, the overlay didn't get layered in — re-run `just overlay::build && just deploy`. If you see `No such file`, the entrypoint never copied the overlay skills — check that `CENTAUR_OVERLAY_DIR` is being set on the sandbox pod (`kubectl describe pod $POD | grep CENTAUR_OVERLAY_DIR`).

Clean up the test pod:

```bash
kubectl -n centaur-system exec deploy/centaur-centaur-api -- curl -s -X POST \
  http://localhost:8000/agent/stop \
  -H "X-Api-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d "{\"thread_key\":\"$THREAD_KEY\"}"
```

- [ ] **Step 7: Commit**

```bash
git add overlay/.agents/skills/creating-tools/
git commit -m "feat(overlay): override creating-tools skill for centaur-lab paths

Adds an overlay-side SKILL.md that takes precedence over upstream
via the entrypoint's OVERLAY_TREE_SKILLS copy step. Redirects new
tool scaffolds to overlay/tools/<name>/ (the upstream skill points
at root tools/, which is inside the read-only submodule in this
repo). Documents the git-branch + gh pr create loop and the
just up rebuild-redeploy half of the deployment."
```

## Task 5: Add the `just slack-loop-smoke` recipe

**Files:**
- Modify: `Justfile` (append a new recipe in the `dev` group)

**Why a `just` recipe, not a manual test:** the verification surface here is large (spawn → message → execute → poll executions → check GitHub for the PR). Encoding it in a recipe means future regressions show up the moment someone forgets the loop. The recipe mirrors the shape of the existing `smoke` recipe (`Justfile:67-112`) but adds GitHub-side verification.

**Test contract:**
1. Spawn a sandbox on a fresh `thread_key` with `harness=lab-eng`.
2. Send a user turn: "scaffold a tool called `probe` that has one method `ping` returning the string `ok`. Open a PR when done. Do not block waiting for review."
3. Poll `/agent/executions/{id}` for completion (timeout 5 minutes — scaffolding + push + PR is slower than the PONG smoke).
4. On `status: completed`, run `gh pr list -R Mperhats/centaur-lab --json title,headRefName,createdAt --search "feat(overlay): add probe tool"` and assert that exactly one PR matching the search exists and was created in the last 10 minutes.
5. On `status: failed`, surface the executions response.
6. Print the PR URL on success.

- [ ] **Step 1: Define the verification command**

The "test" for a `just` recipe is its own dry-run. Run from the repo root:

```bash
( just --show slack-loop-smoke >/dev/null 2>&1 \
    && echo "PASS: recipe is parseable" \
    || echo "FAIL: just couldn't parse the recipe" )
```

- [ ] **Step 2: Run the verification before editing to confirm it fails**

Expected:

```
FAIL: just couldn't parse the recipe
```

with stderr:

```
error: Justfile does not contain recipe `slack-loop-smoke`
```

- [ ] **Step 3: Append the recipe to `Justfile`**

Add the following block at the end of the file (after the existing `dev` recipe block, lines 114-142):

```just
# Full Slack-to-PR loop smoke test. Spawns a `lab-eng` sandbox, asks it to
# scaffold a throwaway `probe` tool, and verifies a PR was opened against
# Mperhats/centaur-lab. Requires repoCache + sandbox.reposPath enabled
# (Task 2) and the lab-eng persona registered (Task 3).
#
# Idempotency: the recipe uses a fresh thread_key per run (timestamped),
# so re-running is safe — each invocation opens a new PR. Clean up
# accumulated probe PRs with `gh pr list -R Mperhats/centaur-lab --search
# "feat(overlay): add probe tool" | awk '{print $1}' | xargs -I{} gh pr
# close -R Mperhats/centaur-lab {} --delete-branch`.
[group('dev')]
slack-loop-smoke:
    #!/usr/bin/env bash
    set -euo pipefail

    timestamp=$(date +%s)
    thread_key="lab-loop-smoke-${timestamp}"
    api_deploy="deploy/${CENTAUR_RELEASE}-centaur-api"
    api_key=$(kubectl -n "$CENTAUR_NAMESPACE" get secret centaur-infra-env -o jsonpath='{.data.SLACKBOT_API_KEY}' | base64 -d)

    exec_curl() {
      kubectl -n "$CENTAUR_NAMESPACE" exec "$api_deploy" -- curl -s -H "X-Api-Key: $api_key" "$@"
    }

    echo "=== 1/4 spawn ==="
    spawn=$(exec_curl -X POST http://localhost:8000/agent/spawn \
      -H "Content-Type: application/json" \
      -d "{\"thread_key\":\"${thread_key}\",\"harness\":\"lab-eng\"}")
    printf '%s\n' "$spawn" | jq .
    assignment_generation=$(printf '%s' "$spawn" | jq -r '.assignment_generation')

    echo "=== 2/4 message ==="
    exec_curl -X POST http://localhost:8000/agent/message \
      -H "Content-Type: application/json" \
      -d "{
        \"thread_key\":\"${thread_key}\",
        \"assignment_generation\":${assignment_generation},
        \"role\":\"user\",
        \"parts\":[{\"type\":\"text\",\"text\":\"Use the creating-tools skill. Scaffold a brand new tool called probe under overlay/tools/probe/. It should have one method named ping that takes no arguments and returns the string 'ok'. Validate with uvx ruff check, then commit, push, and open a PR titled 'feat(overlay): add probe tool'. Reply with only the PR URL when done.\"}]
      }" >/dev/null

    echo "=== 3/4 execute ==="
    execute=$(exec_curl -X POST http://localhost:8000/agent/execute \
      -H "Content-Type: application/json" \
      -d "{
        \"thread_key\":\"${thread_key}\",
        \"assignment_generation\":${assignment_generation},
        \"harness\":\"lab-eng\",
        \"delivery\":{\"platform\":\"dev\"}
      }")
    printf '%s\n' "$execute" | jq .
    execution_id=$(printf '%s' "$execute" | jq -r '.execution_id')

    echo "=== 4/4 poll (timeout 300s) ==="
    for i in $(seq 1 150); do
      state=$(exec_curl "http://localhost:8000/agent/executions/${execution_id}")
      status=$(printf '%s' "$state" | jq -r '.status // empty')
      case "$status" in
        completed)
          echo "✓ execution completed in ~$((i * 2))s"
          printf '%s\n' "$state" | jq '{status, result_text}'
          break
          ;;
        failed|failed_permanent|cancelled)
          echo "✗ execution ended with status=$status"
          printf '%s\n' "$state" | jq .
          exit 1
          ;;
      esac
      sleep 2
    done

    if [ "$status" != "completed" ]; then
      echo "✗ timed out after 300s waiting for execution ${execution_id}"
      exec_curl "http://localhost:8000/agent/executions/${execution_id}" | jq .
      exit 1
    fi

    echo "=== 5/4 verify PR (bonus step — agent might race ahead of GitHub indexing) ==="
    # The agent's gh pr create returns when GitHub accepts the API call, but
    # search indexing can lag a few seconds. Retry a few times.
    for attempt in 1 2 3 4 5; do
      prs=$(gh pr list -R Mperhats/centaur-lab \
        --search "feat(overlay): add probe tool created:>=$(date -u -v-10M +'%Y-%m-%dT%H:%M:%SZ' 2>/dev/null || date -u -d '10 minutes ago' +'%Y-%m-%dT%H:%M:%SZ')" \
        --json number,title,url,headRefName,createdAt --limit 5)
      pr_count=$(printf '%s' "$prs" | jq 'length')
      if [ "$pr_count" -ge 1 ]; then
        echo "✓ found $pr_count matching PR(s) created in the last 10 minutes:"
        printf '%s\n' "$prs" | jq '.'
        exit 0
      fi
      echo "  attempt $attempt: no PR found yet, sleeping 5s..."
      sleep 5
    done

    echo "✗ execution completed but no matching PR appeared on GitHub within 25s"
    echo "  check the agent's result_text above — it may have failed at the push or gh step:"
    exec_curl "http://localhost:8000/agent/executions/${execution_id}" | jq '.result_text'
    exit 1
```

- [ ] **Step 4: Run verification to confirm the recipe parses**

```bash
just --show slack-loop-smoke >/dev/null && echo "PASS: recipe is parseable"
```

Expected:

```
PASS: recipe is parseable
```

- [ ] **Step 5: Dry-run the recipe end-to-end (this is the real integration test)**

```bash
just slack-loop-smoke
```

Expected outcome on a healthy stack: the recipe prints `✓ found N matching PR(s)…` and exits 0. The total runtime is dominated by the agent's reasoning + push (typically 60-180s with Claude Code as the engine).

**Common failure modes to triage if it fails:**

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| `not a valid git repository: /home/agent/github/Mperhats/centaur-lab` | repo-cache hasn't synced yet | `kubectl -n centaur-system logs daemonset/centaur-centaur-repo-cache` and wait for "Cloning Mperhats/centaur-lab" to complete |
| `Permission denied (publickey)` or `403` on push | `GITHUB_TOKEN` scope is missing `Contents: write` | Regenerate the token with the scopes documented in Task 1, re-run `just bootstrap-secrets`, redeploy |
| `gh: command not found` | Old sandbox image cached | `just build-one agent` from inside `.centaur/`, restart sandbox pods |
| Agent edited under `tools/<name>/` instead of `overlay/tools/<name>/` | Skill override didn't reach the sandbox | Re-verify Task 4 Step 6; confirm overlay image rebuilt with `docker images centaur-overlay --digests` |
| Execution times out | Codex/Claude session never returned | Check `kubectl logs <sandbox-pod>` for harness errors; raise the 300s timeout if cluster is slow |

- [ ] **Step 6: Commit**

```bash
git add Justfile
git commit -m "feat(dev): add slack-loop-smoke recipe for the PR loop

Spawns a lab-eng sandbox, asks it to scaffold a throwaway probe
tool, and verifies a PR appears on Mperhats/centaur-lab.
Tolerates GitHub search indexing lag with a 5x5s retry loop.

This is the regression guard for Tasks 2-4 — if any of the
plumbing (repoCache, lab-eng persona, overlay skill override)
breaks, this recipe is what catches it."
```

## Task 6: Document the Slack loop in `README.md`

**Files:**
- Modify: `README.md` (insert a new `## Extend Centaur from Slack` section after the existing "Run the smoke test" section and before "Tear down")

**Why a dedicated section, not a one-liner:** the loop is the only way a future contributor (or you, in 3 months) will know that `--lab-eng` exists, that PRs auto-open, that you still have to `just up` after merge. The README is also where someone debugging "why didn't my Slack message create anything?" will look first.

- [ ] **Step 1: Define the verification command**

The test is: the README has a section explicitly titled "Extend Centaur from Slack" between the smoke section and tear-down, and that section references the three keywords a user would search for: `--lab-eng`, `PR`, and `just up`. Run from the repo root:

```bash
( set -e
  awk '/^## Extend Centaur from Slack$/,/^## /' README.md | head -1 \
    | grep -q '^## Extend Centaur from Slack$' || { echo "FAIL: section missing"; exit 1; }
  for term in '--lab-eng' 'PR' 'just up' 'creating-tools' 'overlay/tools'; do
    grep -q -F "$term" README.md || { echo "FAIL: README missing keyword: $term"; exit 1; }
  done
  # Section order: must appear AFTER "Run the smoke test" and BEFORE "Tear down"
  smoke_line=$(grep -n '^## Run the smoke test$' README.md | cut -d: -f1)
  extend_line=$(grep -n '^## Extend Centaur from Slack$' README.md | cut -d: -f1)
  teardown_line=$(grep -n '^## Tear down$' README.md | cut -d: -f1)
  if [ -z "$smoke_line" ] || [ -z "$extend_line" ] || [ -z "$teardown_line" ]; then
    echo "FAIL: anchor sections missing (smoke=$smoke_line extend=$extend_line teardown=$teardown_line)"
    exit 1
  fi
  [ "$smoke_line" -lt "$extend_line" ] && [ "$extend_line" -lt "$teardown_line" ] \
    || { echo "FAIL: wrong section order"; exit 1; }
  echo PASS )
```

- [ ] **Step 2: Run the verification before editing to confirm it fails**

Expected:

```
FAIL: section missing
```

- [ ] **Step 3: Insert the new section in `README.md`**

Find the existing line (currently line 152):

```markdown
## Tear down
```

Replace it with:

```markdown
## Extend Centaur from Slack

Once `just smoke` returns `PONG`, you can ask the running Centaur to extend
itself. The `lab-eng` persona is wired to `Mperhats/centaur-lab` — when you
mention it, the sandbox spawns with a writable clone of this repo and the
`gh` CLI authenticated via your `GITHUB_TOKEN`.

### Add a new tool from Slack

In any Slack channel where the Centaur bot is installed:

```
@centaur --lab-eng scaffold a new overlay tool called polygon that wraps
the Polygon.io v2 aggregates endpoint. One method: daily_close(ticker,
date) returning the close price. Open a PR when done.
```

What happens:

1. The Slackbot routes the message to the API, which spawns a sandbox pod with `AGENT_REPO=Mperhats/centaur-lab` and `harness=lab-eng`.
2. The sandbox boots, clones this repo from the `repoCache` mount into its workspace, and loads the overlay `creating-tools` skill.
3. The agent scaffolds `overlay/tools/polygon/{__init__.py, client.py, cli.py, pyproject.toml, .env.example}` following the skill's conventions.
4. It commits, pushes the agent-generated branch (`agent-<ts>-<rand>-<rand>`), and runs `gh pr create`.
5. It replies in the Slack thread with the PR URL.

You review the PR locally, merge, then:

```bash
just up
```

`just up` rebuilds `centaur-overlay:latest` with the new tool baked in and
restarts the API pod. Within seconds, `GET /tools` returns `polygon` and
the agent can call it on subsequent turns.

### Smoke test the loop locally

The repo ships a recipe that exercises the whole loop end-to-end without
going through Slack:

```bash
just slack-loop-smoke
```

This spawns a `lab-eng` sandbox, asks it to scaffold a throwaway `probe`
tool, polls until the execution completes, and verifies a PR appears on
`Mperhats/centaur-lab`. Use it after any change to `values.local.yaml`,
the `lab-eng` persona, or the overlay `creating-tools` skill to confirm
the loop is still intact.

Clean up accumulated test PRs:

```bash
gh pr list -R Mperhats/centaur-lab --search "feat(overlay): add probe tool" \
  --json number --jq '.[].number' \
  | xargs -I{} gh pr close -R Mperhats/centaur-lab {} --delete-branch
```

### What the agent can and cannot edit

The `lab-eng` persona is scoped to `overlay/` — new tools, new overlay
skills, new overlay personas, new overlay workflows. It will NOT edit:

- `.centaur/` (the upstream Centaur submodule, pinned at a specific SHA)
- `values.local.yaml` (chart values — these change the cluster, not the
  overlay image, and require human review of the deployment surface)
- `Justfile` (deployment scripts — same reason)

For changes to those paths, edit by hand and `just up` normally.

### Prerequisites checklist

The Slack loop only works if **all** of these are true:

- [ ] `GITHUB_TOKEN` in `.env` has `Contents: write` + `Pull requests: write` (see `.env.example` for the full scope list)
- [ ] `repoCache.enabled: true` in `values.local.yaml` (verify with `kubectl -n centaur-system get daemonset/centaur-centaur-repo-cache`)
- [ ] `lab-eng` persona registered (verify with `curl -H "X-Api-Key: $SLACKBOT_API_KEY" http://localhost:8000/personas | jq '.[] | select(.name == "lab-eng")'`)
- [ ] Overlay image rebuilt since the last skill or persona change (`just overlay::build`)

If any of these fail, `just slack-loop-smoke` will print which one — start there before debugging through Slack.

## Tear down
```

- [ ] **Step 4: Run verification to confirm it passes**

Run the command from Step 1.

Expected:

```
PASS
```

- [ ] **Step 5: Commit**

```bash
git add README.md
git commit -m "docs(readme): document the Slack-to-PR loop

Adds an 'Extend Centaur from Slack' section between smoke and
tear-down that walks a user through @centaur --lab-eng scaffold
a tool, the auto-PR, and the just up rebuild. Includes the
just slack-loop-smoke shortcut and a prerequisites checklist
to triage 'why didn't my Slack message create anything?' fast."
```

## Task 7: Final acceptance — end-to-end through real Slack

**Files:** none (operator-driven validation)

**Why this exists as a separate task:** Tasks 1-6 each have local verification, but the cross-cutting "real Slack message → real PR" path has its own failure modes: webhook signing, Cloudflare Tunnel reachability, bot permissions in the channel. This task isolates them.

This task is **operator-driven**. The subagent cannot execute it — it requires (a) the cluster running locally with `just up` complete, (b) the Cloudflare tunnel up (`just cloudflared::status`), and (c) the human posting in a real Slack channel.

- [ ] **Step 1: Confirm prerequisites**

Run the prerequisites checklist from README.md's new section. All five must pass:

```bash
# Token scopes (visual confirmation against your fine-grained PAT page)
echo "Confirm GITHUB_TOKEN has: Contents (RW), Pull requests (RW), Issues (RW), Metadata (R)"

# DaemonSet healthy
kubectl -n centaur-system get daemonset/centaur-centaur-repo-cache

# Persona registered
API_KEY=$(kubectl -n centaur-system get secret centaur-infra-env -o jsonpath='{.data.SLACKBOT_API_KEY}' | base64 -d)
kubectl -n centaur-system exec deploy/centaur-centaur-api -- \
  curl -s -H "X-Api-Key: $API_KEY" http://localhost:8000/personas \
  | jq '.[] | select(.name == "lab-eng")'

# Local smoke loop passes
just slack-loop-smoke

# Tunnel up
just cloudflared::status
```

- [ ] **Step 2: Post in Slack**

In a real Slack channel where the Centaur bot is installed, post:

```
@centaur --lab-eng scaffold a new overlay tool called acceptance_probe with one method ping that returns the string "real-slack-ok". Open a PR titled "feat(overlay): acceptance_probe (e2e test)" when done.
```

- [ ] **Step 3: Observe in the Slack thread**

Expected within 2-4 minutes:
- A threaded reply from the bot with the PR URL.
- The PR exists on `https://github.com/Mperhats/centaur-lab/pulls` matching the title.

- [ ] **Step 4: Review and merge the PR**

```bash
gh pr view <number> -R Mperhats/centaur-lab --web
```

Sanity check the diff:
- Files all under `overlay/tools/acceptance_probe/`.
- `client.py` has a `_client()` factory.
- `pyproject.toml` has `[tool.centaur] module = "client.py"`.

Merge via the GitHub UI (or `gh pr merge <number> -R Mperhats/centaur-lab --squash --delete-branch`).

- [ ] **Step 5: Pull and rebuild locally**

```bash
git pull origin main
just up
```

- [ ] **Step 6: Verify the tool is live**

```bash
API_KEY=$(kubectl -n centaur-system get secret centaur-infra-env -o jsonpath='{.data.SLACKBOT_API_KEY}' | base64 -d)
kubectl -n centaur-system exec deploy/centaur-centaur-api -- \
  curl -s -H "X-Api-Key: $API_KEY" http://localhost:8000/tools \
  | jq '.[] | select(.name == "acceptance_probe")'

kubectl -n centaur-system exec deploy/centaur-centaur-api -- \
  curl -s -X POST http://localhost:8000/tools/acceptance_probe/ping \
  -H "X-Api-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{}'
# Expected: "real-slack-ok"
```

- [ ] **Step 7: Document the result**

Append to `docs/superpowers/specs/` a verification log entry capturing:
- The PR URL the agent opened.
- The total wall-clock time from Slack post to merged-and-live.
- Any gotchas observed (e.g., search indexing lag, token scope warnings, tunnel hiccups).
- Confirmation that `kubectl exec` returned `"real-slack-ok"`.

This is the only persistent record that the loop actually worked end-to-end; future regressions get diagnosed against this baseline.

- [ ] **Step 8: Clean up the throwaway tool**

Remove the acceptance probe so it doesn't ship in real images:

```bash
git rm -r overlay/tools/acceptance_probe/
git commit -m "chore(overlay): remove acceptance_probe (Task 7 verification artifact)"
git push origin main
just up
```

---

## What this plan deliberately does NOT do

| Out of scope | Why deferred | Where it would land later |
|---|---|---|
| CI to auto-rebuild + deploy on merge | The human-in-the-loop merge + `just up` is intentional. Letting an agent ship to production without any human signoff is a different threat model. | A `.github/workflows/overlay-deploy.yml` that runs on `push: main`, calls `just overlay::build` against a registry, and triggers Argo CD sync. |
| Multi-repo support (let `lab-eng` also edit a separate `infra/` repo) | Each repo doubles the surface (additional `repoCache.repositories` entry, additional persona, additional skill). One repo first proves the loop. | Add a second entry to `repoCache.repositories` and a second persona (`lab-infra`) with a different `default_repo`. |
| Auto-merge of agent PRs that pass CI | Same threat model concern as above. Centaur upstream has `self-improve` auto-merge based on path allowlists (`.centaur/services/api/tests/test_self_improve_daily.py:657`) — adapt that pattern, don't reinvent. | A future `.github/workflows/overlay-auto-merge.yml` using the same `_is_auto_merge_safe_path` check upstream uses. |
| Letting the agent edit the `.centaur/` submodule | Submodule bumps change the deployment surface in ways the user must review explicitly. | A separate persona (`lab-submodule-bumper`) with its own narrow workflow — out of scope here. |
| Sandbox-side `just up` to close the loop without human | Sandbox pods don't have kubectl access (and shouldn't). | Would require a separate CI runner with cluster access — same as the CI/Argo CD answer above. |
| Configuration to clone in multiple branches simultaneously | Each spawn is single-branch by design; the `agent-<ts>-<rand>` branch naming guarantees no collision. | If needed, the sandbox's `git-branch.sh` could take a branch arg, but the upstream pattern works fine. |

## Operator notes

- **The first `just up` after Task 2 takes longer.** The `repoCache` DaemonSet has to do an initial clone of `Mperhats/centaur-lab` before sandboxes can spawn with `AGENT_REPO`. Watch `kubectl -n centaur-system logs daemonset/centaur-centaur-repo-cache` — once "Cloning Mperhats/centaur-lab" finishes, you're ready.
- **`hostPath` on Docker Desktop:** the path `/var/lib/centaur/repos` lives inside the Docker Desktop VM, not your laptop's `/var/lib/`. Don't try to `ls` it from outside the cluster. To inspect, exec into the repo-cache pod (Task 2 Step 7).
- **Branch litter:** every `slack-loop-smoke` run leaves a new `agent-<ts>-<rand>-<rand>` branch and PR. The cleanup snippet in README Step 5 is essential — wire it into a periodic `just cleanup-probe-prs` if it becomes annoying.
- **Token rotation:** if you rotate `GITHUB_TOKEN`, you must also rotate it inside the `centaur-infra-env` Secret (re-run `just bootstrap-secrets`) AND restart the repo-cache DaemonSet (`kubectl -n centaur-system rollout restart daemonset/centaur-centaur-repo-cache`). The Secret is mounted into the pod, so the pod must restart to pick up the new value.

---

## Execution handoff

Plan saved to `docs/superpowers/plans/2026-05-25-slack-to-pr-loop.md`.

Tasks form a strict chain: Task 1 → 2 → 3 → 4 → 5 → 6 → 7. Each task has TDD-style verification (define test → see it fail → make change → see it pass → commit). The branch should be created via `superpowers:using-git-worktrees` before execution begins.

Per your request, the implementation will be driven by `superpowers:subagent-driven-development`: one implementer subagent per task, followed by a spec-compliance reviewer subagent and a code-quality reviewer subagent before moving to the next task. After Task 6, a final reviewer subagent will audit the entire implementation. Task 7 is operator-driven (real Slack) and runs after all subagent work merges.
