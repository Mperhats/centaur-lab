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

# Phase 0 platform smoke: confirms the agent-sandbox controller + api SA RBAC
# can create a Sandbox CRD with the BFTS shape (labels, inline volumeClaim,
# workspace mount path) and that pods/exec works. No overlay workflow
# involved; this is a pure-kubectl check against the bundled controller.
# See docs/superpowers/plans/2026-05-25-bfts-on-centaur.md (Phase 0 Task 0.2).
[group('bfts')]
bfts-platform-smoke:
    #!/usr/bin/env bash
    set -euo pipefail
    ns=$CENTAUR_NAMESPACE
    sandbox_id="bfts-platform-smoke-$(date +%s)"
    cleanup() {
      kubectl -n "$ns" delete sandbox.agents.x-k8s.io "$sandbox_id" \
        --ignore-not-found --cascade=foreground --wait=true || true
    }
    trap cleanup EXIT
    cat <<YAML | kubectl -n "$ns" apply -f -
    apiVersion: agents.x-k8s.io/v1alpha1
    kind: Sandbox
    metadata:
      name: ${sandbox_id}
      labels:
        centaur.ai/bfts-sandbox: "true"
    spec:
      replicas: 1
      service: false
      shutdownPolicy: Retain
      volumeClaimTemplates:
        - metadata:
            name: workspace
          spec:
            accessModes: ["ReadWriteOnce"]
            resources:
              requests:
                storage: 1Gi
      podTemplate:
        metadata:
          labels:
            centaur.ai/bfts-sandbox: "true"
        spec:
          containers:
            - name: sandbox
              image: busybox:1.36
              command: ["sleep", "infinity"]
              workingDir: /workspace
              volumeMounts:
                - name: workspace
                  mountPath: /workspace
    YAML
    kubectl -n "$ns" wait --for=condition=Ready pod/"$sandbox_id" --timeout=120s
    out=$(kubectl -n "$ns" exec "$sandbox_id" -- sh -c \
      'mkdir -p /workspace/smoke && printf "%s" "PLATFORM_OK" > /workspace/smoke/marker && cat /workspace/smoke/marker')
    if [ "$out" = "PLATFORM_OK" ]; then
      echo "PLATFORM SMOKE OK (sandbox ${sandbox_id})"
      exit 0
    fi
    echo "unexpected exec output: '${out}'" >&2
    exit 1

# Build the bfts-executor:latest image used by Sandbox pods the BFTS
# tool spawns. Docker Desktop's k8s shares the host image cache so
# pullPolicy: IfNotPresent finds the local tag without a registry.
# See docs/superpowers/plans/2026-05-25-bfts-on-centaur.md (Phase 1).
[group('bfts')]
bfts-build-executor:
    docker build -f overlay/Dockerfile.bfts-executor -t bfts-executor:latest overlay

# Phase 1 end-to-end: prove that BFTS sandbox PVC retention works
# across pause/resume. Drives BFTSExecutor (already deployed in the
# overlay image) from inside the api pod via `kubectl exec`. See
# docs/superpowers/plans/2026-05-25-bfts-on-centaur.md (Phase 1 Task 1.8).
[group('bfts')]
bfts-retention-smoke:
    #!/usr/bin/env bash
    set -euo pipefail
    api_deploy="deploy/${CENTAUR_RELEASE}-centaur-api"
    sandbox_id="bfts-retention-smoke-$(date +%s)"
    py="$(cat <<'PY'
    import asyncio, os, sys
    sys.path.insert(0, "/app/overlay/org/tools")
    from bfts_executor.client import BFTSExecutor, _KubernetesSandboxAPI

    async def main(sandbox_id: str) -> None:
        api = _KubernetesSandboxAPI()
        executor = BFTSExecutor(sandbox_api=api)
        try:
            await executor.create_sandbox(
                sandbox_id, run_id="retention-smoke"
            )
            await api.write_file(
                sandbox_id, "/workspace/sentinel.txt", "RETENTION_OK"
            )
            await executor.pause_sandbox(sandbox_id)
            await executor.resume_sandbox(sandbox_id)
            res = await api.run_command(
                sandbox_id, "cat /workspace/sentinel.txt", timeout_s=10.0
            )
            if res.stdout.strip() != "RETENTION_OK":
                raise SystemExit(
                    f"sentinel mismatch: '{res.stdout!r}' exit={res.exit_code}"
                )
            print("RETENTION SMOKE OK")
        finally:
            await executor.stop_sandbox(sandbox_id)

    asyncio.run(main(os.environ["SANDBOX_ID"]))
    PY
    )"
    kubectl -n $CENTAUR_NAMESPACE exec "$api_deploy" -c api \
        -- env SANDBOX_ID="$sandbox_id" /app/.venv/bin/python -c "$py"

