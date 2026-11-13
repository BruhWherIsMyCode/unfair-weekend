"""Small shared pieces of the storage package: errors, JSON, ids, time, the transaction helper and access to the
backend's `storage` package (migration tool and `connect_database`, which this package uses and never edits)."""
from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any, Callable, Optional, TypeVar

T = TypeVar("T")
REPO_ROOT = Path(__file__).resolve().parents[2]


class StorageError(Exception):
    """A write the shared schema or its rules refuse."""


class ConflictError(StorageError):
    """The same identity was written again with different content (never overwritten)."""


class StaleWrite(StorageError):
    """A compare-and-update found the row changed (revision or context moved on); nothing was written."""


class NotReady(StorageError):
    """A precondition owned by another writer (for example the call row ending) is not true yet; retry later."""


def now_ms() -> int:
    return time.time_ns() // 1_000_000


def dumps(value: Any) -> str:
    """Canonical JSON for the JSON columns (sorted keys, no spaces)."""
    return json.dumps(value, separators=(",", ":"), sort_keys=True, default=str)


def stored_id(call_id: str, wire_id: str) -> str:
    """Wire ids (attempts, events) are only unique per call; the database ids are global: '<call>.<wire id>'."""
    return "%s.%s" % (call_id, wire_id)


def fingerprint(value: Any, length: int = 8) -> str:
    return hashlib.sha1(dumps(value).encode("utf-8")).hexdigest()[:length]


def backend_storage() -> Any:
    """The backend's `storage` module (`connect_database`, `apply_migrations`).

    Inside the backend it is already importable as `storage`; from the repository root it is loaded from
    `backend/storage/__init__.py` under a private module name. Never edited or copied."""
    module = sys.modules.get("storage")
    if module is None:
        try:
            module = importlib.import_module("storage")
        except ImportError:
            module = None
    if module is not None and hasattr(module, "apply_migrations") and hasattr(module, "connect_database"):
        return module
    loaded = sys.modules.get("uw_backend_storage")
    if loaded is not None:
        return loaded
    path = REPO_ROOT / "backend" / "storage" / "__init__.py"
    spec = importlib.util.spec_from_file_location("uw_backend_storage", path)
    if spec is None or spec.loader is None:
        raise ImportError("backend/storage is not available")
    loaded = importlib.util.module_from_spec(spec)
    sys.modules["uw_backend_storage"] = loaded
    spec.loader.exec_module(loaded)
    return loaded


def connect(path: Any) -> sqlite3.Connection:
    return backend_storage().connect_database(path)


def transact(db: sqlite3.Connection, fn: Callable[[sqlite3.Connection], T]) -> T:
    """One transaction: BEGIN IMMEDIATE, the body, commit; rollback on any error (deferred checks run at commit)."""
    db.execute("BEGIN IMMEDIATE")
    try:
        result = fn(db)
        db.commit()
        return result
    except BaseException:
        db.rollback()
        raise


def one(db: sqlite3.Connection, sql: str, params: tuple = ()) -> Optional[sqlite3.Row]:
    return db.execute(sql, params).fetchone()
