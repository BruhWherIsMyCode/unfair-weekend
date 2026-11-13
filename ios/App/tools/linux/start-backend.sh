#!/usr/bin/env bash
# Starts the check-in backend (call service) on this Ubuntu machine, so the parent iPhone app can reach it
# over Wi-Fi.
#
# The call service the parent app needs is on the agent-4/call-service branch (PR #9), not on this branch.
# This fetches that branch into a folder next to this repo (<repo>-call-service) and runs the backend from
# there. Your own checkout and branch are not touched.
#
# Usage: ./start-backend.sh [--port 8000] [--branch agent-4/call-service] [--runner auto|analysis|fake|scripted]
# Environment: DEMO_KEY (else one is made once and kept in ~/.local/share/checkin-backend/demo_key),
#              ELEVENLABS_API_KEY (optional: real voice and transcription).
set -euo pipefail

BRANCH="agent-4/call-service"
PORT=8000
RUNNER="auto"
while [ $# -gt 0 ]; do
    case "$1" in
        --port) PORT="$2"; shift 2 ;;
        --branch) BRANCH="$2"; shift 2 ;;
        --runner) RUNNER="$2"; shift 2 ;;
        -h|--help) sed -n '2,11p' "$0"; exit 0 ;;
        *) echo "Unknown option: $1" >&2; exit 2 ;;
    esac
done

step() { printf '\n\033[36m== %s\033[0m\n' "$1"; }
warn() { printf '\033[33m%s\033[0m\n' "$1"; }
fail() { printf '\n\033[31m%s\033[0m\n' "$1" >&2; exit 1; }

# ---------------------------------------------------------------- 1. the call-service code
step "Getting $BRANCH"
command -v git >/dev/null || fail "git is not installed: sudo apt install git"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(git -C "$SCRIPT_DIR" rev-parse --show-toplevel 2>/dev/null)" \
    || fail "Run this script from inside the unfair-weekend git checkout."
WORK="$(dirname "$REPO")/$(basename "$REPO")-call-service"

git -C "$REPO" fetch origin "$BRANCH" || fail "Could not fetch $BRANCH. Check the network and your GitHub access."
if [ -e "$WORK/.git" ]; then
    git -C "$WORK" checkout --quiet --detach "origin/$BRANCH" \
        || fail "Could not update $WORK. Delete that folder and run this again."
else
    git -C "$REPO" worktree prune
    git -C "$REPO" worktree add --detach "$WORK" "origin/$BRANCH" || fail "Could not create $WORK."
fi
BACKEND="$WORK/backend"
[ -f "$BACKEND/checkin_backend/hub.py" ] || fail "$BRANCH has no call service (backend/checkin_backend/hub.py is missing)."
echo "Backend code: $BACKEND"

# ---------------------------------------------------------------- 2. uv and dependencies
step "Checking uv"
export PATH="$HOME/.local/bin:$PATH"
if ! command -v uv >/dev/null; then
    command -v curl >/dev/null || fail "curl is not installed: sudo apt install curl"
    read -r -p "uv (the Python package manager the backend uses) is not installed. Install it now from astral.sh? [Y/n] " answer
    case "$answer" in
        [Nn]*) fail "Install uv (https://docs.astral.sh/uv/getting-started/installation/) and run this again." ;;
    esac
    curl -LsSf https://astral.sh/uv/install.sh | sh
    command -v uv >/dev/null || fail "uv was installed but is not on PATH. Open a new terminal and run this again."
fi

step "Installing backend dependencies (the first run also downloads Python 3.12)"
cd "$BACKEND"
uv sync --locked --extra dev || fail "uv sync failed. See the messages above."

# ---------------------------------------------------------------- 3. demo key
KEY_FILE="${XDG_DATA_HOME:-$HOME/.local/share}/checkin-backend/demo_key"
if [ -z "${DEMO_KEY:-}" ]; then
    if [ -f "$KEY_FILE" ]; then
        DEMO_KEY="$(cat "$KEY_FILE")"
    else
        DEMO_KEY="$(od -An -N12 -tx1 /dev/urandom | tr -d ' \n')"
        mkdir -p "$(dirname "$KEY_FILE")"
        (umask 077 && printf '%s' "$DEMO_KEY" > "$KEY_FILE")
    fi
fi

# ---------------------------------------------------------------- 4. firewall
step "Firewall"
if command -v ufw >/dev/null && sudo -n true 2>/dev/null && sudo ufw status | grep -q "Status: active"; then
    sudo ufw allow "$PORT/tcp" >/dev/null && echo "ufw: inbound TCP $PORT allowed."
elif command -v ufw >/dev/null; then
    echo "If ufw is active, allow the port once:  sudo ufw allow $PORT/tcp"
    echo "(Check with: sudo ufw status. Ubuntu ships with ufw inactive.)"
else
    echo "ufw is not installed; nothing to open."
fi

# ---------------------------------------------------------------- 5. what to type into the phone
step "Phone settings (long-press the idle screen for 2 s)"
IPS="$(hostname -I 2>/dev/null || true)"
for ip in $IPS; do
    case "$ip" in
        *:*|127.*|169.254.*|172.17.*) ;;   # skip IPv6, loopback, link-local, docker
        *) echo "  Backend address: http://$ip:$PORT" ;;
    esac
done
echo "  Demo key:        $DEMO_KEY"
echo "  Person:          p_demo"
echo
echo "The phone and this machine must be on the same Wi-Fi. If the event Wi-Fi keeps devices apart,"
echo "join this machine to the iPhone's Personal Hotspot and use the 172.20.10.x address above."
echo
echo "Start a call from another terminal:  $SCRIPT_DIR/start-call.sh"
if [ -z "${ELEVENLABS_API_KEY:-}" ]; then
    warn "No ELEVENLABS_API_KEY set: the avatar's lines are silent (captions only) and answers are not transcribed."
fi

# ---------------------------------------------------------------- 6. run
step "Running the backend on port $PORT (Ctrl+C to stop)"
export DEMO_KEY
export CALL_RUNNER="$RUNNER"
exec uv run --locked uvicorn checkin_backend.app:create_app --factory --host 0.0.0.0 --port "$PORT"
