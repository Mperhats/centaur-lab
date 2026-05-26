set dotenv-load := true

export CENTAUR_NAMESPACE := env_var_or_default("CENTAUR_NAMESPACE", "centaur-system")
export CENTAUR_RELEASE := env_var_or_default("CENTAUR_RELEASE", "centaur")
centaur := ".centaur"

# Cloudflare Tunnel lifecycle (install-service, status, logs, run, ...).
# Lives in cloudflared/Justfile so all tunnel concerns stay in one place.
mod cloudflared 'cloudflared/Justfile'

# Overlay image build/lint/CLI-smoke. Lives in overlay/Justfile so all
# org-overlay recipes stay in one place; reachable as `just overlay::<recipe>`.
mod overlay 'overlay/Justfile'

default:
    just --list

[group('lifecycle')]
up: bootstrap-secrets
    cd {{centaur}} && just build
    just overlay::build
    just deploy

# Deploy the chart with three overlay-image modes (precedence top-down):
#
#   1. $OVERLAY_TAG          — explicit pin, e.g. `OVERLAY_TAG=sha-1b6cb08 just deploy`
#                              Uses values.org.yaml's GHCR repository as-is.
#   2. overlay/.tag          — written by `just overlay::build`. Local Docker
#                              Desktop image; we override the repository back
#                              to bare `centaur-overlay` so the kubelet finds
#                              the host-cached image instead of trying GHCR.
#   3. sha-<origin/main>     — default. `git fetch origin main` + short sha
#                              matches the tag CI publishes on every merge.
#                              `just refresh-overlay` is the one-command
#                              "pull latest main + roll API" version of this.
#
# GHCR is private — the chart's pods reference `ghcr-pull` via global.imagePullSecrets
# in values.org.yaml. Run `just bootstrap-ghcr-pull-secret` once (or just
# `just bootstrap-secrets`, which calls it) before mode 3 will work.
#
# Deploy the chart; resolves overlay image tag from $OVERLAY_TAG > overlay/.tag > origin/main sha.
[group('lifecycle')]
[working-directory('.centaur')]
deploy:
    #!/usr/bin/env bash
    set -euo pipefail
    set_overrides=()
    if [[ -n "${OVERLAY_TAG:-}" ]]; then
      overlay_tag="$OVERLAY_TAG"
      mode="explicit \$OVERLAY_TAG"
    elif [[ -f ../overlay/.tag ]]; then
      overlay_tag=$(tr -d '[:space:]' < ../overlay/.tag)
      # Local-build mode: image lives only in host Docker daemon, never GHCR.
      set_overrides+=(--set "overlay.image.repository=centaur-overlay")
      mode="local build (overlay/.tag)"
    else
      # Default: pull whatever sha the latest publish job pushed to GHCR.
      git -C .. fetch origin main --quiet 2>/dev/null || true
      overlay_tag="sha-$(git -C .. rev-parse --short=7 origin/main)"
      mode="GHCR (origin/main)"
    fi
    echo "Deploying overlay image tag=${overlay_tag}  mode=${mode}"
    helm dependency update contrib/chart >/dev/null
    helm upgrade --install $CENTAUR_RELEASE contrib/chart \
        --namespace $CENTAUR_NAMESPACE --create-namespace \
        -f contrib/chart/values.dev.yaml \
        -f ../values.org.yaml \
        -f ../values.local.yaml \
        --set "overlay.image.tag=${overlay_tag}" \
        "${set_overrides[@]}"

# Fetch the latest centaur-overlay image published to GHCR for origin/main
# and roll the API + recycle Slack sandboxes so the next agent turn picks
# it up. This is the standard post-merge workflow:
#
#     # after PR is squash-merged to main and the Overlay workflow goes green
#     just refresh-overlay
#
# Removes overlay/.tag (the local-build sentinel) so a stale build from a
# sibling worktree can't pin the deploy to an image that isn't in GHCR.
#
# Pull the latest GHCR overlay image for origin/main, roll the API, recycle Slack sandboxes.
[group('lifecycle')]
refresh-overlay:
    #!/usr/bin/env bash
    set -euo pipefail
    rm -f overlay/.tag
    just deploy
    deploy="${CENTAUR_RELEASE}-centaur-api"
    echo "Restarting API deployment/${deploy} so it pulls the new overlay image …"
    kubectl rollout restart "deployment/${deploy}" -n "$CENTAUR_NAMESPACE"
    kubectl rollout status "deployment/${deploy}" -n "$CENTAUR_NAMESPACE" --timeout=120s
    just overlay::clean-sandboxes slack
    echo ""
    echo "Verify in-cluster:"
    echo "  kubectl describe deployment/${deploy} -n $CENTAUR_NAMESPACE | grep -A1 'overlay-bootstrap'"

