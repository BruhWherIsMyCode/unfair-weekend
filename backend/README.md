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

Tests exercise a complete HTTP/SSE call with mocked voice, transcripts and video transport, plus lifecycle, cache, cancellation, authentication and provider-error paths. Real provider generation and iPhone audio/video playback are untested in this worktree; provider credentials are not configured. No provider credits are used by the tests. The separate iOS family stage shell remains mocked.

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

Built here: migration tooling, schema constraints and synthetic storage tests. Runtime repositories, observation routes, CallSession, client analyzers, timers and notifications are not implemented by this change. The call service above has in-memory runtime state; the migration does not yet persist its records. [DATABASE_SCHEMA.md](DATABASE_SCHEMA.md) gives the transactions each writer must implement.

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
