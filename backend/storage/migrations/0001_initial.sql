-- Initial storage contract. UTC epoch milliseconds, opaque string IDs, no media.
-- The migration runner owns the transaction and enables foreign keys.

CREATE TABLE families (
    family_id TEXT PRIMARY KEY,
    created_at_ms INTEGER NOT NULL CHECK (created_at_ms >= 0)
) STRICT;

CREATE TABLE persons (
    person_id TEXT PRIMARY KEY,
    family_id TEXT NOT NULL REFERENCES families(family_id),
    display_name TEXT NOT NULL CHECK (length(display_name) > 0),
    locale TEXT NOT NULL,
    created_at_ms INTEGER NOT NULL CHECK (created_at_ms >= 0),
    UNIQUE (person_id, family_id)
) STRICT;

CREATE INDEX persons_family ON persons(family_id);

CREATE TABLE devices (
    device_id TEXT PRIMARY KEY,
    family_id TEXT NOT NULL REFERENCES families(family_id),
    person_id TEXT,
    role TEXT NOT NULL CHECK (role IN ('parent', 'family')),
    registered_at_ms INTEGER NOT NULL CHECK (registered_at_ms >= 0),
    last_seen_at_ms INTEGER CHECK (last_seen_at_ms >= registered_at_ms),
    FOREIGN KEY (person_id, family_id) REFERENCES persons(person_id, family_id),
    CHECK ((role = 'parent' AND person_id IS NOT NULL)
        OR (role = 'family' AND person_id IS NULL)),
    UNIQUE (device_id, family_id),
    UNIQUE (device_id, person_id)
) STRICT;

CREATE INDEX devices_family ON devices(family_id, role);
CREATE INDEX devices_person ON devices(person_id);

CREATE TABLE calls (
    call_id TEXT PRIMARY KEY,
    person_id TEXT NOT NULL REFERENCES persons(person_id),
    parent_device_id TEXT,
    label TEXT NOT NULL CHECK (label IN ('live', 'normal', 'acted')),
    state TEXT NOT NULL CHECK (state IN ('ringing', 'connected', 'no_answer', 'ended')),
    end_reason TEXT CHECK (end_reason IN (
        'declined', 'ring_timeout', 'completed', 'hangup', 'connection_lost', 'deadline', 'shutdown'
    )),
    created_at_ms INTEGER NOT NULL CHECK (created_at_ms >= 0),
    ring_deadline_at_ms INTEGER NOT NULL CHECK (ring_deadline_at_ms > created_at_ms),
    answered_at_ms INTEGER CHECK (answered_at_ms >= created_at_ms),
    call_deadline_at_ms INTEGER CHECK (call_deadline_at_ms > answered_at_ms),
    ended_at_ms INTEGER CHECK (ended_at_ms >= coalesce(answered_at_ms, created_at_ms)),
    revision INTEGER NOT NULL DEFAULT 0 CHECK (revision >= 0),
    FOREIGN KEY (parent_device_id, person_id) REFERENCES devices(device_id, person_id),
    CHECK ((answered_at_ms IS NULL) = (call_deadline_at_ms IS NULL)),
    CHECK ((state IN ('ringing', 'connected') AND ended_at_ms IS NULL AND end_reason IS NULL)
        OR (state IN ('no_answer', 'ended') AND ended_at_ms IS NOT NULL AND end_reason IS NOT NULL)),
    CHECK (state != 'ringing' OR answered_at_ms IS NULL),
    CHECK (state != 'connected' OR answered_at_ms IS NOT NULL),
    CHECK ((state = 'no_answer') = (end_reason IN ('declined', 'ring_timeout'))),
    CHECK (state != 'no_answer' OR answered_at_ms IS NULL),
    CHECK (end_reason NOT IN ('completed', 'deadline') OR answered_at_ms IS NOT NULL)
) STRICT;

CREATE UNIQUE INDEX calls_one_active_person ON calls(person_id) WHERE ended_at_ms IS NULL;
CREATE INDEX calls_person_history ON calls(person_id, created_at_ms DESC);

CREATE TABLE policy_definitions (
    policy_version TEXT PRIMARY KEY,
    definition_json TEXT NOT NULL CHECK (json_valid(definition_json) AND json_type(definition_json) = 'object'),
    created_at_ms INTEGER NOT NULL CHECK (created_at_ms >= 0)
) STRICT;

CREATE TABLE task_definitions (
    definition_id TEXT PRIMARY KEY,
    task TEXT NOT NULL CHECK (task IN ('smile', 'arms', 'sentence')),
    version TEXT NOT NULL,
    metric_id TEXT NOT NULL,
    unit TEXT NOT NULL,
    instruction_line_id TEXT NOT NULL,
    repeat_line_id TEXT NOT NULL,
    required_resource TEXT NOT NULL CHECK (required_resource IN ('camera', 'microphone')),
    window_ms INTEGER NOT NULL CHECK (window_ms > 0),
    definition_json TEXT NOT NULL CHECK (json_valid(definition_json) AND json_type(definition_json) = 'object'),
    created_at_ms INTEGER NOT NULL CHECK (created_at_ms >= 0),
    UNIQUE (task, version),
    UNIQUE (definition_id, task)
) STRICT;

