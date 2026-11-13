# Backend decisions and evidence

Current as of 13 Nov 2026. [ARCHITECTURE_PLAN.md](ARCHITECTURE_PLAN.md) owns the full flow. The latest team decision replaces server-side task-media analysis with client analyzers and server-owned policy/timers/aggregation. Earlier Swift-baseline, two-service and task sample-upload proposals are historical.

## Current architecture

- **One Python backend and client analyzers.** Agent 2 owns connection/event/speech/delivery modules; Agent 3 owns server task runners, orchestration, final-result rules and canonical contracts. The real client owns capture/feature extraction, with implementation ownership still to assign. There is no internal HTTP service boundary.
- **One active measurement task per call.** Client analyzers emit structured observations; resource events are immediate. CallSession owns window/watchdog timers, applies rules, commands normal stop or interruption and creates the terminal tool result. The client countdown is UI only; a bounded local lease releases resources if the server disappears.
- **A fresh attempt after interruption.** Routine observations update state without a model turn. Partial/interrupted observations cannot confirm a change or enter the baseline. Late events cannot revive a stopped attempt.
- **Fixed script with bounded recovery.** English draft `mvp-en-2` includes camera/microphone recovery lines; one-second observations and task windows are engineering proposals. Agent 3 must define sufficient feature statistics and server aggregation before numeric implementation.
- **Explicit confirmation and delivery.** Two usable final outside readings under the agreed rule trigger a fixed family message. A final summary updates the same call/message ID. Technical failure is not a measured change.
- **Durable state and event history.** [DATABASE_SCHEMA.md](DATABASE_SCHEMA.md) and [migration 1](storage/migrations/0001_initial.sql) define current snapshots, append-only observations, terminal results, baselines/model history and revisioned family delivery. Schema tooling/tests are implemented; runtime persistence and restart cleanup are not.
- **Wire revision required.** [proposals/](proposals/README.md) retains historical draft 0.2, exact lines and synthetic fixtures. Its task sample ingress is superseded; Agent 3 must publish the revised client analyzer contract in `contract/`.

