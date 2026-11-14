-- Additive, forward-only migration. Disable recording by clearing CLIP_STORAGE_DIR.
-- Keep this schema and its metadata; an older migration runner rejects a newer database.
CREATE TABLE attempt_clips (
    clip_id TEXT PRIMARY KEY,
    call_id TEXT NOT NULL REFERENCES calls(call_id),
    attempt_id TEXT NOT NULL CHECK (length(attempt_id) > 0),
    command_id TEXT NOT NULL REFERENCES client_commands(command_id),
    task TEXT NOT NULL CHECK (length(task) > 0),
    session_label TEXT NOT NULL CHECK (session_label IN ('live', 'normal', 'acted')),
    capture_status TEXT NOT NULL CHECK (capture_status IN ('finished', 'interrupted')),
    state TEXT NOT NULL CHECK (state IN ('queued', 'ready', 'failed', 'expired')),
    file_name TEXT NOT NULL UNIQUE CHECK (file_name = clip_id || '.mp4'),
    duration_ms INTEGER NOT NULL CHECK (duration_ms > 0),
    frame_count INTEGER NOT NULL CHECK (frame_count > 0),
    byte_count INTEGER CHECK (byte_count > 0),
    recorded_at_ms INTEGER NOT NULL CHECK (recorded_at_ms >= 0),
    created_at_ms INTEGER NOT NULL CHECK (created_at_ms >= recorded_at_ms),
    encoded_at_ms INTEGER CHECK (encoded_at_ms >= created_at_ms),
    expires_at_ms INTEGER NOT NULL CHECK (expires_at_ms > created_at_ms),
    error_code TEXT,
    UNIQUE (call_id, attempt_id),
    CHECK (command_id = call_id || '.' || attempt_id),
    CHECK (state != 'ready' OR (byte_count IS NOT NULL AND encoded_at_ms IS NOT NULL AND error_code IS NULL))
) STRICT;

CREATE INDEX clips_call_attempt ON attempt_clips(call_id, recorded_at_ms);
CREATE INDEX clips_expiry ON attempt_clips(expires_at_ms, state);

CREATE TRIGGER clip_capture_identity BEFORE INSERT ON attempt_clips
WHEN NOT EXISTS (
    SELECT 1 FROM client_commands cmd
    JOIN calls c ON c.call_id = cmd.call_id
    JOIN call_events e ON e.event_id = cmd.event_id
    WHERE cmd.command_id = NEW.command_id AND cmd.call_id = NEW.call_id
      AND cmd.kind = 'capture' AND c.label = NEW.session_label
      AND json_extract(e.payload_json, '$.purpose.kind') = 'task'
      AND json_extract(e.payload_json, '$.purpose.task') = NEW.task
)
BEGIN
    SELECT RAISE(ABORT, 'clip must belong to its task capture and session label');
END;