CREATE TABLE call_sessions (
    call_id TEXT PRIMARY KEY REFERENCES calls(call_id),
    phase TEXT NOT NULL CHECK (phase IN ('preparing', 'running', 'finishing', 'finished')),
    script_version TEXT NOT NULL,
    policy_version TEXT NOT NULL REFERENCES policy_definitions(policy_version),
    context_revision INTEGER NOT NULL DEFAULT 0 CHECK (context_revision >= 0),
    snapshot_json TEXT NOT NULL CHECK (json_valid(snapshot_json) AND json_type(snapshot_json) = 'object'),
    started_at_ms INTEGER NOT NULL CHECK (started_at_ms >= 0),
    finished_at_ms INTEGER CHECK (finished_at_ms >= started_at_ms),
    CHECK ((phase = 'finished') = (finished_at_ms IS NOT NULL))
) STRICT;

CREATE TABLE baseline_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    person_id TEXT NOT NULL REFERENCES persons(person_id),
    definition_id TEXT NOT NULL REFERENCES task_definitions(definition_id),
    sessions INTEGER NOT NULL CHECK (sessions >= 0),
    needed INTEGER NOT NULL CHECK (needed >= 1),
    ready INTEGER NOT NULL CHECK (ready IN (0, 1)),
    usual_low REAL CHECK (usual_low IS NULL OR abs(usual_low) <= 1.7976931348623157e308),
    usual_high REAL CHECK (usual_high IS NULL OR abs(usual_high) <= 1.7976931348623157e308),
    created_at_ms INTEGER NOT NULL CHECK (created_at_ms >= 0),
    CHECK ((ready = 0 AND usual_low IS NULL AND usual_high IS NULL)
        OR (ready = 1 AND sessions >= needed AND usual_low IS NOT NULL
            AND usual_high IS NOT NULL AND usual_low <= usual_high)),
    UNIQUE (snapshot_id, definition_id),
    UNIQUE (snapshot_id, person_id)
) STRICT;

CREATE INDEX baseline_person_definition ON baseline_snapshots(person_id, definition_id, created_at_ms DESC);

CREATE TABLE session_baselines (
    call_id TEXT NOT NULL REFERENCES call_sessions(call_id),
    task TEXT NOT NULL,
    definition_id TEXT NOT NULL,
    snapshot_id TEXT NOT NULL,
    PRIMARY KEY (call_id, task),
    FOREIGN KEY (definition_id, task) REFERENCES task_definitions(definition_id, task),
    FOREIGN KEY (snapshot_id, definition_id) REFERENCES baseline_snapshots(snapshot_id, definition_id),
    UNIQUE (call_id, task, definition_id, snapshot_id)
) STRICT;

CREATE TABLE model_turns (
    turn_id TEXT PRIMARY KEY,
    call_id TEXT NOT NULL REFERENCES call_sessions(call_id),
    sequence INTEGER NOT NULL CHECK (sequence >= 0),
    context_revision INTEGER NOT NULL CHECK (context_revision >= 0),
    model_id TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('pending', 'completed', 'stale', 'failed', 'cancelled')),
    request_json TEXT NOT NULL CHECK (json_valid(request_json) AND json_type(request_json) = 'object'),
    response_json TEXT CHECK (response_json IS NULL OR (json_valid(response_json) AND json_type(response_json) = 'object')),
    error_code TEXT,
    created_at_ms INTEGER NOT NULL CHECK (created_at_ms >= 0),
    ended_at_ms INTEGER CHECK (ended_at_ms >= created_at_ms),
    CHECK ((state = 'pending') = (ended_at_ms IS NULL)),
    UNIQUE (call_id, sequence),
    UNIQUE (turn_id, call_id)
) STRICT;

CREATE UNIQUE INDEX model_one_pending_turn ON model_turns(call_id) WHERE state = 'pending';

CREATE TABLE tool_calls (
    tool_call_id TEXT PRIMARY KEY,
    call_id TEXT NOT NULL REFERENCES call_sessions(call_id),
    turn_id TEXT NOT NULL,
    tool_name TEXT NOT NULL CHECK (tool_name IN (
        'say', 'ask', 'record_answer', 'run_task', 'recover_resource', 'finish_call'
    )),
    arguments_json TEXT NOT NULL CHECK (json_valid(arguments_json) AND json_type(arguments_json) = 'object'),
    state TEXT NOT NULL CHECK (state IN ('pending', 'running', 'completed', 'interrupted', 'failed', 'cancelled', 'rejected')),
    result_json TEXT CHECK (result_json IS NULL OR (json_valid(result_json) AND json_type(result_json) = 'object')),
    created_at_ms INTEGER NOT NULL CHECK (created_at_ms >= 0),
    ended_at_ms INTEGER CHECK (ended_at_ms >= created_at_ms),
    FOREIGN KEY (turn_id, call_id) REFERENCES model_turns(turn_id, call_id),
    CHECK ((state IN ('pending', 'running') AND result_json IS NULL AND ended_at_ms IS NULL)
        OR (state NOT IN ('pending', 'running') AND result_json IS NOT NULL AND ended_at_ms IS NOT NULL)),
    UNIQUE (tool_call_id, call_id)
) STRICT;

