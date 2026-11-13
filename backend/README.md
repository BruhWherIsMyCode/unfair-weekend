# Backend

The [architecture decision](ARCHITECTURE_PLAN.md) places analyzers on the real parent client and keeps task policy, window timers, aggregation, baseline and final results in one Python backend. The [shared database guide](DATABASE_SCHEMA.md) and [initial SQLite migration](storage/migrations/0001_initial.sql) define persistence for the modules that will implement this flow. The old [OpenAPI 0.2 draft](proposals/README.md) is historical; its task sample-upload transport is superseded.

<a id="python-foundation"></a>

## Python voice and video call service

The parent call service from PR #9 is integrated here with the human's permission. It now supplies English ElevenLabs speech, transcribes uploaded answers, and offers locally prepared Simli MP4s per active speech command. [Voice/video setup and the iOS playback handoff](VOICE_VIDEO.md) describe configuration, preparation, fallback and the provisional `video_path` extension.

```bash
cd backend
uv sync --locked --extra dev
cp .env.example .env  # fill in the ignored local file
uv run --locked --env-file .env python -m checkin_backend.prepare --video --only-line greet
uv run --locked --env-file .env python -m checkin_backend.prepare --video
uv run --locked --env-file .env uvicorn checkin_backend.app:create_app --factory --host 0.0.0.0 --port 8000
```

`DEMO_KEY` is required. Provider keys enable real speech/transcription and explicit video generation. Prepared assets are cached; no video generation runs during calls. The existing parent app's PCM playback works with the speech route; **displaying the new video requires its owner's iOS change**. Without a key or cached audio the demo sends silence and reports `voiced: false`.

Implemented: registration/SSE, call lifecycle, playback acknowledgments, answer upload/STT, voice/cache and prepared video delivery, in-memory family feed, and a scripted fallback. Agent 3's session driver is selected when available. Without it, measuring is absent and the call finishes `incomplete`. The reused raw-task-media routes are compatibility/demo code from PR #9, not the revised observation protocol requested in [issue #10](https://github.com/BruhWherIsMyCode/unfair-weekend/issues/10). Runtime database persistence, SMS, deployed HTTPS and background ringing remain unimplemented.

```bash
uv run --locked --extra dev pytest -q
uv build --out-dir .cache/python-dist
```

