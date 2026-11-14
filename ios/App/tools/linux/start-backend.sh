#!/usr/bin/env bash
# Starts the check-in backend (call service) on this Ubuntu machine, so the parent iPhone app can reach it
# over Wi-Fi.
#
# It runs the backend from main (call service, voice and avatar clips), whatever branch is checked out here:
# main is fetched into a folder next to this repo (<repo>-call-service) and run from there, up to date on
# every start. Your own checkout and branch are not touched. --branch runs another branch instead.
#
# It also installs the measuring packages (mediapipe, opencv) and the face/hand model files into the backend's
# environment on every start (fast when cached), runs a self-check, and starts the backend with Claude talking
# to her. If measuring is not ready, the call falls back to the scripted one (no measuring) and says why.
# The backend is started with the environment's own Python (not "uv run"), so nothing syncs the packages away.
#
# Usage: ./start-backend.sh [--port 8000] [--branch main] [--runner auto|analysis|fake|scripted]
#                           [--claude off|subscription|api] [--mirrored] [--baseline-needed N]
#                           [--label normal|acted] [--debug] [--reset-keys]
#   --claude          who runs the call. subscription (default): your logged-in `claude` command, installs
#                     claude-agent-sdk, no API key used. api: needs ANTHROPIC_API_KEY already set in the
#                     environment (never stored, asked or printed). off: the fixed-order call, no Claude.
#                     If Claude cannot be used, a yellow warning says why and the call runs without Claude.
#   --mirrored        the phone's front camera frames are mirrored (UW_MIRRORED=on).
#   --baseline-needed how many normal calls make her usual ready (UW_BASELINE_NEEDED, default 5): use 1 or 2
#                     to try things out.
#   --label           normal: this run's calls may enter her usual; acted: they never do (DEMO_SESSION_LABEL).
#   --debug           debug view (UW_DEBUG=on): shows which hand the camera sees. One line per second in this terminal,
#                     a short caption on the phone, pictures in analysis/runs/debug/<call>/. Measuring is unchanged.
#                     To check the mirroring: raise your LEFT hand; if the debug says RIGHT, restart with --mirrored.
# Keys: DEMO_KEY is made once. ELEVENLABS_API_KEY (voice, transcription) and SIMLI_API_KEY (avatar clips) are asked
#       for on the first run (Enter skips) and stored in ~/.local/share/checkin-backend/ (mode 600), never in the repo.
#       A key set in the environment wins; --reset-keys asks again.
# Set AVATAR_MODE=live AVATAR_PREGENERATED_CLIPS=off to keep live animation without preparing or offering MP4s.
set -euo pipefail

BRANCH="main"
PORT=8000
RUNNER="auto"
CLAUDE="subscription"
MIRRORED=0
BASELINE_NEEDED=""
LABEL=""
DEBUG_VIEW=0
RESET_KEYS=0
while [ $# -gt 0 ]; do
    case "$1" in
        --port) PORT="$2"; shift 2 ;;
        --branch) BRANCH="$2"; shift 2 ;;
        --runner) RUNNER="$2"; shift 2 ;;
        --claude) CLAUDE="$2"; shift 2 ;;
        --mirrored) MIRRORED=1; shift ;;
        --baseline-needed) BASELINE_NEEDED="$2"; shift 2 ;;
        --label) LABEL="$2"; shift 2 ;;
        --debug) DEBUG_VIEW=1; shift ;;
        --reset-keys) RESET_KEYS=1; shift ;;
        -h|--help) sed -n '2,/^set -euo/{/^set -euo/!p}' "$0"; exit 0 ;;
        *) echo "Unknown option: $1" >&2; exit 2 ;;
    esac
done
case "$CLAUDE" in off|subscription|api) ;; *) echo "--claude must be off, subscription or api" >&2; exit 2 ;; esac
case "$LABEL" in ""|normal|acted) ;; *) echo "--label must be normal or acted" >&2; exit 2 ;; esac
case "$BASELINE_NEEDED" in
    "") ;;
    *[!0-9]*|0|00*) echo "--baseline-needed must be a whole number of 1 or more" >&2; exit 2 ;;
esac

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
VENV_PY="$BACKEND/.venv/bin/python"
[ -x "$VENV_PY" ] || fail "The backend environment has no Python at $VENV_PY."
export PYTHONPATH="$WORK${PYTHONPATH:+:$PYTHONPATH}"   # so `analysis` (next to backend/) can be imported

# ---------------------------------------------------------------- 2b. measuring: packages, models, self-check
step "Measuring (face and hand): packages, model files, self-check"
MEASURING_PROBLEM=""
if [ ! -f "$WORK/analysis/requirements.txt" ]; then
    MEASURING_PROBLEM="$BRANCH has no analysis/requirements.txt (the measuring code is not in that branch)."
elif ! uv pip install --python "$VENV_PY" -r "$WORK/analysis/requirements.txt"; then
    MEASURING_PROBLEM="Installing the measuring packages (mediapipe, opencv) failed."
