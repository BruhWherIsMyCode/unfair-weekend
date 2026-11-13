# PC link protocol, version 1

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
 "settings": {"media_kind": "video", "duration_ms": 12000, "prepare_ms": 2000, "stop_on_silence_ms": null,
              "format": "jpeg", "sample_period_ms": 200},
 "cues": [{"at_ms": 5000, "line_id": "swap_hands"}],
 "phases": [["settle", 0, 1000, null], ["hold_left", 1000, 5000, "left"], ["swap", 5000, 7000, null],
            ["settle", 7000, 8000, null], ["hold_right", 8000, 12000, "right"]]}
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