# Phase 2 smoke: kick off a tiny BFTS run (1 tree, 1 worker, 2 iters)
# and stream status. Smallest possible loop that exercises
# bfts_root → bfts_tree → bfts_expand_one → metric scoring →
# bfts_nodes row updates end-to-end. For parallelism, see
# `just bfts-parallel-smoke`. See docs/superpowers/plans/
# 2026-05-25-bfts-on-centaur.md (Phase 2).
[group('bfts')]
bfts-toy-run:
    #!/usr/bin/env bash
    set -euo pipefail
    api_deploy="deploy/${CENTAUR_RELEASE}-centaur-api"
    exec_curl() {
      kubectl exec -n $CENTAUR_NAMESPACE "$api_deploy" -- sh -c \
        'curl -sS "$@" -H "X-Api-Key: $SLACKBOT_API_KEY"' -- "$@"
    }
    run=$(exec_curl -X POST http://localhost:8000/workflows/runs \
        -H "Content-Type: application/json" \
        -d '{
              "workflow_name":"bfts_root",
              "input":{
                "idea":{
                  "Name":"toy-linreg",
                  "Title":"Linear regression baseline on 200 synthetic samples",
                  "Short Hypothesis":"A least-squares fit on a 1-feature dataset should achieve MSE below the variance of y.",
                  "Experiments":["sklearn.linear_model.LinearRegression on a single synthetic dataset of 200 samples."]
                },
                "num_drafts":1,
                "num_workers":1,
                "max_iters":2,
                "debug_prob":0.5
              }
            }')
    run_id=$(printf '%s' "$run" | jq -r '.run_id')
    echo "started bfts_root run ${run_id}"
    for _ in $(seq 1 240); do
      state=$(exec_curl "http://localhost:8000/workflows/runs/${run_id}")
      status=$(printf '%s' "$state" | jq -r '.status // empty')
      [ "$status" = "completed" ] && { printf '%s\n' "$state" | jq; exit 0; }
      [ "$status" = "failed" ] || [ "$status" = "failed_permanent" ] && { printf '%s\n' "$state" | jq >&2; exit 1; }
      sleep 5
    done
    echo "bfts_root run ${run_id} did not reach terminal in time" >&2
    exec_curl "http://localhost:8000/workflows/runs/${run_id}" | jq >&2
    exit 1

# Phase 3 smoke: run a toy BFTS + assert best_solution.py exists in
# bfts_artifacts. Lightweight end-to-end check that the selector +
# export path works.
[group('bfts')]
bfts-verify-best:
    #!/usr/bin/env bash
    set -euo pipefail
    api_deploy="deploy/${CENTAUR_RELEASE}-centaur-api"
    psql_count() {
      kubectl exec -n $CENTAUR_NAMESPACE $api_deploy -- psql "$DATABASE_URL" -tAc \
        "SELECT count(*) FROM bfts_artifacts WHERE relative_path = 'best_solution.py';" \
        | tr -d '[:space:]'
    }
    before=$(psql_count)
    just bfts-toy-run
    after=$(psql_count)
    delta=$((after - before))
    if [ "$delta" -ge "1" ]; then
      echo "BFTS-VERIFY-BEST OK (+${delta} new artifact(s); total=${after})"
      exit 0
    fi
    echo "no new best_solution.py written (before=${before}, after=${after})" >&2
    exit 1

