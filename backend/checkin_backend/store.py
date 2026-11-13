"""Durable call-service records in the shared SQLite schema (storage/migrations, DATABASE_SCHEMA.md).

Writes the tables the call service owns (owner 2): families, persons, devices, calls, call_events,
client_commands, family_messages, notification_outbox and delivery_attempts. It also writes
session_summaries rows for summaries that reach the call service through `publish`, because a family
message must reference one; Agent 3's coordinator should take that over.

The event loop never blocks on SQLite. Each `record_*` method stamps the wall-clock time, then hands one
transaction to a single writer thread that owns its own connection. Transactions run in submission order.
The in-memory services stay the live source of truth during a call; the database is the durable history
that the family feed and call lookups read, including after a restart.

Known limits, deliberately small for the demo:
- Write-behind: SSE delivery happens before its outbox/delivery rows commit, not "commit before delivery".
- Task capture commands and their events have attempt_id NULL: task_attempts needs Agent 3's sessions,
  definitions and baselines first. The wire attempt ID stays in the journal payload.
- Wire IDs (commands, client events) are only unique per call, so stored IDs are "<call_id>.<wire_id>".
- Startup recovery only closes calls and commands left open by a previous process ('shutdown' /
  'cancelled'). It does not write summaries or retry deliveries.
- No raw media, audio or transcripts are stored.
"""

import asyncio
import json
import logging
import queue
import threading
import time
from collections.abc import Callable
from contextlib import closing
from pathlib import Path
from typing import Any

from storage import apply_migrations, connect_database

from .people import Person

log = logging.getLogger("checkin_backend.store")

Job = tuple[str, Callable[[Any], None]]

# Client event types that end a command, and the command state they lead to.
COMMAND_OUTCOMES = {
    "playback.completed": "completed",
    "command.failed": "failed",
    "command.cancelled": "cancelled",
}


def now_ms() -> int:
    return time.time_ns() // 1_000_000


def iso(ms: int) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ms / 1000))


def stored_id(call_id: str, wire_id: str) -> str:
    return f"{call_id}.{wire_id}"


def dumps(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True, default=str)


