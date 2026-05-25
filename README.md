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
| `.env.example` | Template for the shell env vars `bootstrap-secrets` reads. |
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

2. **Bootstrap the helm chart's subchart dependencies (one-time per machine
   for the repo add, one-time per checkout for the dep build).**

   ```bash
   helm repo add 1password https://1password.github.io/connect-helm-charts 2>/dev/null \
     || helm repo update 1password
   helm dependency build .centaur/contrib/chart
   ```

   The chart declares the 1Password Connect subchart in `Chart.yaml` even
   though we don't use it (we run in env-secret mode). Helm requires it to
   be present locally regardless. The downloaded tarball is git-ignored
   inside the submodule, so this does not dirty the pinned SHA.

3. **Create your local `.env`.**

   ```bash
   cp .env.example .env
   ```

   Fill in `ANTHROPIC_API_KEY` with a real Anthropic key. For each
   `replace-with-random-hex` placeholder, run `openssl rand -hex 32` and
   paste the output. The other env vars are ceremonial — required by the
   upstream bootstrap script's preconditions but never reach the network
   in env-mode + slackbot-disabled. The upstream script generates
   `SANDBOX_SIGNING_KEY` and `IRON_MANAGEMENT_API_KEY` itself and persists
   them in the existing `centaur-infra-env` Secret across subsequent
   `just up` runs, so you don't need to set them.

4. **Source the env so the variables are exported into your shell.**

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
