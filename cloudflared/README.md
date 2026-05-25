# Cloudflare Tunnel

Stable public URL for the local Slackbot service so Slack can deliver Event
Subscriptions to your laptop.

- **Public URL:** `https://centaur.local-labs.xyz`
- **Slack webhook path:** `https://centaur.local-labs.xyz/api/webhooks/slack`
- **Local target:** `http://localhost:3001` (Slackbot Service)

## Day-to-day

Two terminals:

```bash
just port-forward   # kubectl port-forward Slackbot -> localhost:3001
just tunnel         # cloudflared serves public URL -> localhost:3001
```

Both block. Leave them running while you're developing.

## One-time setup on a fresh machine

The repo holds the tunnel's *routing* (`config.yml`). The tunnel's *identity*
(UUID + credentials JSON) is per-Cloudflare-account and per-machine. You need
both.

1. Install cloudflared: `brew install cloudflared`.
2. Authenticate to the Cloudflare zone that owns `local-labs.xyz`:

   ```bash
   cloudflared tunnel login
   ```

   Pick the `local-labs.xyz` zone in the browser. Writes
   `~/.cloudflared/cert.pem`.

3. Either **create a new tunnel** (if this is the first machine) or **reuse
   the existing one**:

   - First machine ever:

     ```bash
     cloudflared tunnel create centaur-dev
     cloudflared tunnel route dns centaur-dev centaur.local-labs.xyz
     ```

     This writes `~/.cloudflared/<UUID>.json` (the credentials) and
     auto-creates the DNS record.

   - Additional machine, reusing the same tunnel: copy the existing
     `~/.cloudflared/<UUID>.json` from the original machine into the same
     path on the new one. (Both machines should not run the tunnel
     simultaneously unless you want Cloudflare to load-balance between them.)

4. Confirm the config is valid:

   ```bash
   cloudflared tunnel --config cloudflared/config.yml ingress validate
   ```

5. You're done. `just tunnel` works.

## How it routes

```
Slack
  └─> https://centaur.local-labs.xyz/api/webhooks/slack
        └─> Cloudflare edge
              └─> cloudflared (your laptop, outbound connection)
                    └─> localhost:3001
                          └─> kubectl port-forward
                                └─> centaur-centaur-slackbot Service (port 3001)
                                      └─> Slackbot pod
```

## Tearing it down

To stop tunneling without deleting anything:

```bash
# Ctrl-C the `just tunnel` and `just port-forward` terminals.
```

To delete the tunnel entirely (e.g. rotating it):

```bash
cloudflared tunnel delete centaur-dev
```

This removes both the tunnel and its DNS record. You'll need to redo the
one-time setup if you want to use it again.
