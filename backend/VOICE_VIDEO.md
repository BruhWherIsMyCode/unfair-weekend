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

The existing parent app can use its current backend-address, demo-key and `p_demo` settings. Register the phone before starting a call. With `DEMO_KEY` exported in another terminal:

```bash
curl --fail -H "X-Demo-Key: $DEMO_KEY" -H 'Content-Type: application/json' \
  -d '{"person_id":"p_demo"}' http://127.0.0.1:8000/v1/calls
```

One process/worker only. The reused service's devices, connection state and family messages remain in memory. The shared SQLite schema is not wired into it. `CALL_RUNNER=auto` selects Agent 3's analysis package when installed, otherwise the fixed script. That fallback asks the three questions and exercises capture, but performs no measuring and finishes `incomplete`. This change does not implement or certify the revised client-observation protocol in issue #10; the inherited task-media endpoints remain compatibility/demo code from PR #9.

## iOS playback handoff

**Voice works with the existing PR #9 PCM player. Video needs a client change by its owner.** The new field is a provisional extension for Agent 3 to publish; it does not replace the shared contract.

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

## Fallback and privacy

Generated speech is PCM16 little-endian, 16 kHz, mono. Prepared audio and MP4s live in ignored `.cache/speech/` and `.cache/avatar/`, written atomically with file mode 0600. Cached voice/video continue working with provider keys removed. An uncached line without an ElevenLabs key is an explicit caption-only demo: `/answer` reports `voiced: false`; no answer transcript is invented. A provider error is reported as a failure, without its response body or credentials.

Ordinary answer recordings are sent temporarily to ElevenLabs and are not written by the speech gateway to disk. The transcription is returned to the session. No person recordings or generated assets are committed. Preparing videos sends only synthetic avatar speech to Simli. An avatar video is not a demonstration of the smile/arms task; it only speaks the instruction. Task-demonstration visuals remain unimplemented.

## Verification and limits

Provider tests use mock HTTP responses and synthetic media containers. A real Uvicorn/SSE call test exercises voice download, prepared MP4 download/ranges, three answer uploads and returned transcripts, completion, expired assets and authentication. The synthetic MP4 is a transport fixture, not a playable video or a lip-sync test. Actual generation latency, voice quality, static-video account access and iPhone playback have not been verified in this worktree because provider credentials are not configured.

The existing Node live-avatar greeting experiment remains available, but this call path uses prepared clips. Simli's continuous-stream `speaking`/`silent` events have no per-line playback identity. Prepared media lets the client acknowledge its own player ending before the next microphone/camera step.

Provider references checked 13 Nov 2026: [ElevenLabs speech](https://elevenlabs.io/docs/api-reference/text-to-speech/convert), [ElevenLabs transcription](https://elevenlabs.io/docs/api-reference/speech-to-text/convert), [Simli static video](https://docs.simli.com/api-reference/generate-static-video), [Simli OpenAPI with MP4 response fields](https://api.simli.ai/openapi.yaml), [Simli streaming protocol](https://docs.simli.com/api-reference/simli-webrtc).
