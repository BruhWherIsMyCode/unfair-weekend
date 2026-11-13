# Run the backend on Ubuntu

For testing the parent iPhone app (`ios/ParentApp`) when the backend cannot run on a Mac.
The call service the app needs is on the `agent-4/call-service` branch (PR #9). The script
fetches that branch into a folder next to this repo (`<repo>-call-service`) and runs the backend
from there. Your own checkout and branch stay as they are.

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

Options: `--port 8000`, `--branch agent-4/call-service`, `--runner auto|analysis|fake|scripted`.
The demo key is made once and kept in `~/.local/share/checkin-backend/demo_key`, outside the repo.
To set your own key, set `DEMO_KEY` before you start. Without `ELEVENLABS_API_KEY` the avatar's
lines are silent: you see captions only, and answers are not transcribed.

## If the phone cannot connect

- If `ufw` is active, run `sudo ufw allow 8000/tcp`. The script does this itself when sudo
  doesn't need a password.
- Event Wi-Fi often keeps devices from seeing each other. Join this machine to the iPhone's
  Personal Hotspot instead and use the `172.20.10.x` address the script prints.
- Test from this machine: `curl -H "X-Demo-Key: <key>" http://<ip>:8000/v1/health` should print
  `{"ok":true}`.
- `start-call.sh` saying the backend did not accept the request usually means no phone is
  registered yet: open the app with the right address and key first.
