# Phase 2 smoke verification log

> **Status: PENDING operator action.**
> Code/doc deliverables (Justfile recipe + this skeleton) are in place
> (Task 2.10 Step 1, Step 3-skeleton). Steps 2 and 3-fill-in require a
> redeploy of the overlay image (new `bfts_root` / `bfts_tree` /
> `_bfts_*` modules) and a 5–20 minute smoke run. See runbook below.

## Runbook

```bash
# 1. Build the overlay image with all Phase 2 modules baked in.
just overlay::build

# 2. Redeploy (helm upgrade) and force the API pod to pull the new image.
just deploy
kubectl rollout restart -n "$CENTAUR_NAMESPACE" deploy/"$CENTAUR_RELEASE"-centaur-api
kubectl rollout status  -n "$CENTAUR_NAMESPACE" deploy/"$CENTAUR_RELEASE"-centaur-api --timeout=180s

# 3. Confirm the new workflows are visible to the loader.
kubectl exec -n "$CENTAUR_NAMESPACE" deploy/"$CENTAUR_RELEASE"-centaur-api -- \
  ls /app/overlay/org/workflows | grep -E '^(bfts_root|bfts_tree|_bfts_)'

# 4. Confirm the BFTS migration is applied (idempotent re-check).
kubectl exec -n "$CENTAUR_NAMESPACE" "$CENTAUR_RELEASE"-centaur-postgres-0 -c postgres -- \
  psql -U centaur -d centaur -c "\d bfts_runs" >/dev/null

# 5. Run the smoke (≤ 20 min poll budget).
just bfts-toy-run | tee /tmp/bfts-toy-run.json

# 6. After completion, query the per-run state for the fields below.
run_id=$(jq -r '.run_id' /tmp/bfts-toy-run.json)
kubectl exec -n "$CENTAUR_NAMESPACE" "$CENTAUR_RELEASE"-centaur-postgres-0 -c postgres -- \
  psql -U centaur -d centaur -c "
    SELECT run_id, iters_used := (SELECT COUNT(*) FROM bfts_nodes WHERE run_id LIKE '$run_id%'),
           best_node_id, status
    FROM bfts_runs WHERE run_id LIKE '$run_id%';"
```

## Fields to fill in after running the smoke

- date:
- run_id:
- elapsed:
- iters_used:
- node_count:
- best_node_id (or "none — toy budget exhausted"):
- any unexpected failures:
- bfts_nodes row count for run_id:
- bfts_artifacts row count for run_id:
