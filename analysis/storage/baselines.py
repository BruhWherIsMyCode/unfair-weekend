"""Her usual, read from the database at call start.

`read_usual` runs on its OWN read connection (never inside a writer job, never on the event loop's connection) and
returns, per task, the dict shape `analysis.pc.store.BaselineStore.load_baselines()` gives a CallSession:

    smile     make_baseline-like dict (+ snapshot_id)
    arms      {"snapshot_id", "sessions", "needed", "ready", "hand_left": ..., "hand_right": ...}
    sentence  like smile (seconds)

plus the snapshot id the call will freeze. A task with no real snapshot (sessions = 0 is only a placeholder) is not in
the result; `choose` then keeps the JSON baseline for it and says so in one line.
"""
from __future__ import annotations

import sqlite3
from contextlib import closing
from typing import Any, Dict, List, Optional, Sequence, Tuple

from analysis import baseline as baseline_mod
from analysis.session import limits
from analysis.storage import repo
from analysis.storage.util import connect


def _smile_like(row: sqlite3.Row, snapshot_id: str) -> Dict[str, Any]:
    ready = bool(row["ready"])
    return {"ready": ready, "status": "ready" if ready else "learning", "sessions": row["sessions"],
            "needed": row["needed"], "median": None, "spread": None, "usual_low": row["usual_low"],
            "usual_high": row["usual_high"], "snapshot_id": snapshot_id}


def _arms(db: sqlite3.Connection, row: sqlite3.Row, snapshot_id: str) -> Optional[Dict[str, Any]]:
    members = [m["source_attempt_id"] for m in repo.snapshot_members(db, snapshot_id)]
    values = repo.member_values(db, "arms", members)
    if len(values) != row["sessions"] or any(v["left"] is None or v["right"] is None for v in values):
        return None  # members without their per-hand values cannot give a usual range
    needed = row["needed"]
    left = baseline_mod.make_baseline([v["left"] for v in values], metric="hand_sink", needed=needed)
    right = baseline_mod.make_baseline([v["right"] for v in values], metric="hand_sink", needed=needed)
    return {"snapshot_id": snapshot_id, "sessions": len(values), "needed": needed,
            "ready": bool(left["ready"] and right["ready"]), "hand_left": left, "hand_right": right}


def read_usual(database_path: Any, person_id: str, rows: Dict[str, Dict[str, Any]]
               ) -> Dict[str, Tuple[str, Dict[str, Any]]]:
    """task -> (snapshot_id, baseline dict) for every task that has a real snapshot of the person. `rows` is
    `definitions.task_rows(modules)`."""
    out: Dict[str, Tuple[str, Dict[str, Any]]] = {}
    with closing(connect(database_path)) as db:
        for task, row in rows.items():
            snap = repo.latest_snapshot(db, person_id, row["definition_id"], real_only=True)
            if snap is None:
                continue
            snapshot_id = snap["snapshot_id"]
            usual = _arms(db, snap, snapshot_id) if task == "arms" else _smile_like(snap, snapshot_id)
            if usual is not None:
                out[task] = (snapshot_id, usual)
    return out


def choose(json_baselines: Dict[str, Dict[str, Any]], db_usual: Dict[str, Tuple[str, Dict[str, Any]]],
           database_on: bool, tasks: Sequence[str] = ()) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, str],
                                                                  Optional[str]]:
    """(baselines for the CallSession, task -> database snapshot id to freeze, the one log line or None).

    The database wins for every task that has a real snapshot; the JSON baseline is the fallback and the only
    source when the database is off or knows nothing about her yet."""
    if not database_on:
        return json_baselines, {}, None
    if not db_usual:
        return json_baselines, {}, "baselines: json file (the database has no usual for this person yet)"
    merged = dict(json_baselines)
    frozen: Dict[str, str] = {}
    for task, (snapshot_id, usual) in db_usual.items():
        merged[task] = usual
        frozen[task] = snapshot_id
    from_json: List[str] = [t for t in tasks if t not in db_usual]
    line = "baselines: database for %s" % ", ".join(sorted(db_usual))
    if from_json:
        line += "; json file for %s" % ", ".join(sorted(from_json))
    return merged, frozen, line
