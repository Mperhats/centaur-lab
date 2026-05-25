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
