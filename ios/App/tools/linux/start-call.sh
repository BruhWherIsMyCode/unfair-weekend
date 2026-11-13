#!/usr/bin/env bash
# Rings the parent app: starts a check-in call on the backend that start-backend.sh is running.
# With a call id, prints that call's state and log instead (handy to check frames and audio arrived).
#
# Usage: ./start-call.sh                 ring the phone
#        ./start-call.sh <call_id>       show one call
# Environment: PORT (default 8000), PERSON_ID (default p_demo), DEMO_KEY (else the key start-backend.sh made).
set -euo pipefail

PORT="${PORT:-8000}"
PERSON_ID="${PERSON_ID:-p_demo}"
BASE="http://127.0.0.1:$PORT"

KEY_FILE="${XDG_DATA_HOME:-$HOME/.local/share}/checkin-backend/demo_key"
if [ -z "${DEMO_KEY:-}" ]; then
    [ -f "$KEY_FILE" ] || { echo "No demo key: run start-backend.sh first, or set DEMO_KEY." >&2; exit 1; }
    DEMO_KEY="$(cat "$KEY_FILE")"
fi

pretty() {
    if command -v python3 >/dev/null; then python3 -m json.tool; else cat; echo; fi
}

if [ $# -ge 1 ]; then
    curl -fsS -H "X-Demo-Key: $DEMO_KEY" "$BASE/v1/calls/$1" | pretty
    exit 0
fi

if ! response="$(curl -fsS -X POST -H "X-Demo-Key: $DEMO_KEY" -H 'Content-Type: application/json' \
        -d "{\"person_id\":\"$PERSON_ID\"}" "$BASE/v1/calls")"; then
    echo "The backend did not accept the request. Is start-backend.sh running, and has the phone" >&2
    echo "registered (open the app once)?" >&2
    exit 1
fi
call_id="$(printf '%s' "$response" | sed -n 's/.*"call_id" *: *"\([^"]*\)".*/\1/p')"
echo "Ringing. Call id: $call_id"
echo "Afterwards, see its log with:  $0 $call_id"