CREATE UNIQUE INDEX tools_one_active ON tool_calls(call_id) WHERE state IN ('pending', 'running');

CREATE TABLE task_attempts (
    attempt_id TEXT PRIMARY KEY,
    call_id TEXT NOT NULL REFERENCES call_sessions(call_id),
    task TEXT NOT NULL,
    definition_id TEXT NOT NULL,
    snapshot_id TEXT NOT NULL,
    tool_call_id TEXT NOT NULL UNIQUE,
    ordinal INTEGER NOT NULL CHECK (ordinal BETWEEN 1 AND 3),
    state TEXT NOT NULL CHECK (state IN (
        'preparing', 'capturing', 'finalizing', 'stopping',
        'completed', 'could_not_perform', 'interrupted', 'failed', 'cancelled'
    )),
    revision INTEGER NOT NULL DEFAULT 0 CHECK (revision >= 0),
    created_at_ms INTEGER NOT NULL CHECK (created_at_ms >= 0),
    preparation_deadline_at_ms INTEGER NOT NULL CHECK (preparation_deadline_at_ms > created_at_ms),
    capture_started_at_ms INTEGER CHECK (capture_started_at_ms >= created_at_ms),
    window_deadline_at_ms INTEGER CHECK (window_deadline_at_ms > capture_started_at_ms),
    observation_deadline_at_ms INTEGER CHECK (observation_deadline_at_ms >= capture_started_at_ms),
    finalization_deadline_at_ms INTEGER CHECK (finalization_deadline_at_ms >= created_at_ms),
    framing_unusable_since_at_ms INTEGER CHECK (framing_unusable_since_at_ms >= capture_started_at_ms),
    latest_observation_event_id TEXT,
    ended_at_ms INTEGER CHECK (ended_at_ms >= created_at_ms),
    terminal_result_attempt_id TEXT,
    FOREIGN KEY (call_id, task, definition_id, snapshot_id)
        REFERENCES session_baselines(call_id, task, definition_id, snapshot_id),
    FOREIGN KEY (tool_call_id, call_id) REFERENCES tool_calls(tool_call_id, call_id),
    FOREIGN KEY (latest_observation_event_id, attempt_id)
        REFERENCES task_observations(event_id, attempt_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (terminal_result_attempt_id) REFERENCES task_results(attempt_id) DEFERRABLE INITIALLY DEFERRED,
    CHECK ((state IN ('preparing', 'capturing', 'finalizing', 'stopping')
            AND ended_at_ms IS NULL AND terminal_result_attempt_id IS NULL)
        OR (state NOT IN ('preparing', 'capturing', 'finalizing', 'stopping')
            AND ended_at_ms IS NOT NULL AND terminal_result_attempt_id IS NOT NULL
            AND terminal_result_attempt_id = attempt_id)),
    CHECK (state NOT IN ('capturing', 'finalizing') OR capture_started_at_ms IS NOT NULL),
    CHECK (state != 'capturing' OR (window_deadline_at_ms IS NOT NULL AND observation_deadline_at_ms IS NOT NULL)),
    CHECK (state != 'finalizing' OR finalization_deadline_at_ms IS NOT NULL),
    UNIQUE (call_id, task, ordinal),
    UNIQUE (attempt_id, call_id),
    UNIQUE (attempt_id, call_id, definition_id)
) STRICT;

CREATE UNIQUE INDEX attempts_one_active ON task_attempts(call_id) WHERE ended_at_ms IS NULL;
CREATE INDEX attempts_call_task ON task_attempts(call_id, task, ordinal);

CREATE TABLE call_events (
    event_id TEXT PRIMARY KEY,
    call_id TEXT NOT NULL REFERENCES calls(call_id),
    attempt_id TEXT,
    device_id TEXT REFERENCES devices(device_id),
    position INTEGER NOT NULL CHECK (position >= 0),
    source TEXT NOT NULL CHECK (source IN ('client', 'server')),
    event_type TEXT NOT NULL CHECK (length(event_type) > 0),
    received_at_ms INTEGER NOT NULL CHECK (received_at_ms >= 0),
    payload_json TEXT NOT NULL CHECK (json_valid(payload_json) AND json_type(payload_json) = 'object'),
    FOREIGN KEY (attempt_id, call_id) REFERENCES task_attempts(attempt_id, call_id),
    UNIQUE (call_id, position),
    UNIQUE (event_id, call_id),
    UNIQUE (event_id, call_id, attempt_id)
) STRICT;

CREATE INDEX events_attempt ON call_events(attempt_id, position);

CREATE TABLE client_commands (
    command_id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL UNIQUE,
    call_id TEXT NOT NULL REFERENCES calls(call_id),
    attempt_id TEXT,
    device_id TEXT NOT NULL REFERENCES devices(device_id),
    kind TEXT NOT NULL CHECK (length(kind) > 0),
    state TEXT NOT NULL CHECK (state IN ('queued', 'sent', 'started', 'completed', 'failed', 'cancelled')),
    created_at_ms INTEGER NOT NULL CHECK (created_at_ms >= 0),
    deadline_at_ms INTEGER NOT NULL CHECK (deadline_at_ms > created_at_ms),
    sent_at_ms INTEGER CHECK (sent_at_ms >= created_at_ms),
    acknowledged_at_ms INTEGER CHECK (acknowledged_at_ms >= created_at_ms),
    ended_at_ms INTEGER CHECK (ended_at_ms >= created_at_ms),
    result_event_id TEXT,
    FOREIGN KEY (event_id, call_id) REFERENCES call_events(event_id, call_id),
    FOREIGN KEY (event_id, call_id, attempt_id) REFERENCES call_events(event_id, call_id, attempt_id),
    FOREIGN KEY (attempt_id, call_id) REFERENCES task_attempts(attempt_id, call_id),
    FOREIGN KEY (result_event_id, call_id) REFERENCES call_events(event_id, call_id),
    CHECK ((state IN ('queued', 'sent', 'started')) = (ended_at_ms IS NULL)),
    CHECK (state NOT IN ('sent', 'started', 'completed') OR sent_at_ms IS NOT NULL),
    CHECK (state != 'started' OR acknowledged_at_ms IS NOT NULL)
) STRICT;

CREATE INDEX commands_device_pending ON client_commands(device_id, created_at_ms)
    WHERE state IN ('queued', 'sent', 'started');
CREATE INDEX commands_attempt ON client_commands(attempt_id, kind);

CREATE TABLE task_observations (
    event_id TEXT PRIMARY KEY,
    call_id TEXT NOT NULL,
    attempt_id TEXT NOT NULL,
    sequence INTEGER NOT NULL CHECK (sequence >= 0),
    elapsed_ms INTEGER NOT NULL CHECK (elapsed_ms >= 0),
    phase TEXT NOT NULL CHECK (phase IN ('capturing', 'finalizing')),
    quality_status TEXT NOT NULL CHECK (quality_status IN ('pending', 'usable', 'unusable')),
    quality_reasons_json TEXT NOT NULL CHECK (json_valid(quality_reasons_json) AND json_type(quality_reasons_json) = 'array'),
    metrics_json TEXT NOT NULL CHECK (json_valid(metrics_json) AND json_type(metrics_json) = 'array'),
    signal TEXT NOT NULL CHECK (signal IN ('none', 'framing_unusable', 'task_unperformable')),
    FOREIGN KEY (event_id, call_id, attempt_id) REFERENCES call_events(event_id, call_id, attempt_id),
    FOREIGN KEY (attempt_id, call_id) REFERENCES task_attempts(attempt_id, call_id),
    CHECK (quality_status != 'unusable' OR json_array_length(quality_reasons_json) > 0),
    CHECK (signal != 'framing_unusable' OR quality_status = 'unusable'),
    UNIQUE (attempt_id, sequence),
    UNIQUE (event_id, attempt_id)
) STRICT;

CREATE TABLE task_results (
    attempt_id TEXT PRIMARY KEY,
    call_id TEXT NOT NULL,
    definition_id TEXT NOT NULL,
    outcome TEXT NOT NULL CHECK (outcome IN ('completed', 'could_not_perform', 'interrupted', 'failed', 'cancelled')),
    quality_status TEXT NOT NULL CHECK (quality_status IN ('usable', 'unusable')),
    quality_reasons_json TEXT NOT NULL CHECK (json_valid(quality_reasons_json) AND json_type(quality_reasons_json) = 'array'),
    performance TEXT NOT NULL CHECK (performance IN ('observed', 'not_observed', 'uncertain')),
    value REAL CHECK (value IS NULL OR abs(value) <= 1.7976931348623157e308),
    usual_low REAL CHECK (usual_low IS NULL OR abs(usual_low) <= 1.7976931348623157e308),
    usual_high REAL CHECK (usual_high IS NULL OR abs(usual_high) <= 1.7976931348623157e308),
    outside INTEGER CHECK (outside IN (0, 1)),
    side TEXT CHECK (side IN ('left', 'right')),
    interruption_code TEXT,
    source_event_id TEXT,
    recoverable INTEGER CHECK (recoverable IN (0, 1)),
    finalized_at_ms INTEGER NOT NULL CHECK (finalized_at_ms >= 0),
    FOREIGN KEY (attempt_id, call_id, definition_id) REFERENCES task_attempts(attempt_id, call_id, definition_id),
    FOREIGN KEY (source_event_id, call_id) REFERENCES call_events(event_id, call_id),
    CHECK (quality_status != 'unusable' OR json_array_length(quality_reasons_json) > 0),
    CHECK ((outcome = 'completed' AND quality_status = 'usable' AND performance = 'observed' AND value IS NOT NULL)
        OR ((outcome != 'completed' OR quality_status != 'usable' OR performance != 'observed') AND value IS NULL)),
    CHECK ((value IS NULL AND usual_low IS NULL AND usual_high IS NULL AND outside IS NULL AND side IS NULL)
        OR (value IS NOT NULL AND ((usual_low IS NULL AND usual_high IS NULL AND outside IS NULL)
            OR (usual_low IS NOT NULL AND usual_high IS NOT NULL AND usual_low <= usual_high
                AND outside IS NOT NULL AND outside = (value < usual_low OR value > usual_high))))),
    CHECK ((outcome = 'interrupted' AND interruption_code IS NOT NULL AND recoverable IS NOT NULL
            AND value IS NULL AND quality_status = 'unusable')
        OR (outcome != 'interrupted' AND interruption_code IS NULL AND source_event_id IS NULL AND recoverable IS NULL)),
    UNIQUE (attempt_id, call_id, definition_id)
) STRICT;

CREATE INDEX results_definition ON task_results(definition_id, call_id);

CREATE TABLE answers (
    answer_id TEXT PRIMARY KEY,
    call_id TEXT NOT NULL REFERENCES call_sessions(call_id),
    tool_call_id TEXT NOT NULL UNIQUE,
    question_id TEXT NOT NULL CHECK (question_id IN ('medicine', 'feeling', 'unusual')),
    ordinal INTEGER NOT NULL CHECK (ordinal >= 1),
    status TEXT NOT NULL CHECK (status IN ('heard', 'no_speech', 'unavailable', 'interrupted')),
    transcript TEXT CHECK (length(transcript) BETWEEN 1 AND 2000),
    normalized_value TEXT,
    selected INTEGER NOT NULL DEFAULT 0 CHECK (selected IN (0, 1)),
    created_at_ms INTEGER NOT NULL CHECK (created_at_ms >= 0),
    FOREIGN KEY (tool_call_id, call_id) REFERENCES tool_calls(tool_call_id, call_id),
    CHECK ((status = 'heard') = (transcript IS NOT NULL)),
    CHECK (question_id != 'medicine' OR normalized_value IS NULL OR normalized_value IN ('yes', 'no', 'unclear')),
    UNIQUE (call_id, question_id, ordinal)
) STRICT;

CREATE UNIQUE INDEX answers_one_selected ON answers(call_id, question_id) WHERE selected = 1;

CREATE TABLE session_summaries (
    call_id TEXT NOT NULL REFERENCES calls(call_id),
    revision INTEGER NOT NULL CHECK (revision >= 1),
    status TEXT NOT NULL CHECK (status IN ('as_usual', 'change_noticed', 'learning', 'incomplete', 'no_answer')),
    phase TEXT NOT NULL CHECK (phase IN ('mid_call', 'final')),
    summary_json TEXT NOT NULL CHECK (json_valid(summary_json) AND json_type(summary_json) = 'object'),
    created_at_ms INTEGER NOT NULL CHECK (created_at_ms >= 0),
    PRIMARY KEY (call_id, revision),
    UNIQUE (call_id, revision, status)
) STRICT;

CREATE UNIQUE INDEX summaries_one_final ON session_summaries(call_id) WHERE phase = 'final';

CREATE TABLE baseline_members (
    snapshot_id TEXT NOT NULL,
    source_attempt_id TEXT NOT NULL,
    source_call_id TEXT NOT NULL,
    definition_id TEXT NOT NULL,
    PRIMARY KEY (snapshot_id, source_call_id),
    FOREIGN KEY (snapshot_id, definition_id) REFERENCES baseline_snapshots(snapshot_id, definition_id),
    FOREIGN KEY (source_attempt_id, source_call_id, definition_id) REFERENCES task_results(attempt_id, call_id, definition_id)
) STRICT;

CREATE INDEX baseline_members_source ON baseline_members(source_attempt_id);

CREATE TABLE family_messages (
    message_id TEXT PRIMARY KEY,
    call_id TEXT NOT NULL UNIQUE REFERENCES calls(call_id),
    family_id TEXT NOT NULL REFERENCES families(family_id),
    summary_revision INTEGER NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('as_usual', 'change_noticed', 'learning', 'incomplete', 'no_answer')),
    template_id TEXT NOT NULL,
    text TEXT NOT NULL CHECK (length(text) > 0),
    created_at_ms INTEGER NOT NULL CHECK (created_at_ms >= 0),
    updated_at_ms INTEGER NOT NULL CHECK (updated_at_ms >= created_at_ms),
    FOREIGN KEY (call_id, summary_revision, kind) REFERENCES session_summaries(call_id, revision, status),
    UNIQUE (message_id, call_id)
) STRICT;

