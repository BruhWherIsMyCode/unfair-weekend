"""The single writer thread of this package: one thread, one connection, one queue; each job is one transaction.

`Writer.submit(name, job)` has the SAME signature as `Store._submit` in Agent 2's store (`research/agent4_store/store.py`):
`job(db)` runs between BEGIN IMMEDIATE and commit on the writer's own connection; a failure rolls back, counts and logs
one line. When the backend runs ONE shared writer thread (their store), it passes its `_submit` to `SessionRecorder`
instead and this class is not used.
"""
from __future__ import annotations

import logging
import queue
import threading
from contextlib import closing
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from analysis.storage.util import connect

log = logging.getLogger(__name__)

Job = Callable[[Any], None]


class Writer:
    def __init__(self, path: Any) -> None:
        self.path = Path(path)
        self.failures = 0
        self._jobs: "queue.SimpleQueue[Optional[tuple]]" = queue.SimpleQueue()
        self._thread = threading.Thread(target=self._run, name="uw-analysis-store", daemon=True)
        self._closed = False
        self._thread.start()

    def submit(self, name: str, job: Job) -> None:
        if self._closed:
            return
        self._jobs.put((name, job))

    def flush(self, timeout: float = 10.0) -> bool:
        """Wait until everything submitted so far is committed (tests; never called from a call path)."""
        done = threading.Event()
        self.submit("flush", lambda db: done.set())
        return done.wait(timeout)

    def close(self, timeout: float = 10.0) -> None:
        if self._closed:
            return
        self.flush(timeout)
        self._closed = True
        self._jobs.put(None)
        self._thread.join(timeout)

    def _run(self) -> None:
        try:
            db = connect(self.path)
        except Exception as exc:  # the file cannot be opened: every job is dropped, the calls go on
            log.warning("recording: the database %s cannot be opened (%s)", self.path.name, type(exc).__name__)
            db = None
        try:
            while True:
                item = self._jobs.get()
                if item is None:
                    return
                name, job = item
                if db is None:
                    self.failures += 1
                    if name == "flush":
                        job(None)
                    continue
                self._transaction(db, name, job)
        finally:
            if db is not None:
                db.close()

    def _transaction(self, db: Any, name: str, job: Job) -> None:
        try:
            db.execute("BEGIN IMMEDIATE")
            job(db)
            db.commit()
        except Exception as exc:
            try:
                db.rollback()
            except Exception:
                pass
            self.failures += 1
            log.warning("recording: %s was not saved (%s)", name, type(exc).__name__)


_WRITERS: Dict[str, Writer] = {}
_LOCK = threading.Lock()


def shared_writer(path: Any) -> Writer:
    """The one writer thread per database file in this process (created on first use)."""
    key = str(Path(path).resolve())
    with _LOCK:
        writer = _WRITERS.get(key)
        if writer is None or writer._closed:
            writer = Writer(path)
            _WRITERS[key] = writer
        return writer


def close_all_writers() -> None:
    with _LOCK:
        writers = list(_WRITERS.values())
        _WRITERS.clear()
    for writer in writers:
        writer.close()