class Store:
    """Writes call-service records. Create, `open()` once at startup, `close()` at shutdown."""

    enabled = True

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.failures = 0
        self._jobs: queue.SimpleQueue[Job | None] = queue.SimpleQueue()
        self._thread: threading.Thread | None = None
        self._parent_device: dict[str, str] = {}

    # ---- lifecycle ---------------------------------------------------------------------------------

    def open(self, people: list[Person]) -> None:
        """Blocking: migrate, close work left open by a previous process, seed people, start the writer."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(connect_database(self.path)) as db:
            applied = apply_migrations(db)
            if applied:
                log.info("store: applied %s", ", ".join(applied))
            self._transaction(db, "recover", lambda c: self._close_open_work(c, now_ms()))
            self._transaction(db, "seed people", lambda c: self._seed(c, people, now_ms()))
        self._thread = threading.Thread(target=self._run, name="checkin-store", daemon=True)
        self._thread.start()

    def close(self) -> None:
        """Blocking: record calls still open as ended by shutdown, drain the queue and stop the writer."""
        if self._thread is None:
            return
        stamp = now_ms()
        self._submit("shutdown", lambda c: self._close_open_work(c, stamp))
        self._jobs.put(None)
        self._thread.join(timeout=10)
        self._thread = None

    async def flush(self) -> None:
        """Wait until everything submitted so far is committed (tests and reads that must see it)."""
        if self._thread is None:
            return
        done = threading.Event()
        self._submit("flush", lambda c: done.set())
        await asyncio.to_thread(done.wait, 10)

    def _submit(self, name: str, job: Callable[[Any], None]) -> None:
        if self._thread is None:
            return
        self._jobs.put((name, job))

    def _run(self) -> None:
        with closing(connect_database(self.path)) as db:
            while True:
                item = self._jobs.get()
                if item is None:
                    return
                name, job = item
                self._transaction(db, name, job)

    def _transaction(self, db: Any, name: str, job: Callable[[Any], None]) -> None:
        try:
            db.execute("BEGIN IMMEDIATE")
            job(db)
            db.commit()
        except Exception:
            db.rollback()
            self.failures += 1
            log.exception("store: %s was not saved", name)

    # ---- people and devices ----------------------------------------------------------------------------

    @staticmethod
    def _seed(db: Any, people: list[Person], stamp: int) -> None:
        for person in people:
            db.execute("INSERT OR IGNORE INTO families (family_id, created_at_ms) VALUES (?, ?)",
                       (person.family_id, stamp))
            db.execute(
                "INSERT INTO persons (person_id, family_id, display_name, locale, created_at_ms) "
                "VALUES (?, ?, ?, 'en', ?) ON CONFLICT (person_id) DO UPDATE SET display_name = excluded.display_name",
                (person.person_id, person.family_id, person.name, stamp),
            )

    def record_device(self, device_id: str, role: str, person_id: str | None, family_id: str) -> None:
        stamp = now_ms()

        def job(db: Any) -> None:
            db.execute("INSERT OR IGNORE INTO families (family_id, created_at_ms) VALUES (?, ?)", (family_id, stamp))
            row = db.execute("SELECT role, person_id, family_id FROM devices WHERE device_id = ?",
                             (device_id,)).fetchone()
            if row is None:
                db.execute(
                    "INSERT INTO devices (device_id, family_id, person_id, role, registered_at_ms, last_seen_at_ms) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (device_id, family_id, person_id, role, stamp, stamp),
                )
                return
            if tuple(row) != (role, person_id, family_id):
                # Calls reference the device with its person; history keeps the first identity.
                log.warning("store: device %s re-registered with another role/person; keeping the stored one",
                            device_id)
            db.execute("UPDATE devices SET last_seen_at_ms = max(?, registered_at_ms) WHERE device_id = ?",
                       (stamp, device_id))

        self._submit("device", job)

    # ---- calls -------------------------------------------------------------------------------------

    def record_call_started(self, call_id: str, person: Person, parent_devices: list[str],
                            ring_timeout_s: float, ring_event: dict[str, Any]) -> None:
        stamp = now_ms()
        device_id = parent_devices[0] if parent_devices else None
        if device_id is not None:
            self._parent_device[call_id] = device_id
        ring_deadline = stamp + max(1, round(ring_timeout_s * 1000))

        def job(db: Any) -> None:
            db.execute(
                "INSERT INTO calls (call_id, person_id, parent_device_id, label, state, created_at_ms, "
                "ring_deadline_at_ms) VALUES (?, ?, ?, ?, 'ringing', ?, ?)",
                (call_id, person.person_id, device_id, person.label, stamp, ring_deadline),
            )
            self._event(db, call_id, "ring", "server", "call.incoming", ring_event, stamp)
            for target in parent_devices:
                db.execute(
                    "INSERT INTO notification_outbox (notification_id, dedupe_key, purpose, call_id, device_id, "
                    "payload_json, state, next_attempt_at_ms, created_at_ms, updated_at_ms) "
                    "VALUES (?, ?, 'incoming_call', ?, ?, ?, 'queued', ?, ?, ?)",
                    (f"{stored_id(call_id, 'ring')}.{target}", f"ring:{call_id}:{target}", call_id, target, dumps(ring_event),
                     stamp, stamp, stamp),
                )

        self._submit("call started", job)

    def record_ring_sent(self, call_id: str, parent_devices: list[str]) -> None:
        stamp = now_ms()

        def job(db: Any) -> None:
            for target in parent_devices:
                self._delivered(db, f"{stored_id(call_id, 'ring')}.{target}", stamp)

        self._submit("ring sent", job)

    def record_call_answered(self, call_id: str, call_timeout_s: float) -> None:
        stamp = now_ms()
        deadline = stamp + max(1, round(call_timeout_s * 1000))

        def job(db: Any) -> None:
            db.execute(
                "UPDATE calls SET state = 'connected', answered_at_ms = max(?, created_at_ms), "
                "call_deadline_at_ms = max(?, created_at_ms + 1), revision = revision + 1 "
                "WHERE call_id = ? AND state = 'ringing'",
                (stamp, deadline, call_id),
            )
            self._event(db, call_id, "answer", "client", "call.answered", {}, stamp,
                        device_id=self._device_of(db, call_id))

        self._submit("call answered", job)

    def record_call_ended(self, call_id: str, state: str, reason: str | None) -> None:
        stamp = now_ms()

        def job(db: Any) -> None:
            db.execute(
                "UPDATE calls SET state = ?, end_reason = ?, "
                "ended_at_ms = max(?, coalesce(answered_at_ms, created_at_ms)), revision = revision + 1 "
                "WHERE call_id = ? AND ended_at_ms IS NULL",
                (state, reason, stamp, call_id),
            )
            self._event(db, call_id, "end", "server", "call.ended", {"state": state, "reason": reason}, stamp)
            self._cancel_commands(db, call_id, stamp)

        self._submit("call ended", job)
        self._parent_device.pop(call_id, None)

    # ---- commands and client events ---------------------------------------------------------------------

    def record_command(self, call_id: str, command: dict[str, Any], deadline_s: float) -> None:
        """A command.execute pushed to the parent device."""
        stamp = now_ms()
        wire_id = command["command_id"]
        device_id = self._parent_device.get(call_id)
        deadline = stamp + max(1, round(deadline_s * 1000))

        def job(db: Any) -> None:
            if device_id is None:
                return
            event_id = self._event(db, call_id, f"exec_{wire_id}", "server", "command.execute", command, stamp)
            if event_id is None:
                return
            db.execute(
                "INSERT INTO client_commands (command_id, event_id, call_id, device_id, kind, state, "
                "created_at_ms, deadline_at_ms, sent_at_ms) VALUES (?, ?, ?, ?, ?, 'sent', ?, ?, ?)",
                (stored_id(call_id, wire_id), event_id, call_id, device_id, command["kind"], stamp, deadline, stamp),
            )

        self._submit("command", job)

    def record_command_cancel(self, call_id: str, wire_id: str, reason: str) -> None:
        """A command.cancel pushed to the parent device (timeout, task stop, call end)."""
        stamp = now_ms()

        def job(db: Any) -> None:
            self._event(db, call_id, f"cancel_{wire_id}", "server", "command.cancel",
                        {"command_id": wire_id, "reason": reason}, stamp)
            state = "failed" if reason == "timeout" else "cancelled"
            self._finish_command(db, call_id, wire_id, state, stamp, None)

        self._submit("command cancel", job)

    def record_command_done(self, call_id: str, wire_id: str, state: str) -> None:
        """An answer upload or a task finish completed the command on the server side."""
        stamp = now_ms()
        self._submit("command done", lambda db: self._finish_command(db, call_id, wire_id, state, stamp, None))

    def record_client_event(self, call_id: str, event: dict[str, Any]) -> None:
        """An accepted client event: journal it and move its command along."""
        stamp = now_ms()

        def job(db: Any) -> None:
            device_id = self._device_of(db, call_id)
            if device_id is None:
                return
            event_id = self._event(db, call_id, f"client_{event['event_id']}", "client", event["type"], event,
                                   stamp, device_id=device_id)
            wire_id = event.get("command_id")
            if event_id is None or wire_id is None:
                return
            if event["type"] == "capture.started":
                db.execute(
                    "UPDATE client_commands SET state = 'started', acknowledged_at_ms = max(?, created_at_ms) "
                    "WHERE command_id = ? AND ended_at_ms IS NULL",
                    (stamp, stored_id(call_id, wire_id)),
                )
            elif event["type"] in COMMAND_OUTCOMES:
                self._finish_command(db, call_id, wire_id, COMMAND_OUTCOMES[event["type"]], stamp, event_id)

        self._submit(f"client {event['type']}", job)

    # ---- family messages -----------------------------------------------------------------------------

    def record_message(self, call_id: str, family_id: str, message: dict[str, Any], phase: str,
                       summary: dict[str, Any] | None, family_devices: list[str]) -> None:
        stamp = now_ms()
        kind = message["kind"]

        def job(db: Any) -> None:
            revision = db.execute("SELECT coalesce(max(revision), 0) + 1 FROM session_summaries WHERE call_id = ?",
                                  (call_id,)).fetchone()[0]
            db.execute(
                "INSERT INTO session_summaries (call_id, revision, status, phase, summary_json, created_at_ms) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (call_id, revision, kind, phase, dumps(summary or {}), stamp),
            )
            db.execute(
                "INSERT INTO family_messages (message_id, call_id, family_id, summary_revision, kind, template_id, "
                "text, created_at_ms, updated_at_ms) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (message_id) DO UPDATE SET summary_revision = excluded.summary_revision, "
                "kind = excluded.kind, template_id = excluded.template_id, text = excluded.text, "
                "updated_at_ms = max(excluded.updated_at_ms, family_messages.created_at_ms)",
                (message["message_id"], call_id, family_id, revision, kind, f"family.{kind}.v1", message["text"],
                 stamp, stamp),
            )
            for target in family_devices:
                notification_id = f"{message['message_id']}.r{revision}.{target}"
                db.execute(
                    "INSERT INTO notification_outbox (notification_id, dedupe_key, purpose, call_id, device_id, "
                    "message_id, summary_revision, payload_json, state, next_attempt_at_ms, created_at_ms, "
                    "updated_at_ms) VALUES (?, ?, 'family_message', ?, ?, ?, ?, ?, 'queued', ?, ?, ?)",
                    (notification_id, f"msg:{message['message_id']}:{revision}:{target}", call_id, target,
                     message["message_id"], revision, dumps(message), stamp, stamp, stamp),
                )
                # Pushed over SSE in-process before this commit (write-behind).
                self._delivered(db, notification_id, stamp)

        self._submit("family message", job)

    # ---- reads (run in a worker thread; never on the event loop) ----------------------------------------------

    async def family_messages(self, family_id: str) -> list[dict[str, Any]]:
        await self.flush()
        return await asyncio.to_thread(self._read, self._family_messages, family_id)

    async def call_record(self, call_id: str) -> dict[str, Any] | None:
        await self.flush()
        return await asyncio.to_thread(self._read, self._call_record, call_id)

    def _read(self, query: Callable[[Any, str], Any], key: str) -> Any:
        with closing(connect_database(self.path)) as db:
            return query(db, key)

    @staticmethod
    def _message_dict(row: Any) -> dict[str, Any]:
        return {"message_id": row["message_id"], "call_id": row["call_id"], "person_id": row["person_id"],
                "kind": row["kind"], "text": row["text"], "phase": row["phase"], "clip_url": None,
                "created_at": iso(row["created_at_ms"]), "updated_at": iso(row["updated_at_ms"])}

    _MESSAGE_SQL = (
        "SELECT m.message_id, m.call_id, c.person_id, m.kind, m.text, s.phase, m.created_at_ms, m.updated_at_ms "
        "FROM family_messages m JOIN calls c USING (call_id) "
        "JOIN session_summaries s ON s.call_id = m.call_id AND s.revision = m.summary_revision "
    )

    def _family_messages(self, db: Any, family_id: str) -> list[dict[str, Any]]:
        rows = db.execute(self._MESSAGE_SQL + "WHERE m.family_id = ? ORDER BY m.updated_at_ms DESC",
                          (family_id,)).fetchall()
        return [self._message_dict(row) for row in rows]

    def _call_record(self, db: Any, call_id: str) -> dict[str, Any] | None:
        call = db.execute("SELECT call_id, person_id, state, end_reason FROM calls WHERE call_id = ?",
                          (call_id,)).fetchone()
        if call is None:
            return None
        message = db.execute(self._MESSAGE_SQL + "WHERE m.call_id = ?", (call_id,)).fetchone()
        summary = db.execute("SELECT summary_json FROM session_summaries WHERE call_id = ? AND phase = 'final'",
                             (call_id,)).fetchone()
        events = db.execute("SELECT source, event_type, payload_json FROM call_events WHERE call_id = ? "
                            "ORDER BY position", (call_id,)).fetchall()
        log_entries = []
        for event in events[-50:]:
            payload = json.loads(event["payload_json"])
            entry = {"kind": event["event_type"], "source": event["source"]}
            for key in ("command_id", "line_id", "kind", "reason", "state", "resource"):
                if key in payload and key not in entry:
                    entry[key] = payload[key]
            log_entries.append(entry)
        return {"call_id": call["call_id"], "person_id": call["person_id"], "state": call["state"],
                "reason": call["end_reason"], "message": self._message_dict(message) if message else None,
                "summary": json.loads(summary["summary_json"]) if summary else None, "log": log_entries,
                "stored": True}

    # ---- helpers (run inside a writer transaction) ----------------------------------------------------------

    def _device_of(self, db: Any, call_id: str) -> str | None:
        device_id = self._parent_device.get(call_id)
        if device_id is not None:
            return device_id
        row = db.execute("SELECT parent_device_id FROM calls WHERE call_id = ?", (call_id,)).fetchone()
        return row[0] if row else None

    @staticmethod
    def _event(db: Any, call_id: str, local_id: str, source: str, event_type: str, payload: dict[str, Any],
               stamp: int, device_id: str | None = None) -> str | None:
        """Append one journal event. Returns its stored ID, or None when it was already journaled."""
        event_id = stored_id(call_id, local_id)
        if db.execute("SELECT 1 FROM call_events WHERE event_id = ?", (event_id,)).fetchone():
            return None
        position = db.execute("SELECT coalesce(max(position), -1) + 1 FROM call_events WHERE call_id = ?",
                              (call_id,)).fetchone()[0]
        db.execute(
            "INSERT INTO call_events (event_id, call_id, device_id, position, source, event_type, received_at_ms, "
            "payload_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (event_id, call_id, device_id, position, source, event_type, stamp, dumps(payload)),
        )
        return event_id

    @staticmethod
    def _finish_command(db: Any, call_id: str, wire_id: str, state: str, stamp: int,
                        result_event_id: str | None) -> None:
        db.execute(
            "UPDATE client_commands SET state = ?, ended_at_ms = max(?, created_at_ms), result_event_id = ?, "
            "acknowledged_at_ms = CASE WHEN ? IS NULL THEN acknowledged_at_ms "
            "ELSE coalesce(acknowledged_at_ms, max(?, created_at_ms)) END "
            "WHERE command_id = ? AND ended_at_ms IS NULL",
            (state, stamp, result_event_id, result_event_id, stamp, stored_id(call_id, wire_id)),
        )

    @staticmethod
    def _cancel_commands(db: Any, call_id: str, stamp: int) -> None:
        db.execute(
            "UPDATE client_commands SET state = 'cancelled', ended_at_ms = max(?, created_at_ms) "
            "WHERE call_id = ? AND ended_at_ms IS NULL",
            (stamp, call_id),
        )

    @staticmethod
    def _delivered(db: Any, notification_id: str, stamp: int) -> None:
        row = db.execute("SELECT attempts, created_at_ms FROM notification_outbox WHERE notification_id = ?",
                         (notification_id,)).fetchone()
        if row is None:
            return
        ordinal = row["attempts"] + 1
        started = max(stamp, row["created_at_ms"])
        db.execute(
            "INSERT INTO delivery_attempts (notification_id, ordinal, transport, state, started_at_ms, ended_at_ms) "
            "VALUES (?, ?, 'sse', 'accepted', ?, ?)",
            (notification_id, ordinal, started, started),
        )
        db.execute(
            "UPDATE notification_outbox SET state = 'accepted', attempts = ?, updated_at_ms = ? "
            "WHERE notification_id = ?",
            (ordinal, started, notification_id),
        )

    @classmethod
    def _close_open_work(cls, db: Any, stamp: int) -> None:
        """Calls and commands a previous process (or this one, at shutdown) left open."""
        for (call_id,) in db.execute("SELECT call_id FROM calls WHERE ended_at_ms IS NULL").fetchall():
            db.execute(
                "UPDATE calls SET state = 'ended', end_reason = 'shutdown', "
                "ended_at_ms = max(?, coalesce(answered_at_ms, created_at_ms)), revision = revision + 1 "
                "WHERE call_id = ?",
                (stamp, call_id),
            )
            cls._event(db, call_id, "end", "server", "call.ended", {"state": "ended", "reason": "shutdown"}, stamp)
            cls._cancel_commands(db, call_id, stamp)
        db.execute(
            "UPDATE notification_outbox SET state = 'cancelled', updated_at_ms = max(?, created_at_ms) "
            "WHERE purpose = 'incoming_call' AND state IN ('queued', 'failed')",
            (stamp,),
        )


class NoStore(Store):
    """Persistence switched off (DATABASE_PATH empty): every record is a no-op."""

    enabled = False

    def __init__(self) -> None:
        super().__init__(Path("unused"))

    def open(self, people: list[Person]) -> None:
        return

    async def family_messages(self, family_id: str) -> list[dict[str, Any]]:
        raise LookupError("persistence is off")

    async def call_record(self, call_id: str) -> dict[str, Any] | None:
        return None