# Materialise (or refresh) the docker-registry secret the chart references
# as `ghcr-pull` (see global.imagePullSecrets in values.org.yaml). Required
# because the centaur-overlay GHCR package is private.
#
# Reads from env:
#   GHCR_USERNAME — GitHub username (defaults to Mperhats)
#   GHCR_TOKEN    — PAT with read:packages scope
#
# Both come from .env (see .env.example). Idempotent — re-run after rotating
# the PAT and the imagePullSecret on every overlay-using pod refreshes
# automatically on the next rollout.
#
# Materialise (or refresh) the ghcr-pull docker-registry secret from GHCR_USERNAME / GHCR_TOKEN.
[group('lifecycle')]
bootstrap-ghcr-pull-secret:
    #!/usr/bin/env bash
    set -euo pipefail
    user="${GHCR_USERNAME:-Mperhats}"
    token="${GHCR_TOKEN:-}"
    if [[ -z "$token" ]]; then
      echo "skip ghcr-pull (GHCR_TOKEN unset — overlay image pulls from GHCR will 401)"
      exit 0
    fi
    kubectl get namespace "$CENTAUR_NAMESPACE" >/dev/null 2>&1 \
      || kubectl create namespace "$CENTAUR_NAMESPACE" >/dev/null
    kubectl create secret docker-registry ghcr-pull \
        --namespace "$CENTAUR_NAMESPACE" \
        --docker-server=ghcr.io \
        --docker-username="$user" \
        --docker-password="$token" \
        --dry-run=client -o yaml \
      | kubectl apply -f - >/dev/null
    echo "applied ghcr-pull (user=${user}) in namespace ${CENTAUR_NAMESPACE}"

# Run upstream bootstrap, patch in keys it does not handle, then ensure the
# GHCR pull secret exists so the overlay initContainer can pull from GHCR.
[group('lifecycle')]
bootstrap-secrets:
    #!/usr/bin/env bash
    set -euo pipefail
    (cd {{centaur}} && just bootstrap-secrets)
    patch_key() {
      local key=$1
      local val=${!key:-}
      if [ -z "$val" ]; then
        echo "skip ${key} (unset)"
        return 0
      fi
      # GNU base64 wraps at 76 cols; strip newlines so the patch JSON stays valid.
      local encoded
      encoded=$(printf '%s' "$val" | base64 | tr -d '\n')
      kubectl -n $CENTAUR_NAMESPACE patch secret centaur-infra-env --type merge \
        -p "{\"data\":{\"${key}\":\"${encoded}\"}}" >/dev/null
      echo "patched ${key}"
    }
    patch_key ANTHROPIC_API_KEY
    patch_key OPENAI_API_KEY
    patch_key SLACK_ETL_TOKEN
    patch_key GITHUB_WEBHOOK_SECRET
    patch_key GITHUB_TOKEN
    patch_key SEMANTIC_SCHOLAR_API_KEY
    patch_key LOCAL_DEV_API_KEY
    just bootstrap-ghcr-pull-secret

[group('lifecycle')]
[confirm("Uninstall " + CENTAUR_RELEASE + " from " + CENTAUR_NAMESPACE + "? Pass --yes to skip this prompt. ")]
down:
    helm uninstall $CENTAUR_RELEASE --namespace $CENTAUR_NAMESPACE

# Cluster health at a glance (mirrors upstream `.centaur/Justfile status`).
[group('lifecycle')]
status:
    kubectl get all -n $CENTAUR_NAMESPACE

# Rebuild overlay image + deploy (sha tag roll) + tear down Slack sandboxes.
# Implemented in overlay/Justfile; exposed here for discoverability.
[group('lifecycle')]
reload:
    just overlay::reload

# filter: all (default) | slack
[group('lifecycle')]
clean-sandboxes filter="all":
    just overlay::clean-sandboxes {{filter}}

