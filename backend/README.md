# Backend

The [architecture plan](ARCHITECTURE_PLAN.md) describes one Python backend with active-task observations, interruptions/recovery and a fixed script. The Python foundation below implements health and local connection state. The Node greeting experiment remains the provider reference to port. [Contract draft and checks](proposals/README.md).

## Python foundation

Requires Python 3.12 or newer and [uv](https://docs.astral.sh/uv/). The local default is Python 3.12. From the repo root:

```bash
cd backend
uv sync --locked --extra dev
export DEMO_KEY='replace-with-your-local-demo-key'
uv run --locked uvicorn checkin_backend.app:create_app --factory --host 127.0.0.1 --port 8000
```

In another terminal with the same `DEMO_KEY` exported:

```bash
curl --fail -H "X-Demo-Key: $DEMO_KEY" http://127.0.0.1:8000/v1/health
```

The response is `{"ok":true}`. A missing/incorrect key receives a 401 error. Missing `DEMO_KEY` prevents startup. Python reads exported environment variables; it does not automatically load `.env`. Provider credentials are not needed for this foundation.

[CallService](checkin_backend/calls.py) is a local Python module, separate from Agent 3's conversation/measurement state. It creates call IDs, permits one active call per person, handles answer/decline/hang-up/connection loss, and resolves a terminal future once. Defaults are a 30-second ring timeout and a 180-second connected-call deadline, configurable with `RING_TIMEOUT_S` and `CALL_TIMEOUT_S`. Repeated answers do not extend the deadline. Shutdown ends active calls and joins their timers; cancelling a waiter does not cancel the shared result.

Connection records are in memory for this process's lifetime; they are not persisted session history. Use one process/worker and call the service on its asyncio event loop. The app owns it at `app.state.calls` for later route/orchestrator integration.

```bash
uv run --locked --extra dev pytest -q
uv build --out-dir .cache/python-dist
```

The tests cover app startup/shutdown, authentication, deadline/answer races, duplicate transitions, person isolation and cancelled waiters. The app uses FastAPI's [lifespan and TestClient context](https://fastapi.tiangolo.com/advanced/testing-events/) for resource cleanup checks. Tests do not contact providers or use people recordings.

Built and locally tested: Python entrypoint, protected `/v1/health`, configuration and local connection lifecycle. Pending: registration/SSE and call HTTP routes, playback acknowledgments, Python provider adapters, media ingress, Agent 3 integration, persistence and family delivery. Baseline network schemas are awaiting canonical publication in [issue #6](https://github.com/BruhWherIsMyCode/unfair-weekend/issues/6); local records are not a replacement wire contract. The iOS stage shell remains mocked. This foundation does not yet demonstrate a joined call.

## Call service (parent client protocol)

The parent client talks to these routes. They follow [contract draft 0.2](proposals/mvp.openapi.yaml), plus the v0 registration, call and family routes.

- `POST /v1/devices` registers a phone: `{device_id, role: parent|family, person_id|family_id}`.
- `GET /v1/devices/{id}/events` is the SSE channel. It carries `call.incoming`, `call.ended`, `command.execute` (say/capture), `command.cancel`, `task.progress` and `message.new`. A reconnect with `Last-Event-ID` replays missed events. A fresh connection replays only active work.
- `POST /v1/calls` (operator: `{person_id}`), `GET /v1/calls/{id}` (state, family message, summary, recent log), `POST /v1/calls/{id}/answer|decline`.
- `POST /v1/calls/{id}/client-events`: `client.ready`, `playback.completed`, `capture.started`, `command.cancelled`, `command.failed`, `resource.changed`, `hangup`. Duplicates are idempotent; a conflicting reuse returns 409.
- `GET /v1/calls/{id}/speech/{cmd}` returns the prepared PCM16 16 kHz line. `POST /v1/calls/{id}/captures/{cmd}` takes the answer audio for STT.
- `POST /v1/calls/{id}/attempts/{attempt}/samples|finish` takes task input. JPEG ≤640×480 or PCM ≤250 ms per sample, with sequence, size and format checks.
- `GET /v1/families/{family_id}/messages` lists family messages. There is one message per call, upserted by ID.

`ClientLink` ([link.py](checkin_backend/link.py)) implements the `CallAdapter` that Agent 3's `CallSession` expects (`say`, `listen`, `start_capture`, `cancel_capture`, `samples`, `publish`, `send_progress`). It forwards client events to `handle_*` / `hang_up`. `say` returns after the client's `playback.completed`, not after TTS generation.

`CALL_RUNNER` picks what drives an answered call:

- `auto` (default): Agent 3's `analysis` package when it is importable, otherwise `scripted`.
- `analysis`: CallSession with the real task modules.
- `fake`: CallSession with input-only stand-in modules. These return `failed` and no reading.
- `scripted`: fixed lines and captures with no analysis, always `incomplete`.

`CALL_DRIVER=fixed|claude` is passed to Agent 3's `run_call`. Without `ELEVENLABS_API_KEY`, lines become silence of an estimated length (captions only) and answers are not transcribed.

`tests/client_sim.py` is a scripted parent client that speaks the real HTTP/SSE protocol. `tests/test_call_service.py` runs whole calls through Uvicorn: the scripted call, decline/ring timeout, hang-up, protocol rejections, and two runs with Agent 3's `CallSession` (normal call, plus camera loss → `command.cancel` → `camera_unavailable` → new attempt). The CallSession tests run only with Agent 3's branch on `PYTHONPATH` and `numpy` installed; otherwise they are skipped. Not verified: real ElevenLabs STT/TTS from Python, a real phone, Simli in the parent app, and the real smile/arms analyzers fed with these samples.

## Simli + ElevenLabs greeting reference

A local browser prototype: type a short line, generate its voice with ElevenLabs, and send that audio to a live Simli avatar. The browser plays the synchronized video and audio returned by Simli. It does not request camera or microphone access.

Requires Node.js 22.9 or newer.

```bash
cd backend
npm ci
cp .env.example .env
```

In `backend/.env`, set `SIMLI_API_KEY` and `ELEVENLABS_API_KEY`. The optional voice, model, and avatar IDs have defaults. API keys stay on the backend; the browser receives only a temporary Simli session token. The backend's `.env`, generated speech cache, and build output are ignored by Git.

An existing project-root `.env` is also loaded, with `backend/.env` taking precedence. `SMILI_API_KEY` is accepted as an alias for `SIMLI_API_KEY`. Keep any project-root credential file untracked.

```bash
npm run dev
```

Open <http://localhost:3000>, press **Play greeting**, and allow audio playback if prompted. **End** closes the session. Changing tabs also ends it. Sessions request a 90-second maximum and a 20-second idle limit. The page stops after the known speech duration plus a short playback allowance. This is a timing estimate, not a per-line playback acknowledgment suitable for the final call flow.

The server binds to `127.0.0.1` and accepts localhost hosts and same-origin JSON requests only. This is a local test, not a publicly deployed service. `PORT` can change the local port. Restart after changing `.env`.

ElevenLabs generates 16 kHz, mono, signed 16-bit PCM, matching Simli's input. Generated lines are cached under `.cache/speech/` by text, voice, model, and format. Repeated lines reuse that audio across restarts; each live avatar session still consumes Simli time. The demo limits lines to 300 characters and 40 seconds of generated audio. It permits three session-token requests and ten speech requests per minute.

Check the installed application:

```bash
npm run build
npm test
```

Tests use mocked provider responses and never spend API credits. They cover the audio format, persistent cache, duplicate requests, session caps, input validation, local request restrictions, and error redaction.

### Greeting reference status and scope

- Built: demo page, backend token creation, ElevenLabs speech generation, audio cache, timing display, and session cleanup.
- Verified: build, seven mocked-provider tests, Chromium setup state, and live ElevenLabs/Simli greeting playback and automatic session cleanup. A replay reused the speech cache. Timing observations are recorded in `DECISIONS.md`.
- Untested: actual iPhone playback, simultaneous native camera capture, and language/voice quality with the target users. Chromium mobile emulation only checks page layout.
- Not started: check-in call orchestration, parent camera processing, family messages, or iOS integration. These demo-only `/api/demo/*` routes do not change the shared `/v1` API or the contract script.

Provider references: [Simli JavaScript SDK](https://docs.simli.com/api-reference/javascript), [Simli token endpoint](https://docs.simli.com/api-reference/endpoint/webrtc/startAudioToVideoSession), [ElevenLabs speech API](https://elevenlabs.io/docs/api-reference/text-to-speech/convert).
