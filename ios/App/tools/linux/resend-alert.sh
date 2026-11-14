#!/usr/bin/env bash
# Debug: shows the last family alert's notification on the family phone again (sound and banner).
# Nothing new is created: no new alert, no history row in the app, nothing stored on the backend.
# The family app must be open or running in the background, connected to the backend start-backend.sh is running.
#
# Usage: ./resend-alert.sh                 the latest alert (a change from her usual, or a concern)
#        ./resend-alert.sh <call_id>       that call's message (also "could not finish" and "no answer")
# Environment: PORT (default 8000), FAMILY_ID (default f_demo), BASE_URL (default http://127.0.0.1:$PORT),
#              DEMO_KEY (else the key start-backend.sh made).
set -euo pipefail

PORT="${PORT:-8000}"
FAMILY_ID="${FAMILY_ID:-f_demo}"
BASE="${BASE_URL:-http://127.0.0.1:$PORT}"

KEY_FILE="${XDG_DATA_HOME:-$HOME/.local/share}/checkin-backend/demo_key"
if [ -z "${DEMO_KEY:-}" ]; then
    [ -f "$KEY_FILE" ] || { echo "No demo key: run start-backend.sh first, or set DEMO_KEY." >&2; exit 1; }
    DEMO_KEY="$(cat "$KEY_FILE")"
fi

url="$BASE/v1/debug/families/$FAMILY_ID/resend"
[ $# -ge 1 ] && url="$url?call_id=$1"

body="$(mktemp)"
trap 'rm -f "$body"' EXIT
status="$(curl -sS -o "$body" -w '%{http_code}' -X POST -H "X-Demo-Key: $DEMO_KEY" "$url")" || {
    echo "Could not reach the backend at $BASE. Is start-backend.sh running?" >&2
    exit 1
}

case "$status" in
    200)
        if command -v python3 >/dev/null; then
            python3 - "$body" <<'EOF'
import json, sys
m = json.load(open(sys.argv[1]))
print(f"Sent again to {m['family_devices']} family phone(s): {m['kind']} from call {m['call_id']}")
print(f"  {m['text']}")
EOF
        else
            cat "$body"; echo
        fi
        ;;
    404)
        if grep -q '"detail"' "$body"; then   # FastAPI's own "Not Found": the route does not exist
            echo "This backend has no resend endpoint yet: run it from a branch that has it (start-backend.sh --branch)." >&2
        elif [ $# -ge 1 ]; then
            echo "No message to resend for call $1 (as-usual and still-learning calls have no notification)." >&2
        else
            echo "No alert to resend for $FAMILY_ID: no call has sent one yet." >&2
        fi
        exit 1 ;;
    409)
        echo "No family phone has connected since the backend started. Open the family app first." >&2
        exit 1 ;;
    *)
        echo "The backend answered $status:" >&2
        cat "$body" >&2; echo >&2
        if [ "$status" = 401 ]; then echo "Wrong demo key? Set DEMO_KEY to the key the backend runs with." >&2; fi
        exit 1 ;;
esac
