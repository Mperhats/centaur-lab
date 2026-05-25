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

# Create the centaur-infra-env Kubernetes Secret from your shell env, then
# patch in ANTHROPIC_API_KEY. The upstream bootstrap-k8s-secrets.sh hardcodes
# which keys land in the Secret and does not include ANTHROPIC_API_KEY;
# iron-proxy in env-mode reads it from this Secret to inject on outbound
# calls to api.anthropic.com. Requires: source .env first.
bootstrap-secrets:
    #!/usr/bin/env bash
    set -euo pipefail
    cd .centaur && just bootstrap-secrets
    # `tr -d '\n'` matters on Linux: GNU coreutils `base64` wraps at 76
    # columns by default, which would inject a newline into the JSON value
    # below and break `kubectl patch`. macOS base64 doesn't wrap; the
    # `tr` is a no-op there.
    encoded=$(printf '%s' "${ANTHROPIC_API_KEY}" | base64 | tr -d '\n')
    kubectl -n "${CENTAUR_NAMESPACE:-centaur-system}" patch secret centaur-infra-env --type merge \
      -p "{\"data\":{\"ANTHROPIC_API_KEY\":\"${encoded}\"}}"

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

# Forward the Slackbot Service to localhost:3001 so the Cloudflare Tunnel
# (or any local HTTP client) can reach it. Blocks; run in its own terminal.
port-forward:
    kubectl port-forward -n centaur-system svc/centaur-centaur-slackbot 3001:3001

# Run the Cloudflare Tunnel that exposes localhost:3001 at
# https://centaur.local-labs.xyz. Blocks; run in its own terminal alongside
# `just port-forward`. See cloudflared/README.md for one-time setup.
tunnel:
    cloudflared tunnel --config cloudflared/config.yml run centaur-dev