CREATE INDEX messages_family_feed ON family_messages(family_id, created_at_ms DESC);

CREATE TABLE notification_outbox (
    notification_id TEXT PRIMARY KEY,
    dedupe_key TEXT NOT NULL UNIQUE,
    purpose TEXT NOT NULL CHECK (purpose IN ('incoming_call', 'family_message')),
    call_id TEXT NOT NULL REFERENCES calls(call_id),
    device_id TEXT NOT NULL REFERENCES devices(device_id),
    message_id TEXT,
    summary_revision INTEGER,
    payload_json TEXT NOT NULL CHECK (json_valid(payload_json) AND json_type(payload_json) = 'object'),
    state TEXT NOT NULL CHECK (state IN ('queued', 'sending', 'accepted', 'failed', 'cancelled')),
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    next_attempt_at_ms INTEGER NOT NULL CHECK (next_attempt_at_ms >= 0),
    last_error_code TEXT,
    created_at_ms INTEGER NOT NULL CHECK (created_at_ms >= 0),
    updated_at_ms INTEGER NOT NULL CHECK (updated_at_ms >= created_at_ms),
    FOREIGN KEY (message_id, call_id) REFERENCES family_messages(message_id, call_id),
    FOREIGN KEY (call_id, summary_revision) REFERENCES session_summaries(call_id, revision),
    CHECK ((purpose = 'incoming_call' AND message_id IS NULL AND summary_revision IS NULL)
        OR (purpose = 'family_message' AND message_id IS NOT NULL AND summary_revision IS NOT NULL))
) STRICT;

