"""Owner-3 repository functions for the shared SQLite schema (backend/DATABASE_SCHEMA.md).

Every function takes a `sqlite3.Connection` that is ALREADY inside one write transaction (BEGIN IMMEDIATE ... commit)
and runs only parameterized, short SQL: no awaits, no I/O, no model or network work. The transaction itself is opened by
whoever owns the connection: `analysis.storage.writer.Writer` (own thread), Agent 2's `Store._submit`, or
`analysis.storage.util.transact` (tests). A function that fails raises; the caller rolls the whole transaction back.

Guide transactions covered here: 2 (answer and prepare: `start_call_session`), 3 (start tool/attempt: `insert_turn`,
`insert_tool`, `insert_attempt`), 4 (`record_observation`, `update_context`, `append_event`), 5 (`advance_attempt`),
6 (`terminalize_attempt`), 7 (`append_summary`: the revision part only) and 8 (`finish_session`, `learn_baselines`).
Rules kept here: versioned definitions are published once; the event journal and observations are append-only; a
terminal result is read and compared, never replaced; revisions move by compare-and-update with an affected-row check.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from analysis import baseline as baseline_mod
from analysis.session import limits
from analysis.storage.util import ConflictError, NotReady, StaleWrite, StorageError, dumps

DB_TOOLS = ("say", "ask", "record_answer", "run_task", "recover_resource", "finish_call")  # alert_family has no row
SUMMARY_STATUS = {"concern_raised": "incomplete"}  # the schema has no concern status (see README, "For Agent 2")
LEARNING_FINAL_STATUS = ("as_usual", "learning")
TOOL_END = {"completed": "completed", "could_not_perform": "completed", "interrupted": "interrupted",
            "failed": "failed", "cancelled": "cancelled"}


# ---- reads used by several writers ----------------------------------------------------------------------------------

def parent_device(db: sqlite3.Connection, call_id: str) -> Optional[str]:
    row = db.execute("SELECT parent_device_id FROM calls WHERE call_id = ?", (call_id,)).fetchone()
    return row[0] if row else None


def latest_snapshot(db: sqlite3.Connection, person_id: str, definition_id: str,
                    real_only: bool = False) -> Optional[sqlite3.Row]:
    """Newest snapshot for the person and definition. `real_only` skips the empty placeholders (sessions = 0)."""
    return db.execute(
        "SELECT * FROM baseline_snapshots WHERE person_id = ? AND definition_id = ? "
        + ("AND sessions > 0 " if real_only else "")
        + "ORDER BY created_at_ms DESC, rowid DESC LIMIT 1", (person_id, definition_id)).fetchone()


def snapshot_members(db: sqlite3.Connection, snapshot_id: str) -> List[sqlite3.Row]:
    return db.execute("SELECT * FROM baseline_members WHERE snapshot_id = ? ORDER BY rowid",
                      (snapshot_id,)).fetchall()


def member_values(db: sqlite3.Connection, task: str, attempt_ids: Sequence[str]) -> List[Any]:
    """The stored value of each member attempt, oldest first. Arms: {'left': x, 'right': y} read from the attempt's
    finalized event (the schema keeps one number per result; the two hands live in that event's payload)."""
    out: List[Any] = []
    for attempt_id in attempt_ids:
        if task == "arms":
            row = db.execute("SELECT payload_json FROM call_events WHERE attempt_id = ? "
                             "AND event_type = 'attempt.finalized'", (attempt_id,)).fetchone()
            values = (json.loads(row[0]).get("values") if row else None) or {}
            out.append({"left": values.get("left"), "right": values.get("right")})
        else:
            row = db.execute("SELECT value FROM task_results WHERE attempt_id = ?", (attempt_id,)).fetchone()
            out.append(row[0] if row else None)
    return out


# ---- baselines: placeholders and snapshots ----------------------------------------------------------------------------

def ensure_unseeded_snapshot(db: sqlite3.Connection, person_id: str, definition_id: str, call_id: str, task: str,
                             needed: int, stamp: int) -> str:
    """A call must freeze one snapshot per task. When the database knows nothing about her yet (her usual came from
    the JSON file), reuse or create an EMPTY snapshot: no sessions, not ready, null bounds, no invented values."""
    row = db.execute("SELECT snapshot_id FROM baseline_snapshots WHERE person_id = ? AND definition_id = ? "
                     "AND sessions = 0 ORDER BY created_at_ms DESC, rowid DESC LIMIT 1",
                     (person_id, definition_id)).fetchone()
    if row is not None:
        return row[0]
    snapshot_id = "unseeded_%s_%s" % (call_id, task)
    db.execute("INSERT INTO baseline_snapshots (snapshot_id, person_id, definition_id, sessions, needed, ready, "
               "usual_low, usual_high, created_at_ms) VALUES (?, ?, ?, 0, ?, 0, NULL, NULL, ?)",
               (snapshot_id, person_id, definition_id, max(1, int(needed)), stamp))
    return snapshot_id


def start_call_session(db: sqlite3.Connection, *, call_id: str, policy_version: str, script_version: str,
                       snapshot: Dict[str, Any], stamp: int, frozen: Dict[str, Tuple[str, str]]) -> None:
    """Transaction 2, our part: the CallSession row and the frozen definition + baseline per task. The `calls` row
    (answered, connected) is Agent 2's; the schema's trigger refuses a session without it."""
    db.execute("INSERT INTO call_sessions (call_id, phase, script_version, policy_version, context_revision, "
               "snapshot_json, started_at_ms, finished_at_ms) VALUES (?, 'running', ?, ?, 0, ?, ?, NULL)",
               (call_id, script_version, policy_version, dumps(snapshot), stamp))
    for task, (definition_id, snapshot_id) in sorted(frozen.items()):
        db.execute("INSERT INTO session_baselines (call_id, task, definition_id, snapshot_id) VALUES (?, ?, ?, ?)",
                   (call_id, task, definition_id, snapshot_id))
    append_event(db, call_id, "%s.session_started" % call_id, "session.started",
                 {"policy_version": policy_version, "script_version": script_version,
                  "baselines": {t: s for t, (_d, s) in sorted(frozen.items())}}, stamp)


def update_context(db: sqlite3.Connection, call_id: str, revision: int, snapshot: Dict[str, Any]) -> None:
    """Move the session snapshot forward. The context version never goes backwards (compare-and-update)."""
    cur = db.execute("UPDATE call_sessions SET snapshot_json = ?, context_revision = ? "
                     "WHERE call_id = ? AND context_revision < ? AND phase != 'finished'",
                     (dumps(snapshot), revision, call_id, revision))
    if cur.rowcount != 1:
        raise StaleWrite("context revision %d is not newer than the stored one" % revision)


def finish_session(db: sqlite3.Connection, call_id: str, revision: int, snapshot: Dict[str, Any], stamp: int) -> bool:
    """Transaction 8, first part: the conversation state is finished (immutable from here on)."""
    cur = db.execute("UPDATE call_sessions SET phase = 'finished', finished_at_ms = max(?, started_at_ms), "
                     "snapshot_json = ?, context_revision = max(context_revision, ?) "
                     "WHERE call_id = ? AND phase != 'finished'", (stamp, dumps(snapshot), revision, call_id))
    return cur.rowcount == 1


# ---- the event journal ------------------------------------------------------------------------------------------------

def append_event(db: sqlite3.Connection, call_id: str, event_id: str, event_type: str, payload: Dict[str, Any],
                 stamp: int, *, source: str = "server", attempt_id: Optional[str] = None,
                 device_id: Optional[str] = None) -> bool:
    """Append one journal event at the next per-call position. True when appended. An identical retry returns False;
    the same id with other content is a ConflictError. The position is allocated inside the write transaction, so
    this and Agent 2's writer (same rule, same table) never collide."""
    body = dumps(payload)
    old = db.execute("SELECT call_id, source, event_type, payload_json FROM call_events WHERE event_id = ?",
                     (event_id,)).fetchone()
    if old is not None:
        if (old["call_id"], old["source"], old["event_type"]) != (call_id, source, event_type) \
                or json.loads(old["payload_json"]) != json.loads(body):
            raise ConflictError("event %s already exists with different content" % event_id)
        return False
    position = db.execute("SELECT coalesce(max(position), -1) + 1 FROM call_events WHERE call_id = ?",
                          (call_id,)).fetchone()[0]
    db.execute("INSERT INTO call_events (event_id, call_id, attempt_id, device_id, position, source, event_type, "
               "received_at_ms, payload_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
               (event_id, call_id, attempt_id, device_id, position, source, event_type, stamp, body))
    return True


# ---- model turns and tools (transaction 3) ----------------------------------------------------------------------------

def insert_turn(db: sqlite3.Connection, turn: Dict[str, Any]) -> None:
    """A finished model request. A stale turn keeps state 'stale' (its output never executed a tool)."""
    db.execute("INSERT INTO model_turns (turn_id, call_id, sequence, context_revision, model_id, state, request_json, "
               "response_json, error_code, created_at_ms, ended_at_ms) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
               (turn["turn_id"], turn["call_id"], turn["sequence"], turn["context_revision"], turn["model_id"],
                turn["state"], dumps(turn["request"]),
                dumps(turn["response"]) if turn.get("response") is not None else None,
                turn.get("error_code"), turn["created_at_ms"], max(turn["ended_at_ms"], turn["created_at_ms"])))


def insert_tool(db: sqlite3.Connection, tool: Dict[str, Any]) -> None:
    db.execute("INSERT INTO tool_calls (tool_call_id, call_id, turn_id, tool_name, arguments_json, state, result_json, "
               "created_at_ms, ended_at_ms) VALUES (?, ?, ?, ?, ?, 'running', NULL, ?, NULL)",
               (tool["tool_call_id"], tool["call_id"], tool["turn_id"], tool["name"], dumps(tool["arguments"]),
                tool["created_at_ms"]))


def finish_tool(db: sqlite3.Connection, tool_call_id: str, state: str, result: Dict[str, Any], stamp: int) -> bool:
    """Complete a tool with its serialized result. Terminal results are immutable: a second finish with the same
    content returns False, with other content a ConflictError."""
    cur = db.execute("UPDATE tool_calls SET state = ?, result_json = ?, ended_at_ms = max(?, created_at_ms) "
                     "WHERE tool_call_id = ? AND ended_at_ms IS NULL", (state, dumps(result), stamp, tool_call_id))
    if cur.rowcount == 1:
        return True
    old = db.execute("SELECT state, result_json FROM tool_calls WHERE tool_call_id = ?", (tool_call_id,)).fetchone()
    if old is None:
        raise StorageError("tool %s does not exist" % tool_call_id)
    if old["state"] != state or json.loads(old["result_json"]) != json.loads(dumps(result)):
        raise ConflictError("tool %s already ended with another result" % tool_call_id)
    return False


# ---- attempts (transactions 3, 4, 5) ----------------------------------------------------------------------------------

def insert_attempt(db: sqlite3.Connection, *, call_id: str, attempt_id: str, wire_attempt_id: str, task: str,
                   tool_call_id: str, ordinal: int, line_id: str, stamp: int, prepare_ms: int) -> None:
    """The attempt row (state 'preparing') and its journal event. The tool must be a running run_task."""
    frozen = db.execute("SELECT definition_id, snapshot_id FROM session_baselines WHERE call_id = ? AND task = ?",
                        (call_id, task)).fetchone()
    if frozen is None:
        raise StorageError("no frozen baseline for %s in call %s" % (task, call_id))
    db.execute("INSERT INTO task_attempts (attempt_id, call_id, task, definition_id, snapshot_id, tool_call_id, "
               "ordinal, state, revision, created_at_ms, preparation_deadline_at_ms) "
               "VALUES (?, ?, ?, ?, ?, ?, ?, 'preparing', 0, ?, ?)",
               (attempt_id, call_id, task, frozen["definition_id"], frozen["snapshot_id"], tool_call_id, ordinal,
                stamp, stamp + max(1, prepare_ms)))
    append_event(db, call_id, "%s.started" % attempt_id, "attempt.started",
                 {"attempt_id": wire_attempt_id, "task": task, "ordinal": ordinal, "instruction_line": line_id,
                  "tool_call_id": tool_call_id}, stamp, attempt_id=attempt_id)


def advance_attempt(db: sqlite3.Connection, attempt_id: str, expected_revision: int, state: str, stamp: int, *,
                    window_ms: int, stall_ms: int, finalize_extra_ms: int) -> None:
    """Transaction 5 and the capture start: preparing -> capturing -> finalizing, or -> stopping. Deadlines are
    set once (coalesce); the revision is compared and bumped."""
    if state == "stopping":
        cur = db.execute("UPDATE task_attempts SET state = 'stopping', revision = revision + 1 "
                         "WHERE attempt_id = ? AND revision = ? AND ended_at_ms IS NULL",
                         (attempt_id, expected_revision))
    elif state in ("capturing", "finalizing"):
        cur = db.execute(
            "UPDATE task_attempts SET state = :state, revision = revision + 1, "
            "capture_started_at_ms = coalesce(capture_started_at_ms, :stamp), "
            "window_deadline_at_ms = coalesce(window_deadline_at_ms, coalesce(capture_started_at_ms, :stamp) + :window), "
            "observation_deadline_at_ms = coalesce(observation_deadline_at_ms, coalesce(capture_started_at_ms, :stamp) + :stall), "
            "finalization_deadline_at_ms = CASE WHEN :state = 'finalizing' THEN coalesce(finalization_deadline_at_ms, "
            "coalesce(capture_started_at_ms, :stamp) + :window + :extra) ELSE finalization_deadline_at_ms END "
            "WHERE attempt_id = :id AND revision = :rev AND ended_at_ms IS NULL",
            {"state": state, "stamp": stamp, "window": max(1, window_ms), "stall": stall_ms, "extra": finalize_extra_ms,
             "id": attempt_id, "rev": expected_revision})
    else:
        raise StorageError("not an active attempt state: %s" % state)
    if cur.rowcount != 1:
        raise StaleWrite("attempt %s is not at revision %d" % (attempt_id, expected_revision))


def record_observation(db: sqlite3.Connection, *, call_id: str, attempt_id: str, event_id: str, obs: Dict[str, Any],
                       expected_revision: int, stamp: int, stall_ms: int) -> bool:
    """Transaction 4, observation part: journal event, observation row, latest projection and deadline together.
    The schema's trigger demands source 'client' and the call's parent device (see README, "For Agent 2")."""
    payload = dict(obs)
    payload["derived_by"] = "server_analysis"
    if not append_event(db, call_id, event_id, "task.observation", payload, stamp, source="client",
                        attempt_id=attempt_id, device_id=parent_device(db, call_id)):
        return False  # an identical retry: acknowledged, nothing else written
    quality = obs["quality"]
    db.execute("INSERT INTO task_observations (event_id, call_id, attempt_id, sequence, elapsed_ms, phase, "
               "quality_status, quality_reasons_json, metrics_json, signal) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
               (event_id, call_id, attempt_id, obs["sequence"], obs["elapsed_ms"], obs["phase"], quality["status"],
                dumps(quality["reasons"]), dumps(obs["metrics"]), obs["signal"]))
    cur = db.execute("UPDATE task_attempts SET latest_observation_event_id = ?, revision = revision + 1, "
                     "observation_deadline_at_ms = max(?, coalesce(capture_started_at_ms, 0)) "
                     "WHERE attempt_id = ? AND revision = ? AND ended_at_ms IS NULL",
                     (event_id, stamp + stall_ms, attempt_id, expected_revision))
    if cur.rowcount != 1:
        raise StaleWrite("attempt %s is not at revision %d" % (attempt_id, expected_revision))
    return True


def _resolve_event(db: sqlite3.Connection, call_id: str, wire_id: Optional[str]) -> Optional[str]:
    """The stored id of an interruption's source event, or None when that event is not in the journal (for example
    Agent 2's store is not writing client events). The wire id stays in the finalized event's payload."""
    if not wire_id:
        return None
    for candidate in ("%s.client_%s" % (call_id, wire_id), "%s.%s" % (call_id, wire_id)):
        if db.execute("SELECT 1 FROM call_events WHERE event_id = ? AND call_id = ?", (candidate, call_id)).fetchone():
            return candidate
    return None


def _result_columns(result: Dict[str, Any], snap: Optional[sqlite3.Row], db: sqlite3.Connection,
                    call_id: str) -> Dict[str, Any]:
    reading = result.get("reading")
    quality = result["quality"]
    interruption = result.get("interruption")
    has_value = (result["outcome"] == "completed" and quality["status"] == "usable"
                 and result["performance"] == "observed" and reading is not None)
    low = high = outside = side = None
    if has_value and snap is not None and snap["ready"] == 1:
        # Only a ready frozen snapshot may carry a comparison, and it must be that snapshot's own bounds (trigger).
        low, high = reading.get("usual_low"), reading.get("usual_high")
        outside = None if reading.get("outside") is None else int(bool(reading["outside"]))
        side = reading.get("side")
    return {"outcome": result["outcome"], "quality_status": quality["status"],
            "quality_reasons": list(quality["reasons"]), "performance": result["performance"],
            "value": float(reading["value"]) if has_value else None,
            "usual_low": low, "usual_high": high, "outside": outside, "side": side,
            "interruption_code": interruption["code"] if interruption else None,
            "source_event_id": _resolve_event(db, call_id, interruption.get("source_event_id")) if interruption else None,
            "recoverable": (int(bool(interruption["recoverable"])) if interruption else None)}


def terminalize_attempt(db: sqlite3.Connection, *, call_id: str, attempt_id: str, result: Dict[str, Any],
                        values: Optional[Dict[str, float]], expected_revision: int, tool_call_id: str, stamp: int,
                        decision: Dict[str, Any]) -> str:
    """Transaction 6: attempt state/end/terminal reference, the one result, the tool's serialized result and the
    decision event, together (the deferred FK refuses a terminal attempt without its result). Returns 'terminal', or
    'duplicate' when the identical result already exists. A different result for a terminal attempt is a
    ConflictError. Never INSERT OR REPLACE."""
    att = db.execute("SELECT * FROM task_attempts WHERE attempt_id = ?", (attempt_id,)).fetchone()
    if att is None:
        raise StorageError("attempt %s does not exist" % attempt_id)
    snap = db.execute("SELECT * FROM baseline_snapshots WHERE snapshot_id = ?", (att["snapshot_id"],)).fetchone()
    cols = _result_columns(result, snap, db, call_id)

    old = db.execute("SELECT * FROM task_results WHERE attempt_id = ?", (attempt_id,)).fetchone()
    if old is not None:
        same = (old["outcome"], old["quality_status"], json.loads(old["quality_reasons_json"]), old["performance"],
                old["value"], old["usual_low"], old["usual_high"], old["outside"], old["side"],
                old["interruption_code"], old["recoverable"]) == (
            cols["outcome"], cols["quality_status"], cols["quality_reasons"], cols["performance"], cols["value"],
            cols["usual_low"], cols["usual_high"], cols["outside"], cols["side"], cols["interruption_code"],
            cols["recoverable"])
        if not same:
            raise ConflictError("attempt %s already has a different terminal result" % attempt_id)
        return "duplicate"

    ended = max(stamp, att["created_at_ms"])
    cur = db.execute("UPDATE task_attempts SET state = ?, ended_at_ms = ?, terminal_result_attempt_id = attempt_id, "
                     "revision = revision + 1 WHERE attempt_id = ? AND revision = ? AND ended_at_ms IS NULL",
                     (cols["outcome"], ended, attempt_id, expected_revision))
    if cur.rowcount != 1:
        raise StaleWrite("attempt %s is not at revision %d" % (attempt_id, expected_revision))
    db.execute("INSERT INTO task_results (attempt_id, call_id, definition_id, outcome, quality_status, "
               "quality_reasons_json, performance, value, usual_low, usual_high, outside, side, interruption_code, "
               "source_event_id, recoverable, finalized_at_ms) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
               (attempt_id, call_id, att["definition_id"], cols["outcome"], cols["quality_status"],
                dumps(cols["quality_reasons"]), cols["performance"], cols["value"], cols["usual_low"],
                cols["usual_high"], cols["outside"], cols["side"], cols["interruption_code"],
                cols["source_event_id"], cols["recoverable"], ended))
    finish_tool(db, tool_call_id, TOOL_END[cols["outcome"]], {"attempt_id": att["attempt_id"], "task_result": result},
                ended)
    payload = {"result": result, "values": values, "decision": decision}
    append_event(db, call_id, "%s.finalized" % attempt_id, "attempt.finalized", payload, ended, attempt_id=attempt_id)
    return "terminal"


# ---- answers ---------------------------------------------------------------------------------------------------------

def insert_answer(db: sqlite3.Connection, *, call_id: str, tool_call_id: str, question_id: str, status: str,
                  transcript: Optional[str], stamp: int) -> str:
    """One row per ask (retries are kept, never overwritten). Nothing is selected yet."""
    ordinal = db.execute("SELECT coalesce(max(ordinal), 0) + 1 FROM answers WHERE call_id = ? AND question_id = ?",
                         (call_id, question_id)).fetchone()[0]
    answer_id = "%s.ans.%s.%d" % (call_id, question_id, ordinal)
    db.execute("INSERT INTO answers (answer_id, call_id, tool_call_id, question_id, ordinal, status, transcript, "
               "normalized_value, selected, created_at_ms) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, 0, ?)",
               (answer_id, call_id, tool_call_id, question_id, ordinal, status,
                transcript[:2000] if transcript else None, stamp))
    return answer_id


def select_answer(db: sqlite3.Connection, call_id: str, question_id: str, value: str) -> Optional[str]:
    """record_answer: select ONE answer for the question (the latest heard one, else the latest row). Moving the
    selection clears the previous one first, so at most one is selected. Medicine is yes/no/unclear."""
    row = db.execute("SELECT answer_id FROM answers WHERE call_id = ? AND question_id = ? "
                     "ORDER BY (status = 'heard') DESC, ordinal DESC LIMIT 1", (call_id, question_id)).fetchone()
    if row is None:
        return None
    normalized = (value if value in ("yes", "no", "unclear") else "unclear") if question_id == "medicine" \
        else str(value)[:2000]
    db.execute("UPDATE answers SET selected = 0 WHERE call_id = ? AND question_id = ? AND selected = 1",
               (call_id, question_id))
    db.execute("UPDATE answers SET selected = 1, normalized_value = ? WHERE answer_id = ?", (normalized, row[0]))
    return row[0]


# ---- summaries (transaction 7, the revision part) -----------------------------------------------------------------------

def append_summary(db: sqlite3.Connection, call_id: str, phase: str, summary: Dict[str, Any], stamp: int) -> int:
    """Append the next summary revision (mid_call or final). Append-only; the schema's trigger keeps a confirmed
    change from being replaced and allows one final. The family message and outbox are Agent 2's."""
    status = SUMMARY_STATUS.get(summary["status"], summary["status"])
    body = dict(summary)
    body["session_status"] = summary["status"]
    revision = db.execute("SELECT coalesce(max(revision), 0) + 1 FROM session_summaries WHERE call_id = ?",
                          (call_id,)).fetchone()[0]
    db.execute("INSERT INTO session_summaries (call_id, revision, status, phase, summary_json, created_at_ms) "
               "VALUES (?, ?, ?, ?, ?, ?)", (call_id, revision, status, phase, dumps(body), stamp))
    return revision


# ---- transaction 8: learn ---------------------------------------------------------------------------------------------

def learn_baselines(db: sqlite3.Connection, *, call_id: str, person_id: str, label: str, needed: int,
                    items: Dict[str, str], stamp: int) -> List[str]:
    """Create a NEW snapshot per task from the previous members plus this call's final attempt (`items`: task ->
    stored attempt id). The snapshot this call used is never touched. Raises NotReady until Agent 2 has ended the
    call, our session is finished and the final summary exists (the schema's eligibility trigger needs all three).
    Returns the new snapshot ids ([] when the call is not eligible or there is nothing to add)."""
    call = db.execute("SELECT state FROM calls WHERE call_id = ?", (call_id,)).fetchone()
    session = db.execute("SELECT phase FROM call_sessions WHERE call_id = ?", (call_id,)).fetchone()
    final = db.execute("SELECT status FROM session_summaries WHERE call_id = ? AND phase = 'final'",
                       (call_id,)).fetchone()
    if call is None or session is None:
        raise StorageError("call %s has no call or session row" % call_id)
    if call["state"] != "ended" or session["phase"] != "finished" or final is None:
        raise NotReady("call %s is not ended/finished/summarized yet" % call_id)
    if label not in ("live", "normal") or final["status"] not in LEARNING_FINAL_STATUS:
        return []
    created: List[str] = []
    for task, attempt_id in sorted(items.items()):
        sb = db.execute("SELECT definition_id FROM session_baselines WHERE call_id = ? AND task = ?",
                        (call_id, task)).fetchone()
        res = db.execute("SELECT * FROM task_results WHERE attempt_id = ?", (attempt_id,)).fetchone()
        if sb is None or res is None:
            continue
        definition_id = sb["definition_id"]
        if not (res["outcome"] == "completed" and res["quality_status"] == "usable"
                and res["performance"] == "observed" and res["value"] is not None and not res["outside"]):
            continue
        if db.execute("SELECT 1 FROM baseline_members WHERE source_call_id = ? AND definition_id = ?",
                      (call_id, definition_id)).fetchone():
            continue  # already learned (idempotent)
        prev = latest_snapshot(db, person_id, definition_id, real_only=True)
        prev_calls = [(m["source_attempt_id"], m["source_call_id"]) for m in snapshot_members(db, prev["snapshot_id"])] \
            if prev else []
        members = prev_calls + [(attempt_id, call_id)]
        values = member_values(db, task, [a for a, _c in members])
        sessions = len(members)
        low = high = None
        ready = 0
        if task == "arms":  # the schema holds one range per snapshot; the two hands are not representable (README)
            if any(v["left"] is None or v["right"] is None for v in values):
                continue
        else:
            if any(v is None for v in values):
                continue
            kwargs: Dict[str, Any] = {"metric": "sentence_duration", "min_half_width": limits.SENTENCE_MIN_HALF_WIDTH_S} \
                if task == "sentence" else {"metric": "corner_height"}
            b = baseline_mod.make_baseline(values, needed=needed, **kwargs)
            ready, low, high = int(b["ready"]), b["usual_low"], b["usual_high"]
        snapshot_id = "snap_%s_%s" % (call_id, task)
        db.execute("INSERT INTO baseline_snapshots (snapshot_id, person_id, definition_id, sessions, needed, ready, "
                   "usual_low, usual_high, created_at_ms) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                   (snapshot_id, person_id, definition_id, sessions, max(1, int(needed)), ready, low, high, stamp))
        for source_attempt, source_call in members:
            db.execute("INSERT INTO baseline_members (snapshot_id, source_attempt_id, source_call_id, definition_id) "
                       "VALUES (?, ?, ?, ?)", (snapshot_id, source_attempt, source_call, definition_id))
        created.append(snapshot_id)
    return created
