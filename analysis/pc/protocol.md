# PC link protocol, version 2

**Version 2 (v8) adds speech** (see "Speech messages (version 2)" at the end). A version 1 client (no microphone,
`speech: false`, `protocol: 1` in `hello`) still works unchanged; the server answers `hello_ok` with the client's own
version. The server accepts protocol 1 and 2 and refuses anything else with `error` `protocol`.

The client program (camera + measuring) and the server program (all call logic) talk over one WebSocket
(`ws://host:port`). Text frames only, one JSON object per message. The server accepts ONE client at a time: a second
connection gets `error` with code `busy` and is closed. Later a phone can take the client's place; the server only
ever sees these messages.

Every message has:

| field | meaning |
|---|---|
| `type` | the message name (below) |
| `call_id` | the call id, or `null` before a call starts. The server chooses it; the client copies the id of the last server message that carried one |
| `seq` | the sender's own counter, 1, 2, 3, ... (informational) |

The server, on an unknown `type`, a missing field or a wrong value, logs it and answers
`{"type": "error", "code": "invalid_message", "text": "..."}`. It never crashes and never closes the link for it.
The client (it has no `error` message to send) only logs an invalid server message and ignores it.

Times: `captured_ms` is milliseconds since the capture started (the client's own clock). Sides are HER left and right.

## Client -> server

`hello` (first message, once)
```json
{"type": "hello", "call_id": null, "seq": 1, "protocol": 1, "client": "pc", "person_id": "feodor",
 "capabilities": {"camera": true, "microphone": false, "measures": ["smile", "arms"], "speech": false}}
```
`start_call` (no fields; starts one call; refused with `error` `call_in_progress` while one runs)
```json
{"type": "start_call", "call_id": null, "seq": 2}
```
`status` (every 1 s while connected; the server turns changes into camera / microphone events)
```json
{"type": "status", "call_id": null, "seq": 3, "camera": "available", "camera_reason": null,
 "microphone": "unavailable", "text": "going fine"}
```
`camera` and `microphone` are `available` or `unavailable`; `camera_reason` is `null`, `camera_blocked`, `camera_lost`
(or another short word, treated as unknown); `text` is what the client shows.

`capture_started` / `capture_ended` / `cancel_ack`
```json
{"type": "capture_started", "call_id": "call_20261113_101500_1", "seq": 9, "attempt_id": "A1"}
```
`capture_ended` is sent at the end of the capture window; `cancel_ack` after a `cancel_capture`.

`reading` (one per measured frame, about every 200 ms of the capture)
```json
{"type": "reading", "call_id": "call_20261113_101500_1", "seq": 10, "attempt_id": "A1", "sequence": 0,
 "captured_ms": 0, "task": "smile",
 "data": {"usable": true, "reason": null, "timestamp_ms": 0, "unit": 100.0, "below_left": 1.0, "below_right": 1.0,
          "corner_height": 0.0, "corner_height_3d": null, "smile_strength": 0.1, "smile_score": null,
          "yaw_deg": null, "pitch_deg": null, "roll_deg": null}}
```
For `arms`, `data` is `{"usable", "reason", "face_ok", "wrong_side", "timestamp_ms", "unit", "height",
"palm_width_ratio"}`. `data: null` means the frame could not be measured at all.

`said` (a `say` line finished playing)
```json
{"type": "said", "call_id": "call_...", "seq": 11, "line_id": "greet"}
```
`answer` (her answer to a `listen`; `transcript` is `null` when nothing was heard / typed)
```json
{"type": "answer", "call_id": "call_...", "seq": 12, "question_id": "q_feeling", "transcript": "I feel fine."}
```
`hang_up` (she or the client ends the call)
```json
{"type": "hang_up", "call_id": "call_...", "seq": 13}
```

## Server -> client

`hello_ok`
```json
{"type": "hello_ok", "call_id": null, "seq": 1, "protocol": 1, "server_version": "pc-1"}
```
`say` (show / speak this line; send `said` when it has been played)
```json
{"type": "say", "call_id": "call_...", "seq": 2, "line_id": "greet", "text": "Hello, Feodor. It's time for our short check-in."}
```
`listen` (collect her answer for at most `timeout_s`, then send `answer`)
```json
{"type": "listen", "call_id": "call_...", "seq": 3, "question_id": "q_feeling", "timeout_s": 8.0}
```
`start_capture`
```json
{"type": "start_capture", "call_id": "call_...", "seq": 4, "attempt_id": "A2", "task": "arms",
 "settings": {"media_kind": "video", "duration_ms": 15000, "prepare_ms": 2000, "stop_on_silence_ms": null,
              "format": "jpeg", "sample_period_ms": 200},
 "cues": [{"at_ms": 6500, "line_id": "swap_hands"}],
 "phases": [["settle", 0, 1000, null], ["hold_left", 1000, 6500, "left"], ["swap", 6500, 8500, null],
            ["hold_right", 8500, 15000, "right"]]}
```
`phases` is present for `arms` only: `[name, start_ms, end_ms, expected side or null]`; the client works out the
expected hand of each frame from `captured_ms`. The client measures locally and sends `reading` messages; cue lines
arrive as ordinary `say` messages.

`cancel_capture`
```json
{"type": "cancel_capture", "call_id": "call_...", "seq": 5, "attempt_id": "A2", "reason": "task_interrupted"}
```
`progress` (optional, neutral hint)
```json
{"type": "progress", "call_id": "call_...", "seq": 6, "text": "Please look at the camera."}
```
`call_result` (once, when the call is over)
```json
{"type": "call_result", "call_id": "call_...", "seq": 7, "summary": {"status": "as_usual", "...": "..."},
 "family_text": "Feodor's check-in today looked as usual."}
```
`error`
```json
{"type": "error", "call_id": null, "seq": 8, "code": "busy", "text": "another client is connected"}
```
Codes used: `busy`, `invalid_message`, `expected_hello`, `protocol`, `call_in_progress`, `no_call`.

## Speech messages (version 2)

Idea: the ElevenLabs API key lives ONLY in the server's environment (`ELEVENLABS_API_KEY`) and never travels over the
link. For each recording the client asks the server for a **single-use token** (valid 15 minutes, used once), calls
ElevenLabs speech-to-text itself with that token, and sends the server the **text and word timings, never audio**.
Claude cannot hear: the server turns the word timings into numbers (duration, longest pause, words per minute, word
error rate against "Today I am sitting by the window.") and Claude sees the numbers and the transcript.

`hello` capabilities: `microphone: true` and `speech: true` when the client has a microphone and can reach the speech
module; `measures` then also lists `"sentence"`. The server makes the sentence task required for a call only when the
client reports `speech: true` AND the server has a key (otherwise the call needs only smile and arms and the sentence
check is skipped).

Client -> server

`stt_token_request` (before transcribing one recording; at most one in flight, at most 10 per call)
```json
{"type": "stt_token_request", "call_id": "call_...", "seq": 20, "purpose": "answer", "ref": "ans_q_feeling_1"}
```
`purpose` is `answer` or `sentence`; `ref` is the client's own id, copied into the reply.

`answer` (as before) gains two optional fields
```json
{"type": "answer", "call_id": "call_...", "seq": 21, "question_id": "q_feeling", "transcript": "I feel fine.",
 "words": [{"text": "I", "start": 0.12, "end": 0.3, "logprob": -0.08}], "stt_error": null}
```
`words` are the speech-to-text words only (no spacing or sound tags); `start`, `end`, `logprob` may be `null`. When
speech-to-text failed: `transcript: null` and `stt_error` a short word (`stt_http_401`, `token_busy`,
`speech_not_configured`, ...); the call goes on.

`speech_result` (the sentence task; sent once, then `capture_ended`)
```json
{"type": "speech_result", "call_id": "call_...", "seq": 40, "attempt_id": "A3",
 "transcript": "Today I am sitting by the window.",
 "words": [{"text": "Today", "start": 0.1, "end": 0.5, "logprob": -0.1}],
 "duration_recorded_s": 4.7, "stt_error": null}
```
While it records and transcribes, the client sends one `reading` per 250 ms with `task: "sentence"` and
`data: {"phase": "recording" | "transcribing", "level": 0.31}` so the server's sample watchdogs know it is alive. The
`speech_result` is the attempt's one result sample (format `speech`).

Server -> client

`stt_token`
```json
{"type": "stt_token", "call_id": "call_...", "seq": 9, "ref": "ans_q_feeling_1", "token": "sutkn_...", "error": null}
```
Without `ELEVENLABS_API_KEY` on the server: `"token": null, "error": "speech_not_configured"`. Other errors:
`token_busy` (one already in flight), `token_limit` (10 per call), `token_failed_<http status>`.

`say` gains an optional `audio_pcm16k_b64` (ElevenLabs voice, `--tts elevenlabs`): base64 of raw PCM, 16 kHz, mono,
signed 16-bit little-endian. The client plays it and sends `said` when playback ends; without the field the line is
shown (and spoken locally with `--tts system`) as in version 1.

Order of one spoken answer: `listen` -> client records (at most `timeout_s`, stops 1.5 s after she stops talking) ->
`stt_token_request` -> `stt_token` -> client calls ElevenLabs -> `answer` with `transcript` and `words`. Nobody spoke:
`answer` with `transcript: null` and no token request at all.