CREATE INDEX notifications_pending ON notification_outbox(next_attempt_at_ms) WHERE state IN ('queued', 'failed');
CREATE INDEX notifications_call ON notification_outbox(call_id, purpose);
CREATE UNIQUE INDEX notifications_one_ring ON notification_outbox(call_id, device_id) WHERE purpose = 'incoming_call';
CREATE UNIQUE INDEX notifications_one_message_revision ON notification_outbox(message_id, summary_revision, device_id)
    WHERE purpose = 'family_message';

CREATE TABLE delivery_attempts (
    notification_id TEXT NOT NULL REFERENCES notification_outbox(notification_id),
    ordinal INTEGER NOT NULL CHECK (ordinal >= 1),
    transport TEXT NOT NULL CHECK (length(transport) > 0),
    state TEXT NOT NULL CHECK (state IN ('sending', 'accepted', 'failed')),
    provider_receipt TEXT,
    error_code TEXT,
    started_at_ms INTEGER NOT NULL CHECK (started_at_ms >= 0),
    ended_at_ms INTEGER CHECK (ended_at_ms >= started_at_ms),
    PRIMARY KEY (notification_id, ordinal),
    CHECK ((state = 'sending') = (ended_at_ms IS NULL))
) STRICT;

-- Cross-table invariants supplement the per-row constraints above. Business
-- decisions and wire payload validation still belong to the owning modules.