# Phase 4h smoke: BFTS run with num_workers=3 + assert intra-tree
# fan-out actually happened. Triggers bfts_root → 1 tree, 3 expansions
# per iteration × 3 iterations, then queries bfts_nodes for per-step
# concurrency (rows with the same step value within the tree).
# A passing smoke means at least one iteration had >= 2 sibling nodes,
# i.e. bfts_expand_one ran concurrently.
[group('bfts')]
bfts-parallel-smoke:
    #!/usr/bin/env bash
    set -euo pipefail
    api_deploy="deploy/${CENTAUR_RELEASE}-centaur-api"
    exec_curl() {
      kubectl exec -n $CENTAUR_NAMESPACE "$api_deploy" -- sh -c \
        'curl -sS "$@" -H "X-Api-Key: $SLACKBOT_API_KEY"' -- "$@"
    }
    run=$(exec_curl -X POST http://localhost:8000/workflows/runs \
        -H "Content-Type: application/json" \
        -d '{
              "workflow_name":"bfts_root",
              "input":{
                "idea":{
                  "Name":"toy-linreg-parallel",
                  "Title":"Linear regression baseline on 200 synthetic samples (parallel smoke)",
                  "Short Hypothesis":"A least-squares fit on a 1-feature dataset should achieve MSE below the variance of y.",
                  "Experiments":["sklearn.linear_model.LinearRegression on a single synthetic dataset of 200 samples."]
                },
                "num_drafts":1,
                "num_workers":3,
                "max_iters":3,
                "debug_prob":0.5
              }
            }')
    run_id=$(printf '%s' "$run" | jq -r '.run_id')
    echo "started bfts_root run ${run_id} (num_workers=3, max_iters=3)"
    for _ in $(seq 1 360); do
      state=$(exec_curl "http://localhost:8000/workflows/runs/${run_id}")
      status=$(printf '%s' "$state" | jq -r '.status // empty')
      case "$status" in
        completed) break ;;
        failed|failed_permanent|cancelled)
          printf '%s\n' "$state" | jq >&2
          exit 1
          ;;
      esac
      sleep 5
    done
    if [ "$status" != "completed" ]; then
      echo "bfts_root run ${run_id} did not reach terminal in time" >&2
      exit 1
    fi
    # The bfts_tree child run_id has the form `<root_run_id>:tree:0`.
    child_run_id="${run_id}:tree:0"
    # Per-step concurrency: any (step, run_id) bucket with >= 2 sibling
    # nodes means bfts_expand_one ran in parallel for that iteration.
    max_siblings=$(kubectl exec -n $CENTAUR_NAMESPACE $api_deploy -- psql "$DATABASE_URL" -tAc \
      "SELECT COALESCE(MAX(cnt), 0) FROM (SELECT step, COUNT(*) AS cnt FROM bfts_nodes WHERE run_id = '${child_run_id}' GROUP BY step) s;" \
      | tr -d '[:space:]')
    total_nodes=$(kubectl exec -n $CENTAUR_NAMESPACE $api_deploy -- psql "$DATABASE_URL" -tAc \
      "SELECT COUNT(*) FROM bfts_nodes WHERE run_id = '${child_run_id}';" \
      | tr -d '[:space:]')
    echo "tree ${child_run_id}: ${total_nodes} total nodes, max sibling count per step = ${max_siblings}"
    if [ "$max_siblings" -ge "2" ]; then
      echo "BFTS-PARALLEL-SMOKE OK (max concurrent expansions = ${max_siblings})"
      exit 0
    fi
    echo "no iteration produced >= 2 sibling nodes (max=${max_siblings}); fan-out may not be working" >&2
    exit 1

# Phase 4c: manually trigger the bfts_reflection_nightly workflow
# (normally scheduled at 03:00 UTC by SCHEDULE; cron-eligible only when
# BFTS_REFLECTION_ENABLED=1 in api.extraEnv). Use to validate the
# reflection heuristic + hyperparams insert without waiting for the
# cron tick. Idempotent: each invocation inserts at most one new
# bfts_hyperparams row (or zero if there's nothing to reflect on).
[group('bfts')]
bfts-trigger-reflection:
    #!/usr/bin/env bash
    set -euo pipefail
    api_deploy="deploy/${CENTAUR_RELEASE}-centaur-api"
    exec_curl() {
      kubectl exec -n $CENTAUR_NAMESPACE "$api_deploy" -- sh -c \
        'curl -sS "$@" -H "X-Api-Key: $SLACKBOT_API_KEY"' -- "$@"
    }
    before=$(kubectl exec -n $CENTAUR_NAMESPACE $api_deploy -- psql "$DATABASE_URL" -tAc \
      "SELECT count(*) FROM bfts_hyperparams;" | tr -d '[:space:]')
    run=$(exec_curl -X POST http://localhost:8000/workflows/runs \
        -H "Content-Type: application/json" \
        -d '{
              "workflow_name":"bfts_reflection_nightly",
              "input":{"lookback_runs":50},
              "eager_start":true
            }')
    run_id=$(printf '%s' "$run" | jq -r '.run_id')
    echo "started bfts_reflection_nightly run ${run_id}"
    for _ in $(seq 1 60); do
      state=$(exec_curl "http://localhost:8000/workflows/runs/${run_id}")
      status=$(printf '%s' "$state" | jq -r '.status // empty')
      case "$status" in
        completed)
          after=$(kubectl exec -n $CENTAUR_NAMESPACE $api_deploy -- psql "$DATABASE_URL" -tAc \
            "SELECT count(*) FROM bfts_hyperparams;" | tr -d '[:space:]')
          delta=$((after - before))
          printf '%s\n' "$state" | jq
          echo "bfts_hyperparams rows: before=${before} after=${after} (+${delta})"
          exit 0
          ;;
        failed|failed_permanent|cancelled)
          printf '%s\n' "$state" | jq >&2
          exit 1
          ;;
      esac
      sleep 2
    done
    echo "bfts_reflection_nightly run ${run_id} did not reach terminal in time" >&2
    exec_curl "http://localhost:8000/workflows/runs/${run_id}" | jq >&2
    exit 1
