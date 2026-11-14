# HeartLine

HeartLine is a short daily video call for an older person who lives alone. An AI avatar rings her iPhone, asks how she is, and asks her to smile and to hold up each hand. HeartLine compares her face and hands with her own earlier calls. If something looks different from her usual, or she says something that worries the avatar, her family gets one plain message on their phone so they can call her.

A family message reads like this:

> Ona's smile looks different from her usual. Call her now. If she seems unwell, call 112.

HeartLine gives no health verdict and does not call emergency services. It notices a change from her own usual and asks her family to call her.

We built it at Unfair Weekend 2026 in Vilnius, 12 to 15 November.

## How a call goes

The backend rings the parent's iPhone through CallKit, so it looks like a normal phone call, also on a locked screen. She answers and sees the avatar. It greets her by name and asks three questions: whether she has taken her medicine today, how she feels, and whether anything has felt unusual since the last call.

Then come two short checks. For the smile check she relaxes her face and smiles when the avatar says "smile". For the hand check she holds the phone in one hand, raises the other hand beside her face, and then swaps hands. During each check the phone sends camera frames to the backend. The backend measures how level the two corners of her mouth are and whether a raised hand sinks, and compares each reading with her own usual range. Her first two calls only learn that range. If a check can't be measured, for example because her face is out of view, the avatar asks her to try once more.

Claude runs the conversation. It chooses the next question or check, replies to her answers in a short sentence of its own, and can raise a concern if she says she feels unwell or asks for help. Fixed code, not the model, decides whether a reading counts as a change.

The avatar never tells her a result. If it noticed a change or a concern, it adds that she should call 112 if she feels unwell, then says goodbye. When the call ends, the family's app gets one message. A change from her usual or a concern arrives as an alert with today's video next to an earlier check-in. A call that could not be finished or was not answered arrives as a plain notification. A call where everything was as usual sends nothing.

## What is in this repo

| Folder | What it holds |
|---|---|
| [backend/](backend/README.md) | The Python (FastAPI) call service. It rings the phone, sends the avatar's lines, receives answers and camera frames, and delivers the family message. ElevenLabs provides the voice and speech-to-text, Simli the avatar video, and SQLite keeps call records. |
| [analysis/](analysis/README.md) | Python modules that the backend imports: the call session, the Claude call agent, the smile and hand measurements, and the comparison with her usual. The measurements use Google MediaPipe face and hand points and our own arithmetic. Nothing is trained. |
| [contract/](contract/README.md) | The shared API between the apps and the backend: the OpenAPI file, the avatar's script lines and example payloads. |
| [ios/ParentApp/](ios/ParentApp/), [ios/ParentCallKit/](ios/ParentCallKit/README.md) | The parent's iPhone app and its call engine. It rings, shows the live avatar and her own camera, and streams frames during a check. |
| [ios/App/](ios/App/README.md) | The family's iPhone app. It receives the message, plays today's video next to an earlier check-in, keeps the call history and calls her number. It also contains the staged scene we use on stage. |
| [docs/](docs/) | Project context, the shared contract guide, team briefs and design exports. |

## Run it

You need Python 3.12 with [uv](https://docs.astral.sh/uv/), Xcode for the two iPhone apps, and API keys for ElevenLabs, Simli and Claude. Keys go in `backend/.env`, which git ignores. The phones and the computer running the backend must be on the same network.

```bash
cd backend
uv sync --locked --extra dev
cp .env.example .env                       # set DEMO_KEY and the provider keys
uv pip install -r ../analysis/requirements.txt
(cd .. && backend/.venv/bin/python -m analysis.get_models)
uv run --locked --env-file .env python -m checkin_backend.prepare --video
uv run --locked --env-file .env uvicorn checkin_backend.app:create_app --factory --host 0.0.0.0 --port 8000
```

On Linux or Windows, `ios/App/tools/linux/start-backend.sh` or `ios/App/tools/windows/start-backend.cmd` does all of this in one step. The [backend README](backend/README.md) lists every setting.

Build `ios/ParentApp` and `ios/App` in Xcode and set your signing team in each app's `Local.xcconfig` (copy it from `Local.xcconfig.example`). In the parent app, long-press the idle screen for 2 seconds and enter the backend address, the demo key and `p_demo`. In the family app, hold the "Family" title for 2 seconds and connect to the same backend with family `f_demo`.

Start a call:

```bash
curl -X POST -H "X-Demo-Key: $DEMO_KEY" -H 'Content-Type: application/json' \
  -d '{"person_id":"p_demo"}' http://<backend-ip>:8000/v1/calls
```

## Status and limits

The team has run full calls on real iPhones, acting both usual calls and calls with a lopsided smile or a sinking hand. HeartLine has not been tried with older people or checked by doctors. The thresholds that decide what counts as a change are our first estimates.

The repeated-sentence check runs only in the laptop version in `analysis/pc/`. The phone call measures the smile and the hands.

Without a paid Apple developer account there is no VoIP push. Both apps keep running in the background with a silent audio loop, so if iOS closes an app it cannot ring or receive alerts until someone opens it again. The backend runs on a laptop, and there is no hosted version.

During a check the phone sends camera frames to our backend. Claude receives the transcripts of her answers and the measured numbers, not video or audio. Saved check videos are optional and are deleted after seven days by default.

## Team rules

[AGENTS.md](AGENTS.md) describes how the team and its AI coding agents split the work. [docs/PROJECT_CONTEXT.md](docs/PROJECT_CONTEXT.md) holds the wording rules for anything users see.
