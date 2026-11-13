"""Helpers for the storage tests: a real migrated SQLite file, the fixture rows Agent 2's tables would hold (families,
persons, devices, calls, as backend/storage/tests/test_schema.py inserts them), a deterministic clock and a settle()."""
from __future__ import annotations

import asyncio
import itertools
import time
from contextlib import closing
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from analysis.session.fallback import FixedOrderDriver
from analysis.storage import SessionRecorder, Writer, pending_retries
from analysis.storage.util import backend_storage, connect
from analysis.tests.agent_helpers import CallRig

PERSON, FAMILY, PARENT = "p_t", "f_t", "parent_t"
REQUIRED = ("smile", "arms")  # what the backend path requires


def make_db(tmp_path: Path, name: str = "t.sqlite3") -> Path:
    path = tmp_path / name
    with closing(connect(path)) as db:
        backend_storage().apply_migrations(db)
        db.execute("INSERT INTO families VALUES (?, 0)", (FAMILY,))
        db.execute("INSERT INTO persons VALUES (?, ?, 'Ona', 'en', 0)", (PERSON, FAMILY))
        db.execute("INSERT INTO devices VALUES (?, ?, ?, 'parent', 0, NULL)", (PARENT, FAMILY, PERSON))
        db.execute("INSERT INTO devices VALUES ('family_t', ?, NULL, 'family', 0, NULL)", (FAMILY,))
    return path


def add_call(path: Path, call_id: str = "call_t", label: str = "live", person: str = PERSON) -> None:
    """The row Agent 2's call service writes when the parent answers."""
    with closing(connect(path)) as db:
        db.execute(
            "INSERT INTO calls (call_id, person_id, parent_device_id, label, state, created_at_ms, ring_deadline_at_ms, "
            "answered_at_ms, call_deadline_at_ms) VALUES (?, ?, ?, ?, 'connected', 1000, 31000, 1100, 181100)",
            (call_id, person, PARENT, label))


def end_call(path: Path, call_id: str = "call_t", reason: str = "completed") -> None:
    with closing(connect(path)) as db:
        db.execute("UPDATE calls SET state = 'ended', end_reason = ?, ended_at_ms = 900000000 WHERE call_id = ?",
                   (reason, call_id))


def agent2_final_summary(path: Path, call_id: str, status: str, phase: str = "final") -> None:
    """What Agent 2's record_message writes (session_summaries) when summaries are switched off on our side."""
    with closing(connect(path)) as db:
        revision = db.execute("SELECT coalesce(max(revision), 0) + 1 FROM session_summaries WHERE call_id = ?",
                              (call_id,)).fetchone()[0]
        db.execute("INSERT INTO session_summaries VALUES (?, ?, ?, ?, '{}', 5000)", (call_id, revision, status, phase))


def rows(path: Path, sql: str, params: Sequence[Any] = ()) -> List[Dict[str, Any]]:
    with closing(connect(path)) as db:
        return [dict(r) for r in db.execute(sql, tuple(params)).fetchall()]


def count(path: Path, table: str, where: str = "1=1", params: Sequence[Any] = ()) -> int:
    return rows(path, "SELECT count(*) AS n FROM %s WHERE %s" % (table, where), params)[0]["n"]


def clock(start: int = 10_000, step: int = 7) -> Callable[[], int]:
    counter = itertools.count(start, step)
    return lambda: next(counter)


def settle(writer: Writer, timeout: float = 10.0) -> None:
    """Everything submitted is committed, including baseline retries waiting for the call to end."""
    end = time.time() + timeout
    while True:
        assert writer.flush(timeout), "the writer did not flush"
        if pending_retries() == 0:
            assert writer.flush(timeout)
            return
        assert time.time() < end, "baseline retries did not finish"
        time.sleep(0.01)


def make_recorder(writer: Writer, call_id: str = "call_t", label: str = "live", **kw: Any) -> SessionRecorder:
    kw.setdefault("now_ms", clock())
    kw.setdefault("learn_retry_s", 0.02)
    return SessionRecorder(call_id, PERSON, label, writer=writer, **kw)


def run_fixed_call(rig: CallRig, recorder: Optional[SessionRecorder], frozen: Optional[Dict[str, str]] = None,
                   **drive_kw: Any) -> Dict[str, Any]:
    """The fixed-order driver over a CallRig; with a recorder: begin() first, finish_call() after (as the entry does)."""
    async def scenario() -> Dict[str, Any]:
        if recorder is not None:
            rig.session.recorder = recorder
            recorder.begin(rig.session, frozen or {})
        await rig.drive(FixedOrderDriver(rig.handlers).run(), **drive_kw)
        summary = rig.session.build_session_result().to_dict()
        if recorder is not None:
            recorder.finish_call(rig.session, summary)
        return summary

    return asyncio.run(scenario())