CREATE TRIGGER sessions_require_answer BEFORE INSERT ON call_sessions
WHEN NOT EXISTS (SELECT 1 FROM calls WHERE call_id = NEW.call_id AND state = 'connected')
BEGIN
    SELECT RAISE(ABORT, 'session requires an answered active call');
END;

CREATE TRIGGER session_baseline_person BEFORE INSERT ON session_baselines
WHEN NOT EXISTS (
    SELECT 1 FROM calls c JOIN baseline_snapshots b ON b.person_id = c.person_id
    WHERE c.call_id = NEW.call_id AND b.snapshot_id = NEW.snapshot_id
)
BEGIN
    SELECT RAISE(ABORT, 'baseline belongs to another person');
END;

CREATE TRIGGER attempt_requires_running_session BEFORE INSERT ON task_attempts
WHEN NOT EXISTS (
    SELECT 1 FROM calls c JOIN call_sessions s USING (call_id)
    JOIN tool_calls t ON t.call_id = c.call_id
    WHERE c.call_id = NEW.call_id AND c.state = 'connected' AND s.phase = 'running'
        AND t.tool_call_id = NEW.tool_call_id AND t.tool_name = 'run_task' AND t.state = 'running'
)
BEGIN
    SELECT RAISE(ABORT, 'attempt requires a running session and run_task tool');
END;

CREATE TRIGGER observation_requires_active_attempt BEFORE INSERT ON task_observations
WHEN NOT EXISTS (
    SELECT 1 FROM task_attempts a JOIN call_events e ON e.attempt_id = a.attempt_id
    JOIN calls c ON c.call_id = a.call_id
    WHERE a.attempt_id = NEW.attempt_id AND a.call_id = NEW.call_id
        AND a.state IN ('capturing', 'finalizing') AND e.event_id = NEW.event_id
        AND e.event_type = 'task.observation' AND e.source = 'client' AND c.state = 'connected'
)
BEGIN
    SELECT RAISE(ABORT, 'observation requires an active matching client attempt');
END;

CREATE TRIGGER client_event_device BEFORE INSERT ON call_events
WHEN NEW.source = 'client' AND NOT EXISTS (
    SELECT 1 FROM calls c JOIN devices d ON d.person_id = c.person_id
    WHERE c.call_id = NEW.call_id AND d.device_id = NEW.device_id AND d.role = 'parent'
)
BEGIN
    SELECT RAISE(ABORT, 'client event requires the matching parent device');
END;

CREATE TRIGGER observation_order BEFORE INSERT ON task_observations
WHEN EXISTS (
    SELECT 1 FROM task_observations WHERE attempt_id = NEW.attempt_id
        AND (sequence >= NEW.sequence OR elapsed_ms > NEW.elapsed_ms)
)
BEGIN
    SELECT RAISE(ABORT, 'observation sequence or elapsed time is obsolete');
END;

CREATE TRIGGER command_matches_parent BEFORE INSERT ON client_commands
WHEN NOT EXISTS (
    SELECT 1 FROM calls c JOIN devices d ON d.person_id = c.person_id
    JOIN call_events e ON e.event_id = NEW.event_id
    WHERE c.call_id = NEW.call_id AND d.device_id = NEW.device_id AND d.role = 'parent'
        AND e.source = 'server' AND e.attempt_id IS NEW.attempt_id
)
BEGIN
    SELECT RAISE(ABORT, 'command requires a matching server event and parent device');
