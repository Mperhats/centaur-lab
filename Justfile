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

[group('lifecycle')]
[working-directory('.centaur')]
deploy:
    #!/usr/bin/env bash
    set -euo pipefail
    if [[ -f ../overlay/.tag ]]; then
      overlay_tag=$(tr -d '[:space:]' < ../overlay/.tag)
    else
      overlay_tag="sha-$(git -C .. rev-parse --short HEAD)"
    fi
    helm dependency update contrib/chart >/dev/null
    helm upgrade --install $CENTAUR_RELEASE contrib/chart \
        --namespace $CENTAUR_NAMESPACE --create-namespace \
        -f contrib/chart/values.dev.yaml \
        -f ../values.org.yaml \
        -f ../values.local.yaml \
        --set "overlay.image.tag=${overlay_tag}"

# Run upstream bootstrap, then patch in keys it does not handle.
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
