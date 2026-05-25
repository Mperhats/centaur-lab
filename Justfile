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
[working-directory(centaur)]
deploy:
    #!/usr/bin/env bash
    set -euo pipefail
    helm dependency update contrib/chart >/dev/null
    helm upgrade --install $CENTAUR_RELEASE contrib/chart \
        --namespace $CENTAUR_NAMESPACE --create-namespace \
        -f contrib/chart/values.dev.yaml \
        -f ../values.local.yaml

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

[group('lifecycle')]
[confirm("Uninstall " + CENTAUR_RELEASE + " from " + CENTAUR_NAMESPACE + "? Pass --yes to skip this prompt. ")]
down:
    helm uninstall $CENTAUR_RELEASE --namespace $CENTAUR_NAMESPACE

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

    echo "=== 5/5 verify PR (gh PR search tokenizes oddly on '():' so we use in:title + branch-name fallback) ==="
    # The agent's gh pr create returns when GitHub accepts the API call, but
    # search indexing can lag a few seconds. Retry a few times.
    cutoff=$(date -u -v-10M +'%Y-%m-%dT%H:%M:%SZ' 2>/dev/null || date -u -d '10 minutes ago' +'%Y-%m-%dT%H:%M:%SZ')
    for attempt in 1 2 3 4 5; do
      # Primary: in:title + time filter. Avoids the literal "(overlay):" tokens
      # that confuse GitHub's search and silently return [] (see slack-to-pr-loop
      # plan, Task 5 review notes).
      prs=$(gh pr list -R Mperhats/centaur-lab \
        --search "in:title \"add probe tool\" created:>=${cutoff}" \
        --json number,title,url,headRefName,createdAt --limit 5)
      pr_count=$(printf '%s' "$prs" | jq 'length')
      if [ "$pr_count" -ge 1 ]; then
        echo "✓ found $pr_count matching PR(s) created in the last 10 minutes:"
        printf '%s\n' "$prs" | jq '.'
        exit 0
      fi
      # Fallback: search-index lag is real even with a tight in:title query;
      # cross-check by listing open PRs whose head branch matches the
      # auto-generated `agent-<ts>-…` pattern.
      branch_prs=$(gh pr list -R Mperhats/centaur-lab --state open \
        --json number,title,url,headRefName,createdAt --limit 20 \
        --jq "[.[] | select(.headRefName | startswith(\"agent-\")) | select(.createdAt >= \"${cutoff}\") | select(.title | contains(\"add probe tool\"))]")
      branch_count=$(printf '%s' "$branch_prs" | jq 'length')
      if [ "$branch_count" -ge 1 ]; then
        echo "✓ found $branch_count matching PR(s) (via head-branch fallback):"
        printf '%s\n' "$branch_prs" | jq '.'
        exit 0
      fi
      echo "  attempt $attempt: no PR found yet, sleeping 5s..."
      sleep 5
    done

    echo "✗ execution completed but no matching PR appeared on GitHub within 25s"
    echo "  check the agent's result_text above — it may have failed at the push or gh step:"
    exec_curl "http://localhost:8000/agent/executions/${execution_id}" | jq '.result_text'
    exit 1
