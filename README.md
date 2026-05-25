# centaur-lab

Local-first onboarding for [Centaur](https://github.com/paradigmxyz/centaur),
the production control plane for shared AI agents. The goal is a Claude Code
agent that replies with `PONG` through Centaur's durable agent API, on your
laptop, reachable from a real Slack workspace via a Cloudflare Tunnel —
without an overlay image or production GitOps.

The full design rationale lives in
[`docs/superpowers/specs/2026-05-25-centaur-lab-mvp-design.md`](docs/superpowers/specs/2026-05-25-centaur-lab-mvp-design.md).

## What this repo contains

| Path | Purpose |
|------|---------|
| `.centaur/` | Git submodule pinned at a specific `paradigmxyz/centaur` SHA. The base platform. |
| `overlay/` | Org-specific tools, skills, and a `Dockerfile` packaged into the `centaur-overlay:latest` image and mounted into the API + sandbox pods. See the [Tools](#tools-in-overlay) section below. |
| `values.local.yaml` | Helm chart overlay: env-var secrets, Claude Code default, Slackbot + Slack ETL enabled, local image-pull policies, overlay image reference. |
| `Justfile` | Thin wrapper over `.centaur/Justfile`. Only owns recipes that fill real upstream gaps — see the recipe-by-recipe `# comments` for the why. `just --list` shows everything grouped. |
| `.env.example` | Template for the shell env vars `bootstrap-secrets` reads. |
| `cloudflared/` | Cloudflare Tunnel routing, launchd agent template, and per-machine setup README. Tunnel auto-starts via `just cloudflared::install-service`. |
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
   `.centaur/contrib/chart/values.dev.yaml` plus our `values.local.yaml`
   overlay.

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
| Slack ETL workflows log token errors | `SLACK_ETL_TOKEN` unset or wrong; or its Slack user lacks `conversations.*` / `users.list` scopes. See [`docs/centaur/operate/slack-etl.md`](docs/centaur/operate/slack-etl.md). |

## Tools (in `overlay/`)

The `overlay/` directory is packaged into a local Docker image
(`centaur-overlay:latest`) and mounted into the API + sandbox pods at
`/app/overlay/org` and `/home/agent/overlay/org` respectively. The Helm
chart adds the overlay path to `TOOL_DIRS` so the API discovers anything
under `overlay/tools/` at startup; sandbox pods receive
`overlay/.agents/skills/` so Claude Code loads them as workspace skills.

The image is rebuilt as part of `just up` (`just overlay::build` chains in
front of `just deploy`); rebuilding by itself is `just overlay::build`,
followed by `just deploy` to pick up the new image.

| Tool | Purpose |
|------|---------|
| [`overlay/tools/semantic_scholar`](overlay/tools/semantic_scholar) | Search papers, fetch metadata, and walk the citation graph via the [Semantic Scholar Graph API](https://api.semanticscholar.org/api-docs/graph). Usable anonymously; set `SEMANTIC_SCHOLAR_API_KEY` in `.env` for higher quota. Companion playbook in `overlay/.agents/skills/academic-research/SKILL.md`. |

For background on the overlay model (how the image is built, how
`TOOL_DIRS` is assembled, how to verify discovery from the API pod), see
[`docs/centaur/extend/overlay.md`](docs/centaur/extend/overlay.md) and
[`docs/centaur/extend/tools.md`](docs/centaur/extend/tools.md).

## What this repo intentionally does NOT contain (yet)

| Future milestone | What it adds |
|------------------|--------------|
| Production infra | `infra/` Argo CD bootstrap pinned at the same chart SHA. |
| CI | Path-scoped GitHub Actions for overlay/infra changes. |
| Alternative harnesses | Either swap default harness to `pi-mono` or wire pi.dev RPC SDK as a tool. |

Each is one focused PR away on top of the current state. See the spec for
the full deferred-work table.

## License

See [`LICENSE`](LICENSE).
