# Retained camera-task videos

When enabled, the backend saves **one silent H.264 MP4 per task capture attempt**. The files and their
SQLite metadata survive backend restarts. Ordinary check-in questions and the full call are not recorded
by this feature. The existing incoming JPEG transport, measurements and family alert JSON remain compatible.

## Enable

Install a system ffmpeg build with the `libx264` encoder (on Ubuntu, `sudo apt install ffmpeg`). In
`backend/.env`, configure:

```dotenv
DATABASE_PATH=.cache/checkin.sqlite3
CLIP_STORAGE_DIR=~/.local/share/heartline/clips
CLIP_RETENTION_DAYS=7
```

`CLIP_STORAGE_DIR` is empty/off by default. Setting it enables retaining every camera task attempt in
operator-controlled calls, including learning, ordinary and acted sessions. Use consented demo input.
The operator's setting does not implement an end-user recording-consent or revocation screen.

Use a directory outside the repository and keep **both** the database and MP4 directory on persistent
storage. In a container, mount persistent volumes; its disposable filesystem does not survive replacement.
Do not serve the directory as public static files. Run one backend process, as with the existing shared
database recovery and rotating logs. Enabling recording without SQLite or ffmpeg/libx264 fails startup
with a configuration error. No Python package or provider key is needed for encoding.

Clearing `CLIP_STORAGE_DIR` stops new recording and disables MP4 downloads. It does not delete old files.
The expiry sweep runs while the archive is enabled; after enabling again it removes expired files at startup.

## Creation and identity

The existing capture receives JPEG samples while a smile or arm task runs. On finish, interruption,
capture failure or hang-up, its bounded in-memory frames are submitted to an encoder queue. One worker
encodes off the asyncio event loop. Frames go to ffmpeg on stdin; no source JPEG files are written.
The worker publishes a completed MP4 by atomic rename. New files are owner-only.
The separate development option `DEBUG_FRAMES_DIR`, if enabled, still writes its debug JPEGs independently.

`attempt_clips` (migration `0002_attempt_clips.sql`) stores the generated clip ID, call ID, wire attempt ID,
capture-command ID, task, session label, capture completion state, duration, original frame count, file
size and expiry. Foreign keys and a trigger enforce the call, task capture and session-label links.
The stored command ID is `<call_id>.<attempt_id>`, which can also join the analysis attempt when present.
The archive does not require the analysis recorder to be enabled.
`recorded_at` is the server's capture-command creation time; source-frame times are relative to the task.

`capture_status: finished` means the client finished sending that attempt. `interrupted` means the stream
closed another way. Neither describes measurement quality. Only Agent 3's task results and frozen baseline
membership decide whether an earlier recording can illustrate a person's usual. Labels remain
`live` / `normal` / `acted`; retaining an acted video does not make it a learning input.

Retries have separate files and never overwrite one another. A duplicate accepted finish does not create
another recording. Attempts with no received camera frames have no video record. Microphone-only tasks
are outside this feature.

## Playback timing and bounds

Videos use five frames per second. The encoder uses the samples' `captured_ms` values: a missing-frame gap
holds the previous image instead of speeding through the remaining frames. The first received image fills
any opening gap. The last image stays until the capture's end, rounded up to a 200 ms boundary, and receives
at least one frame period. Even dimensions are obtained by padding, preserving the image and orientation.
This is a playback copy; measurement still uses the original incoming samples.

Existing capture limits retain at most 100 source frames per attempt. A video is bounded to 20.2 seconds,
with at most four queued encoding jobs and one running encoder. Queue overflow records `encoder_busy`.
An encoder has a 20-second timeout. Failure records an error code without stopping the call, delaying an
alert or publishing another notification. Graceful shutdown drains the bounded queue. A killed process
marks previously queued jobs `failed / process_stopped` on its next startup, rather than claiming that
an unfinished file is playable. Partial files are removed during startup.

## Retrieval

These additive backend routes need the same `X-Demo-Key` header as the existing call API. They are proposed
for canonical contract publication in [Agent 3 review issue #48](https://github.com/BruhWherIsMyCode/unfair-weekend/issues/48).
The current single-demo shared key does not provide separate-family account authorization.

- `GET /v1/calls/{call_id}/clips` returns `{"call_id": "...", "clips": [...]}` in recording order.
  A known call without recordings has an empty list. An unknown call returns 404.
- Each entry has `clip_id`, `call_id`, `attempt_id`, `task`, `session_label`, `capture_status`, `state`,
  `recorded_at`, `created_at`, `expires_at`, `duration_ms`, `frame_count`, `byte_count`, `error_code` and
  `video_url`. Filesystem paths are not returned. `video_url` is non-null only for a ready, unexpired record.
- `GET /v1/clips/{clip_id}/video` returns `video/mp4`, including HTTP byte ranges for seeking. It returns
  202 with `Retry-After: 1` while queued, 409 after encoding failure, 410 after expiry and 404 for missing
  files/IDs or disabled recording. Credentials stay in the header. Responses use `Cache-Control: no-store`.

For example, after a call:

```bash
curl -H "X-Demo-Key: $DEMO_KEY" \
  "http://127.0.0.1:8000/v1/calls/$CALL_ID/clips"
```

The existing `/v1/clips/{id}` JSON alert shelf still expires after 30 minutes and contains available attempts
as before. Its frame sequence and the new per-attempt MP4s are separate outputs. The iPhone continues its
existing frame playback and local saving until Agent 1 adopts MP4 retrieval. No family-app update or
earlier-versus-today UI is included here.

## Retention and verification

The default enabled retention is seven days from video submission. An expired video becomes inaccessible
immediately on retrieval. A sweep at startup and every 60 seconds removes expired files and marks their
metadata expired. Metadata remains for call history. A disabled/stopped backend performs no deletion until
enabled again. Copies already downloaded or exported by a family phone are outside server retention.

The additive migration preserves existing records. To roll back recording behavior, keep this application
version and clear `CLIP_STORAGE_DIR`. The migration runner in an older binary rejects a newer database;
rolling back that binary needs a matching database backup or an explicit forward compatibility change.

Automated checks use drawn colour frames, real ffmpeg/ffprobe and local HTTP calls. They verify encoding,
per-attempt links and labels, retries, interrupted captures, ranges/authentication, app restart, expiry,
failure isolation and the old JPEG response. Provider and physical-phone trials remain separate.

```bash
uv run --locked --extra dev pytest tests/test_attempt_clips.py tests/test_alert_clip.py -q
```