END;

CREATE TRIGGER result_matches_attempt BEFORE INSERT ON task_results
WHEN NOT EXISTS (
    SELECT 1 FROM task_attempts a JOIN baseline_snapshots b ON b.snapshot_id = a.snapshot_id
    WHERE a.attempt_id = NEW.attempt_id AND a.state = NEW.outcome
        AND a.ended_at_ms = NEW.finalized_at_ms
        AND (NEW.value IS NULL
            OR (b.ready = 0 AND NEW.usual_low IS NULL AND NEW.usual_high IS NULL AND NEW.outside IS NULL)
            OR (b.ready = 1 AND NEW.usual_low = b.usual_low AND NEW.usual_high = b.usual_high AND NEW.outside IS NOT NULL))
)
BEGIN
    SELECT RAISE(ABORT, 'result must match terminal attempt and frozen baseline');
END;

CREATE TRIGGER baseline_member_eligibility BEFORE INSERT ON baseline_members
WHEN NOT EXISTS (
    SELECT 1 FROM task_results r JOIN calls c ON c.call_id = r.call_id
    JOIN baseline_snapshots b ON b.snapshot_id = NEW.snapshot_id
    JOIN call_sessions s ON s.call_id = c.call_id
    JOIN session_summaries f ON f.call_id = c.call_id AND f.phase = 'final'
    WHERE r.attempt_id = NEW.source_attempt_id AND c.person_id = b.person_id
        AND c.label IN ('live', 'normal') AND c.state = 'ended' AND s.phase = 'finished'
        AND f.status IN ('as_usual', 'learning') AND r.outcome = 'completed'
        AND r.quality_status = 'usable' AND r.performance = 'observed' AND r.value IS NOT NULL
        AND (r.outside IS NULL OR r.outside = 0)
        AND NOT EXISTS (SELECT 1 FROM session_summaries WHERE call_id = c.call_id AND status = 'change_noticed')
)
BEGIN
    SELECT RAISE(ABORT, 'baseline member is not an eligible final reading');
END;

CREATE TRIGGER frozen_baseline_members BEFORE INSERT ON baseline_members
WHEN EXISTS (SELECT 1 FROM session_baselines WHERE snapshot_id = NEW.snapshot_id)
BEGIN
    SELECT RAISE(ABORT, 'baseline already frozen for a call');
END;

CREATE TRIGGER message_family_insert BEFORE INSERT ON family_messages
WHEN NOT EXISTS (
    SELECT 1 FROM calls c JOIN persons p USING (person_id)
    WHERE c.call_id = NEW.call_id AND p.family_id = NEW.family_id
)
BEGIN
    SELECT RAISE(ABORT, 'message belongs to another family');
END;

CREATE TRIGGER message_update BEFORE UPDATE ON family_messages
WHEN NEW.message_id != OLD.message_id OR NEW.call_id != OLD.call_id OR NEW.family_id != OLD.family_id
    OR NEW.created_at_ms != OLD.created_at_ms OR NEW.summary_revision <= OLD.summary_revision
    OR (OLD.kind = 'change_noticed' AND NEW.kind != 'change_noticed')
BEGIN
    SELECT RAISE(ABORT, 'message identity, revision or confirmed change cannot be replaced');
END;

CREATE TRIGGER notification_recipient BEFORE INSERT ON notification_outbox
WHEN NOT EXISTS (
    SELECT 1 FROM calls c JOIN persons p USING (person_id) JOIN devices d ON d.family_id = p.family_id
    WHERE c.call_id = NEW.call_id AND d.device_id = NEW.device_id
        AND ((NEW.purpose = 'incoming_call' AND d.role = 'parent' AND d.person_id = c.person_id)
            OR (NEW.purpose = 'family_message' AND d.role = 'family'))
)
BEGIN
    SELECT RAISE(ABORT, 'notification recipient does not match call');
END;

CREATE TRIGGER notification_identity BEFORE UPDATE ON notification_outbox
WHEN NEW.notification_id != OLD.notification_id OR NEW.dedupe_key != OLD.dedupe_key
    OR NEW.purpose != OLD.purpose OR NEW.call_id != OLD.call_id OR NEW.device_id != OLD.device_id
    OR NEW.message_id IS NOT OLD.message_id OR NEW.summary_revision IS NOT OLD.summary_revision
    OR NEW.payload_json != OLD.payload_json OR NEW.created_at_ms != OLD.created_at_ms
BEGIN
    SELECT RAISE(ABORT, 'notification identity and revisioned payload are immutable');
END;

CREATE TRIGGER command_identity BEFORE UPDATE ON client_commands
WHEN NEW.command_id != OLD.command_id OR NEW.event_id != OLD.event_id
    OR NEW.call_id != OLD.call_id OR NEW.attempt_id IS NOT OLD.attempt_id
    OR NEW.device_id != OLD.device_id OR NEW.kind != OLD.kind OR NEW.created_at_ms != OLD.created_at_ms
    OR OLD.ended_at_ms IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'command identity and terminal state are immutable');
END;

