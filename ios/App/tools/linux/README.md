# Run the backend on Ubuntu

For testing the parent iPhone app (`ios/ParentApp`) when the backend cannot run on a Mac.
The script runs the backend from `main` (call service, voice and avatar clips), whatever branch
you have checked out: it fetches `main` into a folder next to this repo (`<repo>-call-service`)
and runs it from there, up to date on every start. Your own checkout and branch stay as they are.

## Needs

- `git` and `curl` (`sudo apt install git curl`), with access to the GitHub repo
- Internet on the first run: it installs `uv` (after asking) and Python 3.12
- This machine and the iPhone on the same Wi-Fi

## Start

1. Run `ios/App/tools/linux/start-backend.sh`.
2. It prints the backend address (`http://<ip>:8000`) and the demo key.
3. On the iPhone, open the app and long-press the idle screen for 2 s. Enter the address, the key
   and `p_demo`, then Save. The "Connecting..." line goes away once the phone reaches the backend.
4. In another terminal, run `ios/App/tools/linux/start-call.sh`. The phone rings.
5. To see one call's state and log afterwards, run:
   `ios/App/tools/linux/start-call.sh <call_id>`

Options: `--port 8000`, `--branch main` (another branch to run, e.g. one under review),
`--runner auto|analysis|fake|scripted`, `--reset-keys`.
The demo key is made once and kept in `~/.local/share/checkin-backend/demo_key`, outside the repo.
To set your own key, set `DEMO_KEY` before you start.

To keep live animation for fixed lines and Claude's generated questions, with prepared avatar videos disabled:

```bash
AVATAR_MODE=live AVATAR_PREGENERATED_CLIPS=off ios/App/tools/linux/start-backend.sh
```

`AVATAR_PREGENERATED_CLIPS=off` skips MP4 preparation and prevents cached MP4s from being offered to the phone.
It keeps voice preparation and live Simli sessions available, and leaves existing cached files on disk.
Unset or `on` preserves prepared-video behavior. The backend must restart to apply the setting.

On the first run the script asks for the ElevenLabs key (voice, transcription) and the Simli key
(avatar clips). Typing is hidden, and Enter skips one. They are saved in
`~/.local/share/checkin-backend/keys.env` (only you can read it, never in the repo) and reused after
that. `--reset-keys` asks again. A key already set in the environment wins. Without the ElevenLabs
key the avatar's lines are silent: you see captions only, and answers are not transcribed.

Before starting the server, the script prepares the voice for every script line (ElevenLabs key)
and the avatar clips (Simli key) with `main`'s `checkin_backend.prepare`. Prepared lines are kept in
the backend's ignored `.cache/` and reused, so only the first run on a machine takes about 2 minutes
and uses provider credits. If preparation fails, the server still starts and lines without a clip
play as voice only.

## If the phone cannot connect

- If `ufw` is active, run `sudo ufw allow 8000/tcp`. The script does this itself when sudo
  doesn't need a password.
- Event Wi-Fi often keeps devices from seeing each other. Join this machine to the iPhone's
  Personal Hotspot instead and use the `172.20.10.x` address the script prints.
- Test from this machine: `curl -H "X-Demo-Key: <key>" http://<ip>:8000/v1/health` should print
  `{"ok":true}`.
- `start-call.sh` saying the backend did not accept the request usually means no phone is
  registered yet: open the app with the right address and key first.
