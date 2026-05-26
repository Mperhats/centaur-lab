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
    patch_key LOCAL_DEV_API_KEY

[group('lifecycle')]
[confirm("Uninstall " + CENTAUR_RELEASE + " from " + CENTAUR_NAMESPACE + "? Pass --yes to skip this prompt. ")]
down:
    helm uninstall $CENTAUR_RELEASE --namespace $CENTAUR_NAMESPACE

# Mirrors upstream's `cleanup-orphan-proxy-services` `dry-run | delete`
# interface and delegates the orphan-Service half to that recipe so we do
# not reimplement it. Sandbox pods owned by a Sandbox CR with replicas > 0
# are skipped — the agent-sandbox controller would recreate them — and
# per-sandbox proxy pods whose sandbox-id maps to a skipped sandbox are
# kept. The cluster-wide api iron-proxy (sandbox-id="api") is never
# touched.
# Reap stale sandbox + per-sandbox iron-proxy Pods + orphan proxy Services. Pass `delete` to apply.
[group('lifecycle')]
clean mode="dry-run":
    #!/usr/bin/env bash
    set -euo pipefail
    case "{{mode}}" in
      dry-run|delete) ;;
      *) echo "mode must be dry-run or delete" >&2; exit 2 ;;
    esac

    ns=$CENTAUR_NAMESPACE
    keep_ids=()
    drop_sandboxes=()
    drop_proxies=()

    while IFS=$'\t' read -r pod sid owner_kind owner_name; do
      [[ -n "$pod" ]] || continue
      if [[ "$owner_kind" == "Sandbox" && -n "$owner_name" ]]; then
        replicas=$(kubectl get sandbox -n "$ns" "$owner_name" -o jsonpath='{.spec.replicas}' 2>/dev/null || echo "")
        if [[ -n "$replicas" && "$replicas" != "0" ]]; then
          keep_ids+=("$sid")
          continue
        fi
      fi
      drop_sandboxes+=("$pod")
    done < <(
      kubectl get pods -n "$ns" -l centaur.ai/managed=true \
        -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.metadata.labels.centaur\.ai/sandbox-id}{"\t"}{.metadata.ownerReferences[?(@.kind=="Sandbox")].kind}{"\t"}{.metadata.ownerReferences[?(@.kind=="Sandbox")].name}{"\n"}{end}'
    )

    while IFS=$'\t' read -r pod sid; do
      [[ -n "$pod" && "$sid" != "api" ]] || continue
      for kid in "${keep_ids[@]+"${keep_ids[@]}"}"; do
        [[ "$kid" == "$sid" ]] && continue 2
      done
      drop_proxies+=("$pod")
    done < <(
      kubectl get pods -n "$ns" -l centaur.ai/iron-proxy=true \
        -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.metadata.labels.centaur\.ai/sandbox-id}{"\n"}{end}'
    )

    total=$(( ${#drop_sandboxes[@]} + ${#drop_proxies[@]} ))
    if [[ "$total" -eq 0 ]]; then
      echo "No stale sandbox or proxy pods found."
    else
      for pod in "${drop_sandboxes[@]+"${drop_sandboxes[@]}"}"; do
        if [[ "{{mode}}" == "delete" ]]; then
          kubectl delete pod -n "$ns" "$pod" --wait=false
        else
          printf 'sandbox pod: %s\n' "$pod"
        fi
      done
      for pod in "${drop_proxies[@]+"${drop_proxies[@]}"}"; do
        if [[ "{{mode}}" == "delete" ]]; then
          kubectl delete pod -n "$ns" "$pod" --wait=false
        else
          printf 'proxy pod:   %s\n' "$pod"
        fi
      done
    fi

    echo "---"
    (cd {{centaur}} && just cleanup-orphan-proxy-services "{{mode}}")

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

# Background `kubectl port-forward` for Slackbot (:3001) and API (:8000), detached
# from this terminal so it survives shell exit. Idempotent: re-running while alive
# is a no-op. Pair with `just dev-stop` and `just status`. Use `just logs <component>`
# (delegated to upstream `.centaur/Justfile`) for live cluster logs.
[group('dev')]
dev:
    #!/usr/bin/env bash
    set -uo pipefail
    started=0
    for svc in slackbot:3001 api:8000; do
      name=${svc%:*}; port=${svc#*:}
      pidfile=/tmp/centaur-pf-$name.pid
      logfile=/tmp/centaur-port-forward-$name.log
      pid=$(cat "$pidfile" 2>/dev/null || true)
      if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
        echo "  $name :$port  already running (pid $pid)"
        continue
      fi
      pkill -f "kubectl port-forward.*centaur-$name" 2>/dev/null || true
      nohup kubectl port-forward -n $CENTAUR_NAMESPACE svc/${CENTAUR_RELEASE}-centaur-$name $port:$port \
        >"$logfile" 2>&1 </dev/null &
      new_pid=$!
      disown "$new_pid" 2>/dev/null || true
      echo "$new_pid" > "$pidfile"
      echo "  $name :$port  started (pid $new_pid, log: $logfile)"
      started=1
    done
    [ "$started" = "1" ] && sleep 2 || true
    fail=0
    for port in 3001 8000; do
      if ! lsof -nP -iTCP:$port -sTCP:LISTEN -t >/dev/null 2>&1; then
        echo "WARN: port $port not bound — check /tmp/centaur-port-forward-*.log" >&2
        fail=1
      fi
    done
    [ "$fail" = "0" ] && echo "ready. 'just status' to confirm; 'just dev-stop' to tear down."

# Stop the backgrounded port-forwards started by `just dev`. Idempotent.
# Cleans up both the pid-file-tracked processes and any stale matches.
[group('dev')]
dev-stop:
    #!/usr/bin/env bash
    set -uo pipefail
    for name in slackbot api; do
      pidfile=/tmp/centaur-pf-$name.pid
      pid=$(cat "$pidfile" 2>/dev/null || true)
      if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
        kill "$pid" 2>/dev/null || true
        echo "  $name  stopped (pid $pid)"
      fi
      pkill -f "kubectl port-forward.*centaur-$name" 2>/dev/null || true
      rm -f "$pidfile"
    done
    echo "done."

# Full local-stack health check. Delegates the k8s slice to upstream's
# `just status` and the tunnel slice to `just cloudflared::status`, then
# layers in the port-forward and API-health pieces we own.
[group('dev')]
status:
    #!/usr/bin/env bash
    set -uo pipefail
    echo "=== helm release ==="
    helm list -n $CENTAUR_NAMESPACE 2>/dev/null | awk 'NR==1 || /centaur/'
    echo ""
    echo "=== k8s resources (upstream just status) ==="
    (cd {{centaur}} && just status) 2>&1 | head -40
    echo ""
    echo "=== cloudflared tunnel ==="
    just cloudflared::status 2>&1 || true
    echo ""
    echo "=== port-forwards ==="
    for svc in slackbot:3001 api:8000; do
      name=${svc%:*}; port=${svc#*:}
      pidfile=/tmp/centaur-pf-$name.pid
      pid=$(cat "$pidfile" 2>/dev/null || true)
      bound=$(lsof -nP -iTCP:$port -sTCP:LISTEN -t 2>/dev/null | head -1 || true)
      if [ -n "$bound" ] && [ "$bound" = "$pid" ]; then
        echo "  $name :$port  OK (pid $pid)"
      elif [ -n "$bound" ]; then
        echo "  $name :$port  WARN bound by pid $bound (not tracked — pid file says '${pid:-none}')"
      else
        echo "  $name :$port  DOWN — run 'just dev'"
      fi
    done
    echo ""
    echo "=== API health (in-cluster) ==="
    kubectl exec -n $CENTAUR_NAMESPACE deploy/${CENTAUR_RELEASE}-centaur-api -c api -- \
      curl -fsS http://localhost:8000/health 2>&1 || echo "health probe failed"

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
    sys.path.insert(0, "/app/overlay/org/tools/bfts_executor")
    from client import BFTSExecutor, _KubernetesSandboxAPI

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
        -- env SANDBOX_ID="$sandbox_id" python -c "$py"