The merged [Python foundation PR #8](https://github.com/BruhWherIsMyCode/unfair-weekend/pull/8) implements health/configuration and an in-memory CallService. This change implements storage schema/tooling, not application repositories, CallSession, analyzers, timer execution, baselines, family delivery or real-client integration. The current iOS stage task remains a separate mock.

## Python foundation

### Voice/video integration, 13 Nov

#### Rendering readiness correction, issue #15

The parent-client owner's real API trial identified a wrong assumption in the initial adapter: `POST /static/audio` returns before its MP4 is ready. The immediate GET failed on the expected rendering-time 404, and the downloader also rejected Simli's CDN redirect. The observed ETA field is spelled `mp4_availablility_eta_seconds` by the provider; it is not currently listed in the static-response OpenAPI properties.

The corrected adapter waits ETA + 1 second (at most 60 seconds), retries only download-time 404/429 with 3-second × 1.5 backoff capped at 15 seconds, and respects longer numeric/date `Retry-After` values. Polling is bounded by 20 attempts and a five-minute overall download/readiness deadline after the generation request. The paid generation POST is never retried within this loop. A single 307 may switch download to HTTPS on `simlimicrocdn.blob.core.windows.net` under `/mp4/`; further retries stay there and another redirect is rejected. No account key is sent to either download host. Existing size/container validation and private atomic caching remain in place. Cancellation stops pending waits and leaves no partial ready asset.

A regression test failed with the original `Simli video returned HTTP 404`, then passed for `404 → 429 → 404 → 307 → MP4`. Additional tests cover direct responses, retry-after dates/invalid hints, bounded ETA/attempts/deadline, CDN readiness, disallowed/second redirects, and shutdown. Automated tests use mocked providers and synthetic containers.

One real greeting trial used the existing ignored provider configuration in the main working copy, without copying or logging credentials. ElevenLabs produced 2.508 seconds of speech; Simli preparation and download produced a 287,000-byte MP4 in 7.95 seconds total, with a direct HTTP 200 download after waiting. FFprobe reported H.264 768×768 at 25 fps and AAC 16 kHz mono, duration 2.64 seconds. FFmpeg decoded the whole audio/video without errors. The real trial did not exercise throttling or a CDN redirect; those paths are covered by mocks and the client's reported evidence in [issue #15](https://github.com/BruhWherIsMyCode/unfair-weekend/issues/15). Live STT, subjective voice/lip-sync quality and an iPhone call remain untested by Agent 2.

Both providers offer streaming: ElevenLabs HTTP TTS `/stream`, Simli WebSocket input and WebRTC output; static Simli generation also returns HLS. The Python call path still uses prepared assets, keeping completion tied to the client's media player. A continuous-media client and its line-completion protocol would be a separate native/contract integration.

#### Initial integration scope

The human authorized reuse of `backend/` from PR #9 on `agent-2/backend` before canonical contracts have landed, and selected English. Only backend files were taken; the SQLite schema and current architecture decision were retained. The parent's native client is still owned separately.

The service now runs registration/SSE, call commands, answer upload/transcription and an in-memory family feed. ElevenLabs generates PCM16 little-endian, 16 kHz mono; Scribe v2 transcribes ordinary answers in English. Speech generation is bounded, coalesced and cached privately on disk. Cached audio is read before credentials are checked, so prepared speech remains available when the voice provider is off. Uncached audio without a key remains an explicitly unvoiced demo.

For the first joined voice/video path, choose **clips**: explicitly prepare Simli MP4s from each exact ElevenLabs line before a call. The server offers optional `video_path` on a speech command only for a locally ready MP4; otherwise the existing PCM command works. No video generation or provider URL expiry sits in the call path. Simli static-video request/response fields were verified against its current official OpenAPI, including `audioFormat: pcm16` and `mp4_url`. Remote assets are downloaded only from the documented HTTPS host/path, without the account key, bounded in size/time, container-checked and stored locally. Provider redirects and error bodies are not forwarded.

This choice gives the client a media-player completion signal. The continuous Simli stream's `speaking`/`silent` events do not identify a line or prove device playout; the old Node experiment remains separate. The video speaks an instruction; it does not visually demonstrate the smile/arms task.

Built and locally tested: provider adapters/cache/shutdown, preparation command, optional video delivery/authentication/byte ranges, real HTTP/SSE call transport with synthetic provider responses and three returned answer transcripts. The fixed-script driver has no measuring and honestly finishes `incomplete`. Agent 3 integration tests skip without its package; no analysis code was taken from another branch. Provider credentials are absent in this worktree: no real TTS/STT/video generation or iPhone video playback was tested here, and no new latency/quality figures are claimed. [VOICE_VIDEO.md](VOICE_VIDEO.md) is the operator setup and native playback handoff.

The optional video field/route are a provisional extension for Agent 3 publication. The inherited PR #9 task-media routes remain compatibility/demo code, not implementation of the revised observations protocol in issue #10. Runtime SQLite wiring, SMS, deployment and native video playback remain outstanding.

The foundation app factory in `checkin_backend/app.py` owns a `CallService` during FastAPI lifespan. `DEMO_KEY` is required; no provider credential is needed for health or local lifecycle tests. The existing v0 health sketch supplies `/v1/health` and `{ok: true}`. The later voice/video integration above reuses PR #9's provisional client routes; canonical publication of the revised boundary remains with Agent 3 under issue #10. Local immutable call records do not define client payloads.

Connection state is confined to one process and one asyncio event loop, separate from CallSession's conversation/measurement state. Ring timeout defaults to 30 seconds; connected-call duration defaults to 180 seconds from the first answer. Monotonic time governs deadlines. Late answers cannot revive expired calls, repeated transitions cannot extend deadlines, and closing the app ends active calls and joins deadline tasks. A cancelled consumer does not cancel the shared terminal result. Records are held in memory until process exit. The shared SQLite schema is available, but runtime persistence/recovery remain future work.

Local verification uses `uv run --locked --extra dev pytest -q`. It covers protected health, configuration, lifecycle cleanup, no-answer expiry, connected deadlines, answer/timeout ordering, active-person isolation, terminal replay and cancelled waiters. A real Uvicorn HTTP smoke check returned authenticated 200 and missing-key 401, completed application shutdown after SIGTERM and kept the generated credential out of logs. The runtime lock uses FastAPI 0.162.5, Starlette 1.10.0 and Uvicorn 0.54.0; a `pip-audit` check of the exported runtime requirements reported no known vulnerabilities. These checks do not verify a real client, provider playback, analyzer cancellation or family delivery. [backend/README.md](README.md#python-foundation) contains install/run instructions.

## Runnable provider experiment

The existing browser experiment uses Node, `simli-client` 3.2.0 and ElevenLabs TTS. It is reference code to port, not another required production service. It plays a supplied short line; it does not request camera/microphone access or run a check-in.

- Raw speech input to Simli: signed PCM16, 16 kHz, mono. TTS lines are cached locally by text/voice/model/format.
- API keys stay server-side; the browser receives a temporary Simli session token.
- Session requests are capped at 90 seconds with a 20-second idle limit. The page ends after estimated speech duration plus an allowance, or when hidden. This does not prove the actual playback acknowledgment required for task timing.
- The localhost server limits request size/rate and static file access. Local credentials, recordings and generated files are ignored.

## Recorded verification

The storage change passes 26 synthetic migration/constraint tests, including atomic terminalization, durable observation history, frozen comparisons, stale input, command identity, baseline eligibility and delivery deduplication. Migration CLI apply/reapply, the Node build and seven mocked-provider tests pass. These checks do not demonstrate runtime persistence, watchdog execution, restart recovery or a real client. Agent 3's shared-boundary review is [issue #10](https://github.com/BruhWherIsMyCode/unfair-weekend/issues/10).

On 12 Nov the prototype build, seven mocked-provider tests and dependency audit passed. Chromium verified setup state, console output and layout at an emulated 390-pixel viewport. Mobile emulation is not iPhone media verification.

Two real greeting trials used local provider credentials. The first prepared ElevenLabs audio in 1.14 seconds and rendered the first avatar frame 2.31 seconds after connection began. A replay reused cached audio in 0.01 seconds and rendered its first frame in 2.91 seconds. Simli's first speaking event followed audio submission by 0.08/0.13 seconds. Both reached automatic completion. These are individual observations, not benchmarks, subjective listening assessments or end-to-end voice-latency measurements.

The historical 0.2 draft checker validates OpenAPI, 39 valid and 25 invalid synthetic fixtures, task settings, sample encoding and script-table consistency. It does not validate the revised client analyzer protocol or prove runtime interruption/cancellation or measurement performance.

Provider references checked on 12 Nov: [Simli SDK](https://docs.simli.com/api-reference/javascript), [Simli pricing](https://www.simli.com/pricing), [ElevenLabs speech](https://elevenlabs.io/docs/api-reference/text-to-speech/convert). These links are dated evidence, not a fresh pricing/availability check.

## Remaining verification and decisions

Actual iPhone/web-view playback, simultaneous capture, client feature quality/coverage, real playback acknowledgments, server aggregation/timers, interruption latency, database runtime integration/restart cleanup, deployment and family delivery remain untested. The real parent client, final avatar mode, target voice/language, notification provider and derived-data retention remain open. Prepared voice/still fallback is planned but not built in this experiment.
