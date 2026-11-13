# Run the backend on a Windows PC

For testing the parent iPhone app (`ios/ParentApp`) when the backend cannot run on a Mac.
The call service the app needs is on the `agent-4/call-service` branch (PR #9). The script
fetches that branch into a folder next to this repo (`<repo>-call-service`) and runs the backend
from there. Your own checkout and branch stay as they are.

## Needs

- Git for Windows, with access to the GitHub repo
- Internet on the first run: it installs `uv` (after asking) and Python 3.12
- The PC and the iPhone on the same Wi-Fi

## Start

1. Double-click `start-backend.cmd`. For the firewall rule, right-click it and choose
   "Run as administrator" the first time.
2. It prints the backend address (`http://<pc-ip>:8000`) and the demo key.
3. On the iPhone, open the app and long-press the idle screen for 2 s. Enter the address, the key
   and `p_demo`, then Save. The "Connecting..." line goes away once the phone reaches the PC.
4. Double-click `start-call.cmd`. The phone rings.
5. To see one call's state and log afterwards, run:
   `.\start-call.ps1 -CallId <id>`

The demo key is made once and kept in `%LOCALAPPDATA%\checkin-backend\demo_key.txt`, outside the
repo. To set your own key, set `DEMO_KEY` before you start. Without `ELEVENLABS_API_KEY` the
avatar's lines are silent: you see captions only, and answers are not transcribed.

## If the phone cannot connect

- Windows may call the Wi-Fi "Public", and then it blocks incoming connections. Set the network to
  Private, or run as administrator with `.\start-backend.ps1 -AllowPublicNetwork`. Anyone on that
  Wi-Fi can then reach the backend, and the demo key is the only lock.
- Event Wi-Fi often keeps devices from seeing each other. Join the PC to the iPhone's Personal
  Hotspot instead and use the `172.20.10.x` address the script prints.
- Test from the PC: `curl.exe -H "X-Demo-Key: <key>" http://<pc-ip>:8000/v1/health` should print
  `{"ok":true}`.
