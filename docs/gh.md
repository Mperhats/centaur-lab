# GitHub Token Setup

You need ONE fine-grained personal access token. It is reused by three things:
- `github_issue_triage` workflow (posts triage comments on issues)
- The repo-cache DaemonSet (clones `Mperhats/centaur-lab` into a hostPath mount)
- Sandbox agents (push branches and run `gh pr create` against `Mperhats/centaur-lab`)

## Steps

1. **Open the fine-grained PAT page:**
   <https://github.com/settings/personal-access-tokens/new>

2. **Token name:** `centaur-lab-sandbox` (or anything memorable)

3. **Resource owner:** `Mperhats` (your personal account, where centaur-lab lives)

4. **Expiration:** 90 days is fine. Calendar a reminder to rotate.

5. **Repository access:** select **Only select repositories** → choose `Mperhats/centaur-lab`.

6. **Repository permissions** — set exactly these four:

   | Permission | Access |
   |---|---|
   | Contents | **Read and write** |
   | Pull requests | **Read and write** |
   | Issues | **Read and write** |
   | Metadata | Read-only (auto-selected) |

   Leave everything else as "No access". Account permissions can all stay "No access".

7. **Click "Generate token"** at the bottom.

8. **Copy the token** (starts with `github_pat_…`). You will not see it again.

9. **Paste into your `.env`** (replacing the existing placeholder):

   ```bash
   export GITHUB_TOKEN=github_pat_PASTE_HERE
   ```

10. **Push the token into the cluster:**

    ```bash
    just bootstrap-secrets
    ```

    This patches `GITHUB_TOKEN` into the `centaur-infra-env` Kubernetes Secret.

11. **If `just up` has already been run, restart the consumers** so they pick up the new token value:

    ```bash
    kubectl -n centaur-system rollout restart deploy/centaur-centaur-api
    kubectl -n centaur-system rollout restart daemonset/centaur-centaur-repo-cache 2>/dev/null || true
    ```

    (The DaemonSet only exists after Task 2 of the slack-to-pr-loop plan enables `repoCache`. Until then, only the API restart matters.)

## Verify

Confirm the Secret has the new value (without printing it):

```bash
kubectl -n centaur-system get secret centaur-infra-env \
  -o jsonpath='{.data.GITHUB_TOKEN}' | base64 -d | head -c 20 && echo "..."
```

Expected: `github_pat_...` (first 20 chars), confirming it's the new format.

Confirm `gh` can authenticate with the token from inside a sandbox (only works after the cluster is up and a sandbox exists):

```bash
kubectl -n centaur-system exec deploy/centaur-centaur-api -- \
  curl -s http://firewall:8081/secrets/GITHUB_TOKEN | jq -r '.value' | head -c 20
```

Expected: same first 20 chars as above.

## When you need to rotate the token

Regenerate at the same URL, then:

```bash
# 1. Update .env with the new value
# 2. Re-run bootstrap-secrets
just bootstrap-secrets

# 3. Restart consumers so they re-read the Secret
kubectl -n centaur-system rollout restart deploy/centaur-centaur-api
kubectl -n centaur-system rollout restart daemonset/centaur-centaur-repo-cache 2>/dev/null || true

# 4. Revoke the old token at github.com/settings/personal-access-tokens
```
