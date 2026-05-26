# centaur-lab

Local-first onboarding for [Centaur](https://github.com/paradigmxyz/centaur),
the production control plane for shared AI agents. The repo doubles as
the **organization overlay** — its surface mirrors
[`paradigmxyz/centaur-acme`](https://github.com/paradigmxyz/centaur-acme),
so `tools/`, `workflows/`, `.agents/skills/`, `services/`, the root
`Dockerfile`, root `pyproject.toml`, and root `tests/` are packaged
directly into the overlay image (or used to verify it locally).

The full design rationale lives in
[`docs/superpowers/specs/2026-05-25-centaur-lab-mvp-design.md`](docs/superpowers/specs/2026-05-25-centaur-lab-mvp-design.md).
ACME-mirror reorganization plan:
[`docs/superpowers/plans/2026-05-26-acme-mirror-reorg.md`](docs/superpowers/plans/2026-05-26-acme-mirror-reorg.md).

## What this repo contains

| Path | Purpose |
|------|---------|
| `tools/` | Org-specific tool plugins discovered by the API at startup. See [Tools](#tools). |
| `workflows/` | Durable, checkpoint-replayable workflow handlers. See [Workflows](#workflows). |
| `.agents/skills/` | Sandbox-loaded agent skills (one `SKILL.md` per directory). |
| `services/api/db/migrations/` | Overlay-side SQL migrations applied by the API on startup. |
| `services/sandbox/SYSTEM_PROMPT.md` | Org-specific sandbox prompt overlay. |
| `tests/` | Pytest suite at the repo root, ACME-style. |
| `Dockerfile` + `.dockerignore` | Single-`COPY .` overlay image. The `.dockerignore` is the source of truth for what does **not** ship. |
| `pyproject.toml` + `uv.lock` | Single-root uv project: aggregated dev/test deps + `centaur-sdk` (path-installed from `.centaur/centaur_sdk`) and a single `.venv` at the repo root. Per-tool `pyproject.toml` files are still authoritative for runtime dep resolution inside the API pod. |
| `.centaur/` | Git submodule pinned at a specific `paradigmxyz/centaur` SHA. Source of truth for the base platform **and** the `centaur-sdk` package. |
| `.scientist/` | Git submodule pinning [Sakana AI-Scientist-v2](https://github.com/SakanaAI/AI-Scientist-v2) for research-flow experiments. |
| `cloudflared/` | Cloudflare Tunnel routing, launchd agent template, and per-machine setup README. Has its own standalone `Justfile` (`cd cloudflared && just install-service`). |
| `docs/superpowers/` | Design specs + implementation plans. |
| `docs/TODO.md` | Actionable backlog. |
| `.env.example` | Template for the shell env vars the cluster bootstrap script reads. |
| `tmp/` | Local-only scratch (gitignored, also `.dockerignore`d). The pre-reorg per-tool/workflow test suites currently live at `tmp/tests-old/` for reference while we re-author them under `tests/`. |

The overlay image build context is the entire repo. `.dockerignore`
excludes everything that isn't an overlay extension point — submodules,
`cloudflared/`, `docs/`, `tmp/`, any local `values*.yaml`, lockfiles,
the root `.venv/`, and README-style markdown — while allow-listing the
runtime-loaded markdown under `.agents/skills/**` and
`services/**/SYSTEM_PROMPT.md`.

**Credential hygiene:** per the
[centaur-acme guidance](https://github.com/paradigmxyz/centaur-acme),
no credentials, secret values, `.env` files, or Helm values live in
this repo. Tools request secrets through Centaur's secret system
(`secret("…")` placeholders resolved by iron-proxy / iron-token-broker
at the network boundary). Helm values for any cluster live in a
sibling GitOps / infra repo (e.g. `centaur-lab-infra` shaped after
[`paradigmxyz/centaur-acme-infra`](https://github.com/paradigmxyz/centaur-acme-infra)),
not here.

## Prerequisites

- macOS or Linux
- Docker
- A local Kubernetes cluster reachable from `kubectl`. Docker Desktop with
  Kubernetes enabled is the simplest path; `kind`, `k3d`, and `minikube` also
  work — the upstream chart targets generic local k8s.
- `brew install uv kubectl helm jq` (or your distro's equivalents)
- An Anthropic API key (Claude Code is the default harness)

## Setup

1. **Clone with submodules.**

   ```bash
   git clone https://github.com/<your-org>/centaur-lab
   cd centaur-lab
   git submodule update --init --recursive
   ```

2. **Create the root `.venv`.**

   ```bash
   uv sync
   ```

   Installs the aggregated runtime deps (every tool's runtime
   requirements) plus the dev group (pytest, pytest-asyncio, ruff) into
   `.venv/` at the repo root. From here, `uv run pytest tests/`,
   `uv run ruff check .`, or `uv run python -m tools.semantic_scholar.cli ...`
   all work without per-tool venvs.

3. **Pull the chart's subchart tarballs.** The chart declares the 1Password
   Connect subchart even though we run in env-secret mode; Helm still
   requires it locally. One-time per checkout:

   ```bash
   helm repo add 1password https://1password.github.io/connect-helm-charts 2>/dev/null \
     || helm repo update 1password
   helm dependency build .centaur/contrib/chart
   ```

4. **Create your local `.env`.**

   ```bash
   cp .env.example .env
   ```

   Fill in the placeholders:

   | Var | Required? | Source |
   |-----|-----------|--------|
   | `ANTHROPIC_API_KEY` | Yes (default harness is `claude-code`) | console.anthropic.com |
   | `OPENAI_API_KEY` | Optional (enables `--codex` selector) | platform.openai.com |
   | `SLACK_BOT_TOKEN` | Yes (Slackbot is enabled) | Slack App -> OAuth & Permissions -> Bot User OAuth Token |
   | `SLACK_SIGNING_SECRET` | Yes (Slackbot is enabled) | Slack App -> Basic Information -> App Credentials |
   | `SLACK_ETL_TOKEN` | Yes (Slack ETL is enabled) | Slack user token with `conversations.*` + `users.list` scopes |
   | `SEMANTIC_SCHOLAR_API_KEY` | Optional (boosts quota for the `semantic_scholar` tool) | [Semantic Scholar API form](https://www.semanticscholar.org/product/api#api-key-form) |
   | `OP_SERVICE_ACCOUNT_TOKEN` / `OP_VAULT` / `SLACKBOT_API_KEY` | Yes (ceremonial) | `openssl rand -hex 32` each |

## Build the overlay image locally

```bash
docker build -t centaur-overlay:dev .
```

Build context is the whole repo; `.dockerignore` keeps it small (~150 KB).
Image content is `/overlay/{tools,workflows,.agents,services}` plus a few
small static files. Inspect with:

```bash
docker run --rm centaur-overlay:dev sh -c 'ls /overlay && ls /overlay/tools /overlay/workflows /overlay/.agents/skills /overlay/services'
```

## Deploying to a local cluster

> **Note:** The cluster-orchestration recipes (Helm install/uninstall,
> kubectl rollout, sandbox cleanup, deployed-API smoke harness, the
> `bootstrap-secrets` script that patches `centaur-infra-env`) used to
> live in a root `Justfile`. They were removed to align this repo with
> the [`paradigmxyz/centaur-acme`](https://github.com/paradigmxyz/centaur-acme)
> overlay shape — cluster lifecycle ultimately lives in a sibling
> GitOps / infra repo (shaped after
> [`paradigmxyz/centaur-acme-infra`](https://github.com/paradigmxyz/centaur-acme-infra)),
> not in the overlay. The git history of `Justfile` on the
> `chore/reorganize` branch is the canonical reference for any recipe
> you want to bring back.

For local laptop-cluster boot, the upstream submodule's `Justfile` covers
most of what's needed. Provide your own `values.local.yaml` (gitignored)
for any laptop-specific overrides — start from
[`.centaur/contrib/chart/values.dev.yaml`](.centaur/contrib/chart/values.dev.yaml)
and add only the diffs you need for your local environment (typically:
flip pull policies to `IfNotPresent`, point `repoCache.hostPath` at a
local directory, set the overlay image tag).

```bash
cd .centaur
just bootstrap-secrets        # creates centaur-infra-env from your shell env
just build                    # builds the upstream Centaur images
cd ..
docker build -t centaur-overlay:dev .
helm dependency update .centaur/contrib/chart
helm upgrade --install centaur .centaur/contrib/chart \
    --namespace centaur-system --create-namespace \
    -f .centaur/contrib/chart/values.dev.yaml \
    -f values.local.yaml \
    --set overlay.image.tag=dev \
    --set overlay.image.repository=centaur-overlay
```

`values*.yaml` at the repo root is gitignored and excluded from the
overlay image build context, so a local file is safe to keep alongside
the rest of the repo without leaking into history or the published
image.

Verify the pods are healthy:

```bash
kubectl get pods -n centaur-system
```

Expected: `centaur-centaur-api`, `centaur-iron-proxy`, `centaur-centaur-slackbot`,
and Postgres pods are running.

For Slack and workflow webhooks (GitHub, etc.) to reach the cluster, the
Cloudflare Tunnel must be live and two local ports must be forwarded:

| Public path | Forwarded port | Backend |
|---|---|---|
| `/api/webhooks/slack` | `localhost:3001` | Slackbot pod |
| everything else | `localhost:8000` | Centaur API pod |

The tunnel runs as a launchd user agent installed once via the
[`cloudflared/`](cloudflared/) Justfile (`cd cloudflared && just install-service`).
Per-session port-forward + log-tail commands live in the same directory's
README.

## Smoke test

```bash
thread_key="smoke-$(date +%s)"
api_deploy="deploy/centaur-centaur-api"
exec_curl() {
  kubectl exec -n centaur-system "$api_deploy" -- sh -c \
    'curl -s "$@" -H "X-Api-Key: $SLACKBOT_API_KEY"' -- "$@"
}
spawn=$(exec_curl -X POST http://localhost:8000/agent/spawn \
  -H 'Content-Type: application/json' \
  -d "{\"thread_key\":\"${thread_key}\"}")
# ... see git log for the full smoke driver
```

The full deployed-smoke driver was previously the root `Justfile`'s
`just smoke` recipe — recover it from git history if you need it back.

## Local checks

```bash
uv run pytest tests/         # ACME-style suite at the root
uv run ruff check .          # lint everything
docker build -t centaur-overlay:dev .  # build the overlay image
```

## Tools

The repo root `tools/` directory is mounted into the API + sandbox pods at
`/app/overlay/org/tools` and `/home/agent/overlay/org/tools` respectively.
The Helm chart adds the overlay path to `TOOL_DIRS` so the API discovers
anything under `tools/` at startup.

| Tool | Purpose |
|------|---------|
| [`tools/semantic_scholar`](tools/semantic_scholar) | Search papers, fetch metadata, walk the citation graph, and build persisted research briefs via the [Semantic Scholar Graph API](https://api.semanticscholar.org/api-docs/graph). Discoverable methods include `search_papers` / `get_paper` / `get_references` (live API), `search` (agent-facing live search with an error envelope), and `research_brief` (one-call S2 search + Markdown lit-review render + upsert of the brief plus each underlying paper as parent/child rows). Companion playbook in `.agents/skills/academic-research/SKILL.md`. |
| [`tools/pdf`](tools/pdf) | Fetch + parse open-access PDFs to Markdown for full-text indexing (pymupdf4llm → pymupdf → pypdf fallback chain). |

For background on the overlay model, see [Using an overlay](https://centaur.run/extend/overlay) and [Creating tools](https://centaur.run/extend/tools).

## Workflows

Workflows are durable, checkpoint-replayable handlers shipped via the
same `centaur-overlay:sha-*` image as the tools. They're auto-discovered
on API startup from `WORKFLOW_DIRS=/app/workflows:/app/overlay/org/workflows`,
so dropping a new file in `workflows/` and rebuilding the image is
all that's needed to register one.

| Workflow | Purpose |
|----------|---------|
| [`workflows/save_papers.py`](workflows/save_papers.py) | Upsert one or more Semantic Scholar paper IDs into `company_context_documents` as `source_type="paper"` rows; idempotent on content hash. |
| [`workflows/research_brief.py`](workflows/research_brief.py) | Search Semantic Scholar, render a Markdown lit-review brief, and upsert the brief plus each underlying paper as parent/child rows in `company_context_documents`. |
| [`workflows/archive_papers.py`](workflows/archive_papers.py) | Fetch the open-access PDF for a paper, parse to Markdown, persist to `paper_archives` and `company_context_documents` for full-text retrieval. |
| [`workflows/search_and_archive_papers.py`](workflows/search_and_archive_papers.py) | Atomic search-then-archive-everything-matched: searches S2, dispatches `archive_papers` as a child run for every matched ID. |

### Triggering workflows

From a Slack-driven agent in the sandbox, use the documented `call workflow run` shape:

```
call workflow run '{"workflow_name":"save_papers","input":{"paper_ids":["..."]}}'
call workflow run '{"workflow_name":"research_brief","input":{"query":"...","limit":5}}'
```

From the host (against the live cluster), POST directly via `kubectl exec` so
the in-pod `$SLACKBOT_API_KEY` covers auth:

```bash
kubectl exec -n centaur-system deploy/centaur-centaur-api -- sh -c \
  'curl -sS -X POST http://localhost:8000/workflows/runs \
    -H "X-Api-Key: $SLACKBOT_API_KEY" \
    -H "Content-Type: application/json" \
    -d "{\"workflow_name\":\"save_papers\",\"input\":{\"paper_ids\":[\"<paper_id>\"]}}"' | jq .
```

Both workflows write to `company_context_documents`, which is BM25-indexed
via paradedb — so persisted papers and briefs are immediately
future-RAG-ready for retrieval across turns.

## Migrating legacy tests

The pre-reorg per-tool/per-workflow test suites are staged locally at
`tmp/tests-old/` (gitignored). To migrate one:

1. Copy the test file to `tests/`.
2. Update its imports to be repo-rooted: `from tools.semantic_scholar.client import ...`,
   `from workflows.research_brief import handler`.
3. Run `uv run pytest tests/<file>::<test>` and fix anything that breaks.
4. Delete the old copy from `tmp/tests-old/` once it's been re-authored.

The smoke test in [`tests/test_smoke.py`](tests/test_smoke.py) is the
ACME-style template — keep new tests small, well-isolated, and prefer
the same import shape.

## CI

`.github/workflows/overlay.yml` runs on push to `main` and on PRs that
touch overlay code. It's now a single `uv sync && uv run ruff check . &&
uv run pytest tests/` against the root `.venv`, then a `docker build`
+ GHCR push on merges to `main`.

## License

See [`LICENSE`](LICENSE).
