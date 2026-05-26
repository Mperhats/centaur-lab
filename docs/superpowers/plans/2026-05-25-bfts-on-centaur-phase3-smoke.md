# Phase 3 smoke verification log

> **Status: PENDING operator action.**
> Code/doc deliverables (Justfile recipe + this skeleton) are in place
> (Task 3.6 Step 1 + Step 3-skeleton). Step 2 (running the smoke) requires
> a redeploy of the overlay image (new `bfts_vlm` tool + `_bfts_export` +
> VLM-wired `_bfts_expand` + `mark_buggy_plots` wiring + best-artifact
> persistence) and the Phase 2 smoke prerequisites. See runbook below.

## Runbook

```bash
# 1. Build the overlay image with all Phase 2 + Phase 3 modules baked in.
just overlay::build

# 2. Redeploy (helm upgrade) and force the API pod to pull the new image.
just deploy
kubectl rollout restart -n "$CENTAUR_NAMESPACE" deploy/"$CENTAUR_RELEASE"-centaur-api
kubectl rollout status  -n "$CENTAUR_NAMESPACE" deploy/"$CENTAUR_RELEASE"-centaur-api --timeout=180s

# 3. Confirm the new workflows + tools are visible to the loader.
kubectl exec -n "$CENTAUR_NAMESPACE" deploy/"$CENTAUR_RELEASE"-centaur-api -- sh -c \
  "ls /app/overlay/org/workflows | grep -E '^(bfts_root|bfts_tree|_bfts_)' && \
   ls /app/overlay/org/tools     | grep -E '^bfts_(executor|vlm)$'"

# 4. Run the Phase 3 smoke (≤ 20 min poll budget for the inner toy-run).
just bfts-verify-best | tee /tmp/bfts-verify-best.json

# 5. If the toy run never produces a good node within max_iters=2,
#    bump max_iters in the bfts-toy-run recipe temporarily to 4 and rerun.
```

## Fields to fill in after running the smoke

- date:
- run_id (from inner just bfts-toy-run output):
- elapsed (total wall time across both recipes):
- iters_used:
- node_count:
- best_node_id (or "none — toy budget exhausted; rerun with max_iters=4"):
- bfts_artifacts.best_solution.py row count for run_id:
- VLM behavior observed (is_buggy_plots distribution, any "no plots produced" fallbacks):
- any unexpected failures:
