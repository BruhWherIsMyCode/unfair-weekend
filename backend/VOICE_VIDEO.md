# Voice and video for the parent call

Implemented on `agent-2/backend`, using the parent call service from PR #9 with the human's explicit permission. The call speaks English fixed script lines, uploads spoken answers to ElevenLabs, and supplies prepared Simli video when available. This is a scripted check-in with live answers. Agent 3 owns conversational decisions and measuring.

## Run

From `backend/`:

```bash
uv sync --locked --extra dev
cp .env.example .env
```

Edit the ignored `.env`: set `DEMO_KEY`, `ELEVENLABS_API_KEY`, `SIMLI_API_KEY`, and the desired person name/voice/avatar IDs. Do not put credentials in URLs or source files. `uv --env-file` loads this file; Python itself does not load it.

Prepare a single greeting first (uses provider credits), then the complete script:

```bash
uv run --locked --env-file .env python -m checkin_backend.prepare --video --only-line greet
uv run --locked --env-file .env python -m checkin_backend.prepare --video
uv run --locked --env-file .env uvicorn checkin_backend.app:create_app --factory --host 0.0.0.0 --port 8000
```

Omit `--video` to prepare voice alone. Repeating preparation reuses local assets. Changing the person name, script text, voice/model or face produces the corresponding new assets. Preparation stops at the first failure; it never reports silent demo audio as prepared speech. Use HTTPS for a deployed server. For a local phone test, both devices must be able to reach this computer on the same Wi-Fi or hotspot.

Simli returns its MP4 URL before rendering finishes. Preparation waits for the reported `mp4_availablility_eta_seconds` plus one second (bounded at 60 seconds), then retries download-only 404/429 responses. Pauses increase from 3 seconds by a factor of 1.5 to 15 seconds; a longer `Retry-After` is respected. There are at most 20 download attempts within a five-minute wait/download budget, after the generation request. The generation POST is not repeated by these retries. One 307 redirect is accepted only to HTTPS on `simlimicrocdn.blob.core.windows.net` under `/mp4/`; later retries use that location. Downloads receive no account key. Only a bounded, container-checked MP4 is committed to the local cache. Failure or shutdown leaves no partial asset. This corrects [issue #15](https://github.com/BruhWherIsMyCode/unfair-weekend/issues/15).

The existing parent app can use its current backend-address, demo-key and `p_demo` settings. Register the phone before starting a call. With `DEMO_KEY` exported in another terminal:

```bash
curl --fail -H "X-Demo-Key: $DEMO_KEY" -H 'Content-Type: application/json' \
  -d '{"person_id":"p_demo"}' http://127.0.0.1:8000/v1/calls
```

One process/worker only. The reused service's devices, connection state and family messages remain in memory. The shared SQLite schema is not wired into it. `CALL_RUNNER=auto` selects Agent 3's analysis package when installed, otherwise the fixed script. That fallback asks the three questions and exercises capture, but performs no measuring and finishes `incomplete`. This change does not implement or certify the revised client-observation protocol in issue #10; the inherited task-media endpoints remain compatibility/demo code from PR #9.

## iOS playback handoff

**Voice works with the existing PR #9 PCM player.** Its owner reports video playback implemented on that PR in [issue #15](https://github.com/BruhWherIsMyCode/unfair-weekend/issues/15); the native changes are not part of this backend branch. The new field is a provisional extension for Agent 3 to publish; it does not replace the shared contract.

An existing `command.execute` / `kind: say` gains `video_path` only when its exact audio has a prepared MP4:

```json
{
  "command_id": "cmd_example",
  "kind": "say",
  "line_id": "greet",
  "text": "Hello, Ona. It's time for our short check-in.",
  "audio_path": "/v1/calls/call_example/speech/cmd_example",
  "duration_ms": 4000,
  "video_path": "/v1/calls/call_example/video/cmd_example"
}
```

`duration_ms` above is illustrative. It is metadata, never a completion signal.

1. Decode `video_path` as optional. Fetch it with `X-Demo-Key` and play the downloaded MP4, including its audio, with AVPlayer. The backend supports byte ranges and `video/mp4`. Do not simultaneously play the PCM track.
2. If no video is offered, use the existing PCM player and the app's avatar placeholder. On a video error, stop that player before attempting the voice fallback. If fallback also fails, report `command.failed`.
3. Send the existing `playback.completed` with the same command ID only after actual successful playback end. With AVPlayer, distinguish `AVPlayerItemDidPlayToEndTime` from failed-to-play and cancellation. Do not use a duration timer or Simli signaling events as playback evidence.
4. On cancel, hang-up or app teardown, stop playback and discard pending downloads. A cancelled/old command must never acknowledge successful completion or start capture. The existing server command/capture flow stays authoritative.

Audio and video routes require the demo header, belong to one call/command, and return 410 after command completion or call end. Provider keys and signed provider URLs are never sent to the phone. The current parent client ignores the additional video field and continues with PCM. iPhone playback, simultaneous camera/microphone capture and audio routing require a device check by the iOS owner.

## Live avatar (provisional, 13 Nov)

With `AVATAR_MODE=live` (the default) and `SIMLI_API_KEY` set, the parent phone asks for a Simli live session once the call is answered:

```
POST /v1/calls/{call_id}/avatar/live   (X-Demo-Key)
200 {"session_token": "...", "ws_url": "wss://api.simli.ai/compose/webrtc/livekit"}
503 {"code": "avatar_unavailable"}   live is off, the key is missing or Simli refused
409 not answered yet, 410 call ended
```

Set `AVATAR_PREGENERATED_CLIPS=off` alongside `AVATAR_MODE=live` to disable prepared avatar MP4s entirely. Preparation commands (including `--video`) then prepare voice only, and calls omit cached `video_path` assets. Existing MP4s remain on disk but are not offered. Live animation still uses Simli and ElevenLabs; keep their keys configured. This switch does not control retained camera-task recordings for the family (`CLIP_STORAGE_DIR`). Unset or `on` keeps the existing prepared-video behavior. Restart the backend after changing it.

The backend calls Simli's `POST /compose/token` with the account key (`faceId`, `handleSilence`, `maxSessionLength` = call timeout + 60 s, `maxIdleTime` 120 s, `pcm16`). The phone receives only the short-lived session token. It opens the WebSocket, joins the LiveKit room that Simli names, and for each `say` downloads the existing `audio_path` PCM and sends it over the socket. This includes Claude's newly generated remarks and clarification questions: live mode animates their speech without generating an MP4 during the call. The avatar's own LiveKit audio is the voice, in sync with the lips. The first line waits up to 10 s for the live connection, then uses a prepared clip if the connection is not ready. `AVATAR_MODE=clips` turns live off without rebuilding the app; generated lines without a prepared clip then have voice but no new animation.

The native replay fix separates playback from its acknowledgment. A line can fall back immediately only when the live player explicitly reports that audio was not attempted (`LivePlaybackFailure.notStarted`). An interrupted or uncertain live line reports `playback_failed` and disables live for subsequent commands; that same line is not replayed as a clip. Successful playback sends `playback.completed` separately, with bounded acknowledgment-only retries preserving the event ID and body. An acknowledgment failure never plays the line again. These changes require rebuilding the parent app. The native `live-avatar` logging category records local line counters, elapsed time, submitted-byte counts and fixed failure categories, without provider tokens, URLs, text or media.

A 14 Nov phone trace reproduced the original duplicate within one command: the backend sent one left-hand instruction, the phone fetched live audio and then the prepared video for the same command about 5.2 seconds later, and the hand check completed in one usable attempt. A comparison with prepared clips played the instruction once, as confirmed by the operator. The exact native error that caused the transition was not recorded by the old app. The updated native code has not yet been checked in a rebuilt iPhone app. Live mode remains the default for the demo.

This is a provisional extension for Agent 3 to publish. It follows the simli-client 3.2.0 source. The Simli `SILENT` event is used as playback evidence in live mode, because the stream has no per-line player end. Mic recording during an active LiveKit session needs an iPhone check.

## Family alert clip (provisional, 13 Nov)

While a task runs, the backend keeps the parent phone's JPEG frames per attempt in memory (at most 100 per attempt). When a `change_noticed` message is published for a confirmed `face` or `arm` sign, the frames of every attempt at that task (`smile`, or tasks starting with `arm`) are joined into one clip on the delivery shelf, and the message's `clip_url` becomes `/v1/clips/{clip_id}`. The final summary of the same call keeps that URL. `GET /v1/clips/{clip_id}` (demo key) returns `{"clip_id", "format": "jpeg", "period_ms", "frames": [base64 JPEG…]}`, at most 160 frames. Clips live only in memory: never on disk or in the database. They expire 30 minutes after creation, and the link drops its frames when the call ends. Speech changes and concerns carry no clip. This changes the contract's "family clips deferred" (`clip_id: null`) and needs Agent 3 to publish it.

## Fallback and privacy

Generated speech is PCM16 little-endian, 16 kHz, mono. Prepared audio and MP4s live in ignored `.cache/speech/` and `.cache/avatar/`, written atomically with file mode 0600. Cached voice/video continue working with provider keys removed. An uncached line without an ElevenLabs key is an explicit caption-only demo: `/answer` reports `voiced: false`; no answer transcript is invented. A provider error is reported as a failure, without its response body or credentials.

Ordinary answer recordings are sent temporarily to ElevenLabs and are not written by the speech gateway to disk. The transcription is returned to the session. No person recordings or generated assets are committed. Preparing videos sends only synthetic avatar speech to Simli. An avatar video is not a demonstration of the smile/arms task; it only speaks the instruction. Task-demonstration visuals remain unimplemented.

## Verification and limits

Automated provider tests use mock HTTP responses and synthetic media containers. A real Uvicorn/SSE call test exercises voice download, prepared MP4 download/ranges, three answer uploads and returned transcripts, completion, expired assets and authentication. The synthetic MP4 is a transport fixture, not a playable video or a lip-sync test. Regression tests reproduce delayed 404s, 429/Retry-After, the permitted CDN redirect, rejected redirects, attempt/time bounds and shutdown during rendering waits.

The issue #15 fix was also checked against real ElevenLabs and Simli using existing local provider configuration: one 2.508-second English greeting produced a 287,000-byte MP4 in 7.95 seconds including voice generation and preparation. That request downloaded directly with HTTP 200 after the readiness wait. FFprobe identified H.264 768×768 at 25 fps and AAC 16 kHz mono, duration 2.64 seconds; FFmpeg decoded both streams without errors. These are observations from one trial, not a latency benchmark or an iPhone/lip-sync assessment. CDN/throttling paths are tested with mocks; live answer STT and iPhone playback remain unverified by Agent 2. Generated media and credentials remain ignored local files.

The existing Node live-avatar greeting experiment remains separate. In the Python/iOS call path, the native phone streams each line's prepared PCM to the live avatar; cached MP4s and voice provide fallback. ElevenLabs also offers an [HTTP speech stream](https://elevenlabs.io/docs/api-reference/text-to-speech/stream), but this call path prepares a complete line before sending it. Simli accepts the audio over WebSocket and returns the live avatar's audio/video over [WebRTC](https://docs.simli.com/api-reference/simli-webrtc). Its continuous-stream `speaking`/`silent` events have no per-line playback identity. Prepared MP4 playback instead lets the client acknowledge its own player ending before the next microphone/camera step.

Provider references checked 13 Nov 2026: [ElevenLabs speech](https://elevenlabs.io/docs/api-reference/text-to-speech/convert), [ElevenLabs transcription](https://elevenlabs.io/docs/api-reference/speech-to-text/convert), [Simli static video](https://docs.simli.com/api-reference/generate-static-video), [Simli OpenAPI with MP4 response fields](https://api.simli.ai/openapi.yaml), [Simli streaming protocol](https://docs.simli.com/api-reference/simli-webrtc).