CREATE TRIGGER attempt_identity BEFORE UPDATE ON task_attempts
WHEN NEW.attempt_id != OLD.attempt_id OR NEW.call_id != OLD.call_id OR NEW.task != OLD.task
    OR NEW.definition_id != OLD.definition_id OR NEW.snapshot_id != OLD.snapshot_id
    OR NEW.tool_call_id != OLD.tool_call_id OR NEW.ordinal != OLD.ordinal
    OR NEW.created_at_ms != OLD.created_at_ms
BEGIN
    SELECT RAISE(ABORT, 'attempt identity and definition are immutable');
END;

CREATE TRIGGER call_identity BEFORE UPDATE ON calls
WHEN NEW.call_id != OLD.call_id OR NEW.person_id != OLD.person_id
    OR NEW.label != OLD.label OR NEW.created_at_ms != OLD.created_at_ms
BEGIN
    SELECT RAISE(ABORT, 'call identity and evidence label are immutable');
END;

CREATE TRIGGER events_no_update BEFORE UPDATE ON call_events
BEGIN
    SELECT RAISE(ABORT, 'event journal is append-only');
END;

CREATE TRIGGER events_no_delete BEFORE DELETE ON call_events
BEGIN
    SELECT RAISE(ABORT, 'event journal is append-only');
END;

CREATE TRIGGER observations_no_update BEFORE UPDATE ON task_observations
BEGIN
    SELECT RAISE(ABORT, 'observations are append-only');
END;

CREATE TRIGGER observations_no_delete BEFORE DELETE ON task_observations
BEGIN
    SELECT RAISE(ABORT, 'observations are append-only');
END;

CREATE TRIGGER results_no_update BEFORE UPDATE ON task_results
BEGIN
    SELECT RAISE(ABORT, 'terminal result is immutable');
END;

CREATE TRIGGER results_no_delete BEFORE DELETE ON task_results
BEGIN
    SELECT RAISE(ABORT, 'terminal result is immutable');
END;

CREATE TRIGGER terminal_attempt_no_update BEFORE UPDATE ON task_attempts
WHEN OLD.ended_at_ms IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'terminal attempt is immutable');
END;

CREATE TRIGGER terminal_call_no_update BEFORE UPDATE ON calls
WHEN OLD.ended_at_ms IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'terminal connection state is immutable');
END;

CREATE TRIGGER terminal_tool_no_update BEFORE UPDATE ON tool_calls
WHEN OLD.ended_at_ms IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'terminal tool result is immutable');
END;

CREATE TRIGGER tool_identity BEFORE UPDATE ON tool_calls
WHEN NEW.tool_call_id != OLD.tool_call_id OR NEW.call_id != OLD.call_id OR NEW.turn_id != OLD.turn_id
    OR NEW.tool_name != OLD.tool_name OR NEW.arguments_json != OLD.arguments_json
    OR NEW.created_at_ms != OLD.created_at_ms
BEGIN
    SELECT RAISE(ABORT, 'model tool identity and arguments are immutable');
END;

CREATE TRIGGER terminal_session_no_update BEFORE UPDATE ON call_sessions
WHEN OLD.finished_at_ms IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'finished conversation state is immutable');
END;

CREATE TRIGGER summary_order BEFORE INSERT ON session_summaries
WHEN EXISTS (SELECT 1 FROM session_summaries WHERE call_id = NEW.call_id
        AND (revision >= NEW.revision OR phase = 'final'))
    OR (NEW.status != 'change_noticed' AND EXISTS (
        SELECT 1 FROM session_summaries WHERE call_id = NEW.call_id AND status = 'change_noticed'
    ))
BEGIN
    SELECT RAISE(ABORT, 'summary revision, final state or confirmed change cannot be replaced');
END;

CREATE TRIGGER definitions_no_update BEFORE UPDATE ON task_definitions
BEGIN
    SELECT RAISE(ABORT, 'publish a new task definition version');
END;

CREATE TRIGGER policies_no_update BEFORE UPDATE ON policy_definitions
BEGIN
    SELECT RAISE(ABORT, 'publish a new policy version');
END;

CREATE TRIGGER baselines_no_update BEFORE UPDATE ON baseline_snapshots
BEGIN
    SELECT RAISE(ABORT, 'publish a new baseline snapshot');
END;

CREATE TRIGGER session_baselines_no_update BEFORE UPDATE ON session_baselines
BEGIN
    SELECT RAISE(ABORT, 'call baseline is frozen');
END;

CREATE TRIGGER session_baselines_no_delete BEFORE DELETE ON session_baselines
BEGIN
    SELECT RAISE(ABORT, 'call baseline is frozen');
END;

CREATE TRIGGER baseline_members_no_update BEFORE UPDATE ON baseline_members
BEGIN
    SELECT RAISE(ABORT, 'baseline membership is immutable');
END;

CREATE TRIGGER baseline_members_no_delete BEFORE DELETE ON baseline_members
BEGIN
    SELECT RAISE(ABORT, 'baseline membership is immutable');
END;

CREATE TRIGGER summaries_no_update BEFORE UPDATE ON session_summaries
BEGIN
    SELECT RAISE(ABORT, 'summary history is append-only');
END;

CREATE TRIGGER summaries_no_delete BEFORE DELETE ON session_summaries
BEGIN
    SELECT RAISE(ABORT, 'summary history is append-only');
END;