Tests exercise a complete HTTP/SSE call with mocked voice, transcripts and video transport, plus lifecycle, cache, cancellation, authentication and provider-error paths. The Simli readiness fix reproduces delayed 404/429 responses and the permitted CDN redirect, with bounded retries and cancellation. One real ElevenLabs/Simli greeting was generated, downloaded and decoded successfully; [VOICE_VIDEO.md](VOICE_VIDEO.md#verification-and-limits) records that trial and its limits. Real answer STT and iPhone audio/video playback remain untested by Agent 2. No provider credits are used by the automated tests. The separate iOS family stage shell remains mocked.

## Running with Agent 3's analysis

`CALL_RUNNER=analysis` (or `auto`, which picks it when the package imports) runs `analysis.backend_entry.run_backend_call(link)` for every answered call: Agent 3's `CallSession` over this backend's `ClientLink`, the real smile and arms tasks on the JPEG frames the phone streams, her usual from a baseline store, and the family message from the result. The backend finds `analysis/` itself when it is in the repo root (the parent of `backend/`); no `PYTHONPATH` is needed. Install its requirements into the backend environment and fetch the model files once, from the repo root:

```bash
uv pip install -r analysis/requirements.txt   # or: pip install -r analysis/requirements.txt, inside the backend's venv
python -m analysis.get_models
```

The backend owns native smile/arms reader cleanup through [native_tasks.py](checkin_backend/native_tasks.py).
An interrupted attempt waits for its in-flight inference before closing MediaPipe, with cleanup on a worker
thread. Repeated cancellation keeps the analyzer slot busy until cleanup finishes; a retry gets a fresh reader.
Ten regression checks cover this lifecycle, late decoding, errors, retries and unchanged hand measurements.
A synthetic-image smoke test with real MediaPipe models completed 24 cancellations and two fresh attempts,
closing all 26 readers without inference/cleanup overlap. A live phone call still needs to be repeated.
The separate SQLite recording warnings are tracked with Agent 3 in [issue #24](https://github.com/BruhWherIsMyCode/unfair-weekend/issues/24).

Optional environment variables (all can stay empty; see `.env.example`). They are read for each call and checked before it starts; an invalid value ends the call with an `incomplete` family message.

- `CALL_RUNNER`: `auto`, `analysis`, `fake` (stand-in tasks, never a reading), `scripted`.
- `CALL_DRIVER`: `fixed` (default; the fixed order, no model) or `claude`. Empty follows `UW_CLAUDE=on`.
- `UW_CLAUDE_BACKEND`: `api` (default, needs `ANTHROPIC_API_KEY`) or `subscription` (the `claude` CLI login; the API key variable is removed from the process first).
- `UW_MIRRORED`: `on` when the phone frames are mirrored (default `off`).
- `UW_BASELINE_PERSON`: the key her usual is stored under (default: the person id). `UW_DATA_DIR`: the folder of the JSON baseline files (default `analysis/data`). `UW_BASELINE_NEEDED`: normal calls before her usual is ready (default 5; use 1-2 to try it out).
- `UW_SAFETY_FLOOR`: only `off` turns off the mid-call message for two same-side readings outside her usual. Experiments only.
- `UW_CALL_LOG`: `off` hides the live log (one plain line per step, prefixed `[call <id>]`). `UW_RUNS_DIR`: where the call record JSON is written (default `analysis/runs/calls`).

`DATABASE_PATH` (default: unset, database off): the shared SQLite file of [DATABASE_SCHEMA.md](DATABASE_SCHEMA.md). When set, the backend applies the migrations once at startup and passes the path to Agent 3's `run_backend_call`, which reads her usual from the database (the JSON store stays the fallback), records the call in the tables Agent 3 owns (`analysis/storage/README.md` lists them, the transactions they implement and what Agent 2's writers must provide) and adds a baseline snapshot after an eligible normal call. It needs the `calls` row of an answered call, written by the call service's own store; without that row (this branch has no such writer) recording switches itself off for the call with one log line and the call is unchanged. Added by Agent 3 for Agent 2's review.

Required checks on the phone path are smile and arms; the sentence is not measured on the server yet. Four script lines changed text with the one-hand phone script (`setup_phone`, `task_arms`, `repeat_arms`, new `swap_hands`; `data/script.en.json` is a copy of `contract/script.en.json`), so run `python -m checkin_backend.prepare` again to speak them; unchanged lines are reused from the cache. This integration was written by Agent 3 for Agent 2's review. Never run: real phone frames, mediapipe on the server, Claude, real providers.

## Shared database

[Open the interactive database guide](guides/database-guide.html) in a browser
for Russian explanations of all tables, their relationships, three call
scenarios and observation history. It is one self-contained HTML file; the
teaching model does not write to an actual database.

From `backend/`, using Python 3 and SQLite 3.38+:

```bash
python3 -m storage --database .cache/checkin.sqlite3
python3 -m unittest discover -s storage/tests -v
```

Built here: migration tooling, schema constraints and synthetic storage tests. [DATABASE_SCHEMA.md](DATABASE_SCHEMA.md) gives the transactions each writer must implement. The call service writes its own tables (see "Call service persistence" below). Observation routes, CallSession storage, client analyzers, timers and recovery beyond closing open calls are not implemented.

## Call service persistence

`DATABASE_PATH` (default `.cache/checkin.sqlite3`, git-ignored; empty turns it off) is the SQLite file. At startup the app applies `storage/migrations`, closes calls and commands a previous process left open (`shutdown` / `cancelled`) and seeds the configured people. [store.py](checkin_backend/store.py) then records, on one writer thread so the event loop never waits on SQLite:

- `families`, `persons`, `devices`: seeded people and registered phones.
- `calls`: ring, answer and end, with wall-clock deadlines and the session label.
- `call_events`: the journal of the ring, answer, every `command.execute` / `command.cancel`, every accepted client event and the end. Raw samples, audio and transcripts are not stored.
- `client_commands`: each say/capture command from `sent` through `started` to `completed`, `failed` or `cancelled`.
- `family_messages`, `notification_outbox`, `delivery_attempts`: one message per call with its SSE deliveries, plus the ring deliveries.
- `session_summaries`: the summary that came with each published message. A family message must reference one. Agent 3's coordinator should take this table over.

`GET /v1/families/{id}/messages` reads the stored feed, and `GET /v1/calls/{id}` falls back to the stored record (with `"stored": true`) for calls from an earlier process.

Limits: delivery is write-behind (the SSE push happens before its outbox row commits). Task capture commands have `attempt_id` NULL until Agent 3's `task_attempts` exist. Wire command and client event IDs are only unique per call, so they are stored as `<call_id>.<wire_id>`. `tests/test_store.py` runs whole calls through Uvicorn into a temporary database and checks the rows, foreign keys and journal order, a restart that reads the same history, and recovery of a call left open by a crash.


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

## Status of the Node reference

- Built: demo page, backend token creation, ElevenLabs speech generation, audio cache, timing display, and session cleanup.
- Verified: build, seven mocked-provider tests, Chromium setup state, and live ElevenLabs/Simli greeting playback and automatic session cleanup. A replay reused the speech cache. Timing observations are recorded in `DECISIONS.md`.
- Untested: actual iPhone playback, simultaneous native camera capture, and language/voice quality with the target users. Chromium mobile emulation only checks page layout.
- Not started: check-in call orchestration, parent camera processing, family messages, or iOS integration. These demo-only `/api/demo/*` routes do not change the shared `/v1` API or the contract script.

Provider references: [Simli JavaScript SDK](https://docs.simli.com/api-reference/javascript), [Simli token endpoint](https://docs.simli.com/api-reference/endpoint/webrtc/startAudioToVideoSession), [ElevenLabs speech API](https://elevenlabs.io/docs/api-reference/text-to-speech/convert).