# Upstream `just smoke` with X-Api-Key added — current chart rejects unauthed localhost.
[group('dev')]
smoke:
    #!/usr/bin/env bash
    set -euo pipefail
    thread_key="smoke-$(date +%s)"
    api_deploy="deploy/${CENTAUR_RELEASE}-centaur-api"
    exec_curl() {
      kubectl exec -n $CENTAUR_NAMESPACE "$api_deploy" -- sh -c \
        'curl -s "$@" -H "X-Api-Key: $SLACKBOT_API_KEY"' -- "$@"
    }

    spawn=$(exec_curl -X POST http://localhost:8000/agent/spawn \
      -H "Content-Type: application/json" \
      -d "{\"thread_key\":\"${thread_key}\"}")
    assignment_generation=$(printf '%s' "$spawn" | jq -r '.assignment_generation')

    exec_curl -X POST http://localhost:8000/agent/message \
      -H "Content-Type: application/json" \
      -d "{\"thread_key\":\"${thread_key}\",\"assignment_generation\":${assignment_generation},\"role\":\"user\",\"parts\":[{\"type\":\"text\",\"text\":\"Reply with exactly PONG and nothing else.\"}]}" >/dev/null

    execute=$(exec_curl -X POST http://localhost:8000/agent/execute \
      -H "Content-Type: application/json" \
      -d "{\"thread_key\":\"${thread_key}\",\"assignment_generation\":${assignment_generation},\"delivery\":{\"platform\":\"dev\"}}")
    execution_id=$(printf '%s' "$execute" | jq -r '.execution_id')

    for _ in $(seq 1 60); do
      state=$(exec_curl "http://localhost:8000/agent/executions/${execution_id}")
      status=$(printf '%s' "$state" | jq -r '.status // empty')
      case "$status" in
        completed)
          printf '%s\n' "$state" | jq
          printf '%s\n' "$state" | jq -e '.result_text | contains("PONG")' >/dev/null
          exit 0
          ;;
        failed|failed_permanent|cancelled)
          printf '%s\n' "$state" | jq
          exit 1
          ;;
      esac
      sleep 2
    done

    exec_curl "http://localhost:8000/agent/executions/${execution_id}" | jq
    echo "smoke timed out waiting for execution ${execution_id}" >&2
    exit 1

# Per-session dev loop: port-forward Slackbot (:3001) + API (:8000), tail Slackbot logs. Tunnel auto-runs as a launch agent.
[group('dev')]
dev:
    #!/usr/bin/env bash
    set -euo pipefail
    slackbot_log=/tmp/centaur-port-forward-slackbot.log
    api_log=/tmp/centaur-port-forward-api.log
    kubectl port-forward -n $CENTAUR_NAMESPACE svc/${CENTAUR_RELEASE}-centaur-slackbot 3001:3001 \
      >"$slackbot_log" 2>&1 &
    slackbot_pid=$!
    kubectl port-forward -n $CENTAUR_NAMESPACE svc/${CENTAUR_RELEASE}-centaur-api 8000:8000 \
      >"$api_log" 2>&1 &
    api_pid=$!
    trap 'kill "$slackbot_pid" "$api_pid" 2>/dev/null || true; wait 2>/dev/null || true' EXIT INT TERM
    sleep 2
    for pid_var in slackbot_pid api_pid; do
      pid=${!pid_var}
      if ! kill -0 "$pid" 2>/dev/null; then
        echo "port-forward ${pid_var} died on startup — check logs:" >&2
        cat "$slackbot_log" "$api_log" >&2
        exit 1
      fi
    done
    echo "slackbot -> localhost:3001 (pid $slackbot_pid, log: $slackbot_log)  -- routes /api/webhooks/slack"
    echo "api      -> localhost:8000 (pid $api_pid, log: $api_log)            -- routes everything else"
    echo "tunnel   -> https://centaur.local-labs.xyz (cloudflared user agent — 'just cloudflared::status' to verify)"
    echo "Tailing Slackbot logs. Ctrl-C stops both port-forwards; tunnel keeps running."
    echo ""
    kubectl logs -n $CENTAUR_NAMESPACE deploy/${CENTAUR_RELEASE}-centaur-slackbot --tail=20 -f
