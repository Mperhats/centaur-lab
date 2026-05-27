#!/usr/bin/env bash
# Overlay wrapper: inject Slack thread_key + delivery into workflow run JSON.
# Usage: call-workflow-run.sh '{"workflow_name":"bfts_research",...}'
set -euo pipefail

body="${1:?workflow run JSON body required}"
tk="${CENTAUR_THREAD_KEY:-}"
if [ -z "$tk" ]; then
  exec /usr/local/bin/call workflow run "$body"
fi

merged="$(python3 - "$body" "$tk" <<'PY'
import json, sys
body = json.loads(sys.argv[1])
tk = sys.argv[2].strip()
inp = body.setdefault("input", {}) if isinstance(body.get("input"), dict) else {}
if not isinstance(body.get("input"), dict):
    body["input"] = inp
if tk and not str(inp.get("thread_key") or "").strip():
    inp["thread_key"] = tk
parts = tk.split(":")
if parts and parts[0] == "slack":
    delivery = inp.get("delivery") if isinstance(inp.get("delivery"), dict) else {}
    if str(delivery.get("platform") or "").lower() != "slack":
        delivery = {}
    if len(parts) == 3:
        derived = {"platform": "slack", "channel": parts[1], "thread_ts": parts[2]}
    elif len(parts) >= 4:
        derived = {
            "platform": "slack",
            "recipient_team_id": parts[1],
            "channel": parts[2],
            "thread_ts": parts[3],
        }
    else:
        derived = {}
    if derived:
        inp["delivery"] = {**derived, **delivery}
print(json.dumps(body, separators=(",", ":")))
PY
)"
exec /usr/local/bin/call workflow run "$merged"
