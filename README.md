# centaur-lab

Local-first onboarding for [Centaur](https://github.com/paradigmxyz/centaur),
the production control plane for shared AI agents. The goal is a Claude Code
agent that replies with `PONG` through Centaur's durable agent API, on your
laptop, reachable from a real Slack workspace via a Cloudflare Tunnel.

The full design rationale lives in
[`docs/superpowers/specs/2026-05-25-centaur-lab-mvp-design.md`](docs/superpowers/specs/2026-05-25-centaur-lab-mvp-design.md).
Deploy alignment plan: [`docs/superpowers/plans/2026-05-26-centaur-lab-deploy-alignment.md`](docs/superpowers/plans/2026-05-26-centaur-lab-deploy-alignment.md).

## What this repo contains

| Path | Purpose |
|------|---------|
| `.centaur/` | Git submodule pinned at a specific `paradigmxyz/centaur` SHA. The base platform. |
| `overlay/` | Org-specific tools, **workflows**, skills, and a `Dockerfile` packaged into `centaur-overlay:sha-<git-short>` and mounted into the API + sandbox pods. See [Tools](#tools-in-overlay) and [Workflows](#workflows-in-overlay). |
| `values.org.yaml` | Org chart overlay: harness, Slackbot, Slack ETL, overlay repo name, repoCache. |
| `values.local.yaml` | Laptop-only overrides: image pull policies, warm pool off, Docker Desktop paths. |
| `infra/` | Prod GitOps skeleton (Argo CD + pinned image tags). Not used locally — see [`infra/README.md`](infra/README.md). |
| `Justfile` | Thin wrapper over `.centaur/Justfile`. Owns `up`, `deploy`, `reload`, and org overlay recipes. `just --list` shows everything grouped. |
| `.env.example` | Template for the shell env vars `bootstrap-secrets` reads. |
| `cloudflared/` | Cloudflare Tunnel routing, launchd agent template, and per-machine setup README. Tunnel auto-starts via `just cloudflared::install-service`. |
| `docs/TODO.md` | Actionable backlog (infra fixes, deletion notes). |
| `docs/superpowers/` | Design spec + deploy alignment plan. |

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

2. **Pull the chart's subchart tarballs.** The chart declares the 1Password
   Connect subchart even though we run in env-secret mode; Helm still
   requires it locally. One-time per checkout:

   ```bash
   helm repo add 1password https://1password.github.io/connect-helm-charts 2>/dev/null \
     || helm repo update 1password
   helm dependency build .centaur/contrib/chart
   ```

3. **Create your local `.env`.**

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
   | `SEMANTIC_SCHOLAR_API_KEY` | Optional (boosts quota for the `semantic_scholar` overlay tool) | [Semantic Scholar API form](https://www.semanticscholar.org/product/api#api-key-form) |
   | `OP_SERVICE_ACCOUNT_TOKEN` / `OP_VAULT` / `SLACKBOT_API_KEY` | Yes (ceremonial) | `openssl rand -hex 32` each |

   The upstream script generates `SANDBOX_SIGNING_KEY` and
   `IRON_MANAGEMENT_API_KEY` itself and persists them across subsequent
   `just up` runs, so you don't need to set them.

   No `source .env` step: the Justfile sets `dotenv-load := true` (matching
   upstream's `.centaur/Justfile`), so every recipe loads `.env` automatically.

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
   `.centaur/contrib/chart/values.dev.yaml` plus `values.org.yaml` and
   `values.local.yaml`. Overlay tag is set from `overlay/.tag` (written by
   `just overlay::build`) or `sha-<git-short>` as fallback.

Verify the pods are healthy:

```bash
kubectl get pods -n centaur-system
# or, against the submodule's status recipe:
cd .centaur && just status
```

Expected: `centaur-centaur-api`, `centaur-iron-proxy`, `centaur-centaur-slackbot`,
and Postgres pods are running.

For Slack — and for any workflow webhook (GitHub, etc.) — to reach the
cluster, the Cloudflare Tunnel must be live and two local ports must be
forwarded:

| Public path | Forwarded port | Backend |
|---|---|---|
| `/api/webhooks/slack` | `localhost:3001` | Slackbot pod |
| everything else | `localhost:8000` | Centaur API pod (workflow webhooks, `/workflows/runs`, `/agent/*`) |

The tunnel runs as a launchd user agent (`com.local-labs.centaur-tunnel`)
installed once via `just cloudflared::install-service` — it auto-starts on
login and restarts on crash. Both port-forwards are per-session and bundled
with the Slackbot log tail in:

```bash
just dev
```

Ctrl-C `just dev` to stop the port-forwards; the tunnel keeps running.

One-time per-machine setup (`brew install cloudflared`, `cloudflared tunnel
login`, `cloudflared tunnel create centaur-dev`, DNS routing, and
`just cloudflared::install-service`) lives in
[`cloudflared/README.md`](cloudflared/README.md), which also documents how
to add or reorder ingress rules.

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

To exercise the Codex harness instead, mention the bot in Slack with the
selector: `@centaur --codex reply with exactly PONG`. Requires `OPENAI_API_KEY`
in `.env` so `bootstrap-secrets` can patch it into the Secret.

## Tear down

```bash
just down            # prompts for confirmation (safety net)
just --yes down      # skip the prompt; useful in scripts/CI
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
| `bootstrap-secrets` complains about missing variables | Did you fill in `ANTHROPIC_API_KEY` in `.env`? The Justfile auto-loads `.env` via `dotenv-load`; no `source` step needed, but the variables must actually be set. |
| `ImagePullBackOff` on `centaur-centaur-api`, `centaur-api-proxy`, or a sandbox pod | The chart defaults locally-built `:latest` images to `pullPolicy: Always`; `values.local.yaml` overrides for `api`, `ironProxy`, `sandbox`. Re-run `just up`. |
| Slack URL verification fails ("didn't respond with the value of the challenge parameter") | `SLACK_SIGNING_SECRET` in `.env` does not match the value in the Slack app's Basic Information page. |
| Pods crash-loop with `OOMKilled` | Local cluster is too small. Bump CPU/memory in Docker Desktop or kind config. |
| Smoke test never completes | `cd .centaur && just logs api` for the API container; `kubectl get pods -n centaur-system -l centaur.ai/managed=true` for sandbox state. |
| Smoke fails with `Missing API key` | You're running upstream's `just smoke` (e.g. `cd .centaur && just smoke`). The current chart's API rejects all unauthenticated calls; our root `just smoke` injects `X-Api-Key: $SLACKBOT_API_KEY` to compensate. Always invoke from the repo root. |
| `helm get values` does not show `defaultHarness` | The pinned base SHA may not expose the key yet — see [open question 2 in the spec](docs/superpowers/specs/2026-05-25-centaur-lab-mvp-design.md#open-questions-for-implementation). Pass `--claude` manually in the smoke prompt as a workaround. |
| Slack ETL workflows log token errors | `SLACK_ETL_TOKEN` unset or wrong; or its Slack user lacks `conversations.*` / `users.list` scopes. See [Slack ETL docs](https://centaur.run/operate/slack-etl). |
| Overlay tools/workflows/skills look stale after a code edit | **`just reload`** — rebuild overlay, `helm deploy` with new `overlay.image.tag`, delete Slack Sandbox CRDs. Tools-only: `just overlay::reload-api`. Skills-only: `just overlay::reload-skills`. |

## Tools (in `overlay/`)

The `overlay/` directory is packaged into a local Docker image
(`centaur-overlay:sha-<git-short>`) and mounted into the API + sandbox pods at
`/app/overlay/org` and `/home/agent/overlay/org` respectively. The Helm
chart adds the overlay path to `TOOL_DIRS` so the API discovers anything
under `overlay/tools/` at startup; sandbox pods receive
`overlay/.agents/skills/` so Claude Code loads them as workspace skills.

The image is rebuilt as part of `just up` (`just overlay::build` writes
`overlay/.tag` before `just deploy`). After editing overlay code on a running
cluster, use **`just reload`** — rebuild, deploy with the new sha tag (rolls
API pods via Helm), and delete Slack Sandbox CRDs. The next Slack turn
cold-spawns with the new overlay. For demo/smoke leftovers:
`just clean-sandboxes` (all) or `just clean-sandboxes slack`.

| Tool | Purpose |
|------|---------|
| [`overlay/tools/semantic_scholar`](overlay/tools/semantic_scholar) | Search papers, fetch metadata, walk the citation graph, and build persisted research briefs via the [Semantic Scholar Graph API](https://api.semanticscholar.org/api-docs/graph). Usable anonymously; set `SEMANTIC_SCHOLAR_API_KEY` in `.env` for higher quota. Discoverable methods include `search_papers` / `get_paper` / `get_references` (live API), `search` (agent-facing live search with an error envelope), and `research_brief` (one-call S2 search + Markdown lit-review render + upsert of the brief plus each underlying paper as parent/child rows). Companion playbook in `overlay/.agents/skills/academic-research/SKILL.md`. |

For background on the overlay model, see [Using an overlay](https://centaur.run/extend/overlay) and [Creating tools](https://centaur.run/extend/tools).

## Workflows (in `overlay/`)

Overlay workflows are durable, checkpoint-replayable handlers shipped via
the same `centaur-overlay:sha-*` image as the tools. They're auto-discovered
on API startup from `WORKFLOW_DIRS=/app/workflows:/app/overlay/org/workflows`,
so dropping a new file in `overlay/workflows/` and rebuilding the image is
all that's needed to register one.

| Workflow | Purpose |
|----------|---------|
| [`overlay/workflows/save_papers.py`](overlay/workflows/save_papers.py) | Upsert one or more Semantic Scholar paper IDs into `company_context_documents` as `source_type="paper"` rows; idempotent on content hash. |
| [`overlay/workflows/research_brief.py`](overlay/workflows/research_brief.py) | Search Semantic Scholar, render a Markdown lit-review brief, and upsert the brief plus each underlying paper as parent/child rows in `company_context_documents`. |

### Triggering workflows

From a Slack-driven agent in the sandbox, use the documented `call workflow run` shape:

```
call workflow run '{"workflow_name":"save_papers","input":{"paper_ids":["..."]}}'
call workflow run '{"workflow_name":"research_brief","input":{"query":"...","limit":5}}'
```

From the host (against the live cluster), the overlay Justfile wraps the
same POST against the in-pod API:

```bash
just overlay::smoke-save-papers <paper_id>
just overlay::smoke-research-brief-deployed "<query>"
```

For a lighter local loop that drives the same `research_brief` tool
method without going through the workflow router or the cluster, run
`just overlay::smoke-research-brief "<query>"` — it shells out to the
standalone semantic_scholar CLI subcommand and persists against
whatever `DATABASE_URL` is in scope (port-forward to `centaur_test`
recommended).

Both workflows write to `company_context_documents`, which is BM25-indexed
via paradedb and has a GIN index on `metadata` — so persisted papers and
briefs are immediately future-RAG-ready for retrieval across turns.

### Testing workflows locally

Unit tests for `save_papers` and `research_brief` mock the database and
S2 client and run via `just overlay::test-workflows`. A separate
`just overlay::test-workflows-integration` recipe exercises the same
handlers against a real Postgres (with the centaur schema and `pg_search`
migrations applied), gated on `CENTAUR_TEST_DATABASE_URL`. Point that env
var at a port-forwarded cluster Postgres (recipe in
[`db/README.md`](db/README.md)) and the integration tests run; leave it
unset and they skip cleanly.

## CI and production

| Path | Purpose |
|------|---------|
| `.github/workflows/overlay.yml` | Lint, test, and on merge to `main` push `ghcr.io/<repo>/centaur-overlay:sha-*`. |
| `infra/` | Argo CD Application template + prod `centaur.yaml` values (placeholders). Laptop dev does not use this. |

## What this repo intentionally defers

| Future milestone | What it adds |
|------------------|--------------|
| Filled prod infra | Replace `<PLACEHOLDER>` tags in `infra/` and apply via Argo CD. |
| Alternative harnesses | Either swap default harness to `pi-mono` or wire pi.dev RPC SDK as a tool. |

Each is one focused PR away on top of the current state. See the spec for
the full deferred-work table.

## License

See [`LICENSE`](LICENSE).