elif ! (cd "$WORK" && "$VENV_PY" -m analysis.get_models); then
    MEASURING_PROBLEM="Downloading the face/hand model files failed."
elif ! (cd "$WORK" && "$VENV_PY" -m analysis.selfcheck); then
    MEASURING_PROBLEM="The measuring self-check found a problem (see above)."
fi
if [ -n "$MEASURING_PROBLEM" ]; then
    warn "$MEASURING_PROBLEM"
    case "$RUNNER" in
        auto) RUNNER="scripted"; warn "Continuing with the scripted call: it plays the lines but measures nothing." ;;
        analysis) warn "You asked for --runner analysis: the backend will refuse to start and print the problems." ;;
    esac
fi

# ---------------------------------------------------------------- 2c. Claude
step "Claude ($CLAUDE)"
case "$CLAUDE" in
    subscription)
        if ! command -v claude >/dev/null; then
            warn "The 'claude' command was not found, so the subscription cannot be used. Calls run without Claude."
            warn "(Install Claude Code and log in with it, or use --claude api with ANTHROPIC_API_KEY set.)"
            CLAUDE="off"
        elif ! uv pip install --python "$VENV_PY" claude-agent-sdk; then
            warn "Installing claude-agent-sdk failed. Calls run without Claude."
            CLAUDE="off"
        fi
        ;;
    api)
        if [ -z "${ANTHROPIC_API_KEY:-}" ]; then
            warn "ANTHROPIC_API_KEY is not set in the environment, so the API cannot be used. Calls run without Claude."
            CLAUDE="off"
        elif ! uv pip install --python "$VENV_PY" anthropic; then
            warn "Installing the anthropic package failed. Calls run without Claude."
            CLAUDE="off"
        fi
        ;;
    off) echo "Claude is off: the call follows the fixed order." ;;
esac

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
            "$VENV_PY" -m checkin_backend.prepare --video \
            || warn "Preparation stopped (see above). The backend still starts; lines without a clip play as voice."
    else
        ELEVENLABS_API_KEY="$ELEVENLABS_API_KEY" "$VENV_PY" -m checkin_backend.prepare \
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
step "Settings of this run"
if [ "$CLAUDE" = "off" ]; then echo "  Claude:    off (fixed-order call)"; else echo "  Claude:    $CLAUDE"; fi
if [ -n "$MEASURING_PROBLEM" ]; then echo "  Measuring: NOT ready ($RUNNER call)"; else echo "  Measuring: ready"; fi
echo "  Runner:    $RUNNER"
echo "  Avatar:    ${AVATAR_MODE:-live}"
echo "  Prepared avatar clips: ${AVATAR_PREGENERATED_CLIPS:-on}"
if [ "$MIRRORED" = 1 ]; then echo "  Camera:    mirrored"; else echo "  Camera:    not mirrored"; fi
if [ -n "$BASELINE_NEEDED" ]; then
    echo "  Baseline:  needs $BASELINE_NEEDED normal calls"
else
    echo "  Baseline:  needs 5 normal calls (the default)"
fi
echo "  Label:     ${LABEL:-live}"
if [ "$DEBUG_VIEW" = 1 ]; then echo "  Debug:     on (terminal lines, captions, pictures in analysis/runs/debug)"; fi

step "Running the backend on port $PORT (Ctrl+C to stop)"
export DEMO_KEY
export CALL_RUNNER="$RUNNER"
if [ "$CLAUDE" = "off" ]; then
    export CALL_DRIVER="fixed"
    unset UW_CLAUDE_BACKEND
else
    export CALL_DRIVER="claude"
    export UW_CLAUDE_BACKEND="$CLAUDE"
fi
# The API key is only passed on for --claude api, from the environment it was already in (never stored or printed).
if [ "$CLAUDE" = "api" ]; then export ANTHROPIC_API_KEY; else unset ANTHROPIC_API_KEY; fi
if [ "$MIRRORED" = 1 ]; then export UW_MIRRORED="on"; else unset UW_MIRRORED; fi
if [ -n "$BASELINE_NEEDED" ]; then export UW_BASELINE_NEEDED="$BASELINE_NEEDED"; else unset UW_BASELINE_NEEDED; fi
if [ "$DEBUG_VIEW" = 1 ]; then export UW_DEBUG="on"; else unset UW_DEBUG; fi
if [ -n "$LABEL" ]; then export DEMO_SESSION_LABEL="$LABEL"; else unset DEMO_SESSION_LABEL; fi
if [ -n "${ELEVENLABS_API_KEY:-}" ]; then export ELEVENLABS_API_KEY; else unset ELEVENLABS_API_KEY; fi
if [ -n "${SIMLI_API_KEY:-}" ]; then export SIMLI_API_KEY; else unset SIMLI_API_KEY; fi
cd "$BACKEND"
exec "$VENV_PY" -u -m uvicorn checkin_backend.app:create_app --factory --host 0.0.0.0 --port "$PORT"
