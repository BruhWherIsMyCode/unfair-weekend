#!/usr/bin/env bash
# Starts the check-in backend (call service) on this Ubuntu machine, so the parent iPhone app can reach it
# over Wi-Fi.
#
# It runs the backend from main (call service, voice and avatar clips), whatever branch is checked out here:
# main is fetched into a folder next to this repo (<repo>-call-service) and run from there, up to date on
# every start. Your own checkout and branch are not touched. --branch runs another branch instead.
#
# Usage: ./start-backend.sh [--port 8000] [--branch main] [--runner auto|analysis|fake|scripted]
#                           [--reset-keys]
# Keys: DEMO_KEY is made once. ELEVENLABS_API_KEY (voice, transcription) and SIMLI_API_KEY (avatar clips) are asked
#       for on the first run (Enter skips) and stored in ~/.local/share/checkin-backend/ (mode 600), never in the repo.
#       A key set in the environment wins; --reset-keys asks again.
set -euo pipefail

BRANCH="main"
PORT=8000
RUNNER="auto"
RESET_KEYS=0
while [ $# -gt 0 ]; do
    case "$1" in
        --port) PORT="$2"; shift 2 ;;
        --branch) BRANCH="$2"; shift 2 ;;
        --runner) RUNNER="$2"; shift 2 ;;
        --reset-keys) RESET_KEYS=1; shift ;;
        -h|--help) sed -n '2,13p' "$0"; exit 0 ;;
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

# ---------------------------------------------------------------- 3. keys
KEY_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/checkin-backend"
KEY_FILE="$KEY_DIR/demo_key"
PROVIDER_FILE="$KEY_DIR/keys.env"
mkdir -p "$KEY_DIR" && chmod 700 "$KEY_DIR"
if [ -z "${DEMO_KEY:-}" ]; then
    if [ -f "$KEY_FILE" ]; then
        DEMO_KEY="$(cat "$KEY_FILE")"
    else
        DEMO_KEY="$(od -An -N12 -tx1 /dev/urandom | tr -d ' \n')"
        (umask 077 && printf '%s' "$DEMO_KEY" > "$KEY_FILE")
    fi
fi

step "Provider keys"
STORED_ELEVENLABS=""
STORED_SIMLI=""
if [ "$RESET_KEYS" = 0 ] && [ -f "$PROVIDER_FILE" ]; then
    # Plain KEY=value lines; read, never sourced.
    while IFS='=' read -r name value; do
        case "$name" in
            ELEVENLABS_API_KEY) STORED_ELEVENLABS="$value" ;;
            SIMLI_API_KEY) STORED_SIMLI="$value" ;;
        esac
    done < "$PROVIDER_FILE"
fi

ask_key() {  # $1 label, $2 what it is for. Hidden typing; prints the answer (may be empty).
    local answer=""
    if [ -t 0 ]; then
        read -r -s -p "$1 ($2; Enter to skip): " answer
        echo >&2
    fi
    printf '%s' "$answer" | tr -d '[:space:]'
}

ASKED=0
if [ -z "${ELEVENLABS_API_KEY:-}" ]; then
    if [ -n "$STORED_ELEVENLABS" ]; then
        ELEVENLABS_API_KEY="$STORED_ELEVENLABS"
    elif [ "$RESET_KEYS" = 1 ] || [ ! -f "$PROVIDER_FILE" ]; then
        ELEVENLABS_API_KEY="$(ask_key "ElevenLabs API key" "voice and transcription")"
        ASKED=1
    fi
fi
if [ -z "${SIMLI_API_KEY:-}" ]; then
    if [ -n "$STORED_SIMLI" ]; then
        SIMLI_API_KEY="$STORED_SIMLI"
    elif [ "$RESET_KEYS" = 1 ] || [ ! -f "$PROVIDER_FILE" ]; then
        SIMLI_API_KEY="$(ask_key "Simli API key" "avatar clips")"
        ASKED=1
    fi
fi
if [ "$ASKED" = 1 ]; then
    (umask 077 && printf 'ELEVENLABS_API_KEY=%s\nSIMLI_API_KEY=%s\n' "${ELEVENLABS_API_KEY:-}" "${SIMLI_API_KEY:-}" \
        > "$PROVIDER_FILE")
    echo "Saved to $PROVIDER_FILE (only you can read it). Change them later with --reset-keys."
fi
for name in ELEVENLABS_API_KEY SIMLI_API_KEY; do
    if [ -n "${!name:-}" ]; then echo "  $name: set"; else echo "  $name: not set"; fi
done

# ---------------------------------------------------------------- voice and avatar clips
step "Voice and avatar clips"
if [ -n "${ELEVENLABS_API_KEY:-}" ]; then
    echo "Prepared lines are reused; only new lines are generated (the first run takes about 2 minutes)."
    if [ -n "${SIMLI_API_KEY:-}" ]; then
        ELEVENLABS_API_KEY="$ELEVENLABS_API_KEY" SIMLI_API_KEY="$SIMLI_API_KEY" \
            uv run --locked python -m checkin_backend.prepare --video \
            || warn "Preparation stopped (see above). The backend still starts; lines without a clip play as voice."
    else
        ELEVENLABS_API_KEY="$ELEVENLABS_API_KEY" uv run --locked python -m checkin_backend.prepare \
            || warn "Preparation stopped (see above). The backend still starts."
        echo "No Simli key: no avatar clips, lines play as voice."
    fi
else
    echo "No ElevenLabs key: nothing to prepare (lines are captions only)."
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
if [ -n "${ELEVENLABS_API_KEY:-}" ]; then export ELEVENLABS_API_KEY; else unset ELEVENLABS_API_KEY; fi
if [ -n "${SIMLI_API_KEY:-}" ]; then export SIMLI_API_KEY; else unset SIMLI_API_KEY; fi
exec uv run --locked uvicorn checkin_backend.app:create_app --factory --host 0.0.0.0 --port "$PORT"
