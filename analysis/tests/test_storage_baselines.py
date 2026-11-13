"""Her usual in the database: read at call start, frozen per call, learned after an eligible normal call. Whole calls
through analysis.backend_entry.run_backend_call on a fake link (the same rig as test_backend_entry), a real migrated
SQLite file, the single writer thread of this package. No mediapipe, no network, no Claude."""
from __future__ import annotations

import asyncio
import json

import pytest

from analysis import backend_entry
from analysis.pc.store import BaselineStore
from analysis.storage import close_all_writers, shared_writer
from analysis.tests.storage_helpers import (PERSON, add_call, count, end_call, make_db, rows, settle)
from analysis.tests.test_backend_entry import SEED, Link, modules


@pytest.fixture()
def world(tmp_path):
    path = make_db(tmp_path)
    yield tmp_path, path
    close_all_writers()


def env_for(tmp_path, path, needed=2, **extra):
    env = {"UW_DATA_DIR": str(tmp_path / "data"), "UW_RUNS_DIR": str(tmp_path / "runs"),
           "UW_BASELINE_NEEDED": str(needed), "CALL_DRIVER": "fixed", "DATABASE_PATH": str(path)}
    env.update(extra)
    return env


def call(world, n, *, label="live", corners=(0.01,), sinks=({},), end=True, **extra):
    """One whole call number n; returns (summary, live log lines). The call is ended in the database afterwards,
    which is what Agent 2's call service does once the driver returned."""
    tmp_path, path = world
    call_id = "call_t%d" % n
    add_call(path, call_id, label)
    link, lines = Link(label), []
    link.call_id = call_id
    summary = asyncio.run(backend_entry.run_backend_call(
        link, modules(corners, sinks), env=env_for(tmp_path, path, **extra), out=lines.append))
    if end:
        end_call(path, call_id)
        settle(shared_writer(path))
    else:
        assert shared_writer(path).flush()  # learning is waiting for the call to end: only flush
    return summary, lines


def snapshots(path, task):
    return rows(path, "SELECT s.snapshot_id, s.sessions, s.needed, s.ready, s.usual_low, s.usual_high FROM "
                      "baseline_snapshots s JOIN task_definitions d USING (definition_id) WHERE d.task = ? "
                      "AND s.sessions > 0 ORDER BY s.created_at_ms, s.rowid", (task,))


def test_the_usual_becomes_ready_after_the_needed_number_of_eligible_normal_calls(world):
    tmp_path, path = world
    summary1, lines1 = call(world, 1)
    assert summary1["status"] == "learning"
    assert any("json file" in line and "no usual for this person yet" in line for line in lines1)  # said once
    assert [s["sessions"] for s in snapshots(path, "smile")] == [1] and snapshots(path, "smile")[0]["ready"] == 0

    summary2, lines2 = call(world, 2)
    assert summary2["status"] == "learning"  # still one value short when the call started
    assert any("baselines: database for arms, smile" in line for line in lines2)
    smile = snapshots(path, "smile")
    assert [(s["sessions"], s["ready"]) for s in smile] == [(1, 0), (2, 1)]
    assert smile[1]["usual_low"] is not None and smile[1]["usual_low"] <= 0.01 <= smile[1]["usual_high"]

    summary3, _ = call(world, 3)
    assert summary3["status"] == "as_usual"  # now ready, from the database
    # the snapshot this call used is frozen and untouched; the learned one is NEW
    used = rows(path, "SELECT task, snapshot_id FROM session_baselines WHERE call_id = 'call_t3' ORDER BY task")
    assert used == [{"task": "arms", "snapshot_id": "snap_call_t2_arms"},
                    {"task": "smile", "snapshot_id": "snap_call_t2_smile"}]
    assert snapshots(path, "smile")[1] == smile[1]
    newest = snapshots(path, "smile")[2]
    assert newest["snapshot_id"] == "snap_call_t3_smile" and newest["sessions"] == 3
    members = rows(path, "SELECT source_call_id FROM baseline_members WHERE snapshot_id = 'snap_call_t3_smile' "
                         "ORDER BY rowid")
    assert [m["source_call_id"] for m in members] == ["call_t1", "call_t2", "call_t3"]

    # call 3 compared with the frozen smile snapshot: its bounds are in the result, inside her usual
    result = rows(path, "SELECT r.value, r.usual_low, r.usual_high, r.outside FROM task_results r "
                        "JOIN task_attempts a USING (attempt_id) WHERE a.call_id = 'call_t3' AND a.task = 'smile'")[0]
    assert (result["usual_low"], result["usual_high"], result["outside"]) == (smile[1]["usual_low"],
                                                                              smile[1]["usual_high"], 0)
    # arms: the schema keeps one range per snapshot, so the two hands' comparison is not stored (README, For Agent 2)
    arms = rows(path, "SELECT r.value, r.usual_low, r.outside FROM task_results r "
                      "JOIN task_attempts a USING (attempt_id) WHERE a.call_id = 'call_t3' AND a.task = 'arms'")[0]
    assert arms["value"] is not None and arms["usual_low"] is None and arms["outside"] is None
    assert snapshots(path, "arms")[1]["ready"] == 0 and snapshots(path, "arms")[1]["sessions"] == 2


def test_the_database_wins_over_the_json_file_where_it_has_a_snapshot(world):
    tmp_path, path = world
    for values in SEED:  # a READY usual in the json file ...
        BaselineStore(PERSON, tmp_path / "data", 2).append("normal", values)
    summary1, lines1 = call(world, 1)
    assert summary1["status"] == "as_usual" and any("json file" in line for line in lines1)
    summary2, lines2 = call(world, 2)  # ... the database now has one session of its own and takes over (learning)
    assert any("database for" in line for line in lines2) and summary2["status"] == "learning"


def test_the_call_record_says_where_her_usual_came_from(world):
    tmp_path, _ = world
    call(world, 1)
    record = json.loads((tmp_path / "runs" / "call_t1.json").read_text("utf-8"))
    assert any(e["kind"] == "baseline_source" for e in record["session_log"])


@pytest.mark.parametrize("label,corners", [("acted", (0.01,)), ("live", (0.11, 0.01)), ("live", (0.11, 0.11))])
def test_acted_outside_or_changed_calls_add_nothing_to_her_usual(world, label, corners):
    tmp_path, path = world
    for values in SEED:
        BaselineStore(PERSON, tmp_path / "data", 2).append("normal", values)  # json: ready, inside 0.0 .. 0.01
    call(world, 1, label=label, corners=corners)
    assert snapshots(path, "smile") == [] and count(path, "baseline_members") == 0
    assert count(path, "task_results") == 4 or count(path, "task_results") >= 2


def test_learning_is_deferred_until_agent_2_has_ended_the_call(world):
    tmp_path, path = world
    summary, _ = call(world, 1, end=False)
    assert summary["status"] == "learning" and snapshots(path, "smile") == []  # the call is still 'connected'
    end_call(path, "call_t1")
    settle(shared_writer(path))
    assert [s["sessions"] for s in snapshots(path, "smile")] == [1]


def test_without_a_database_nothing_changes(world, tmp_path):
    _, path = world
    link, lines = Link(), []
    env = env_for(tmp_path, path)
    env["DATABASE_PATH"] = ""
    summary = asyncio.run(backend_entry.run_backend_call(link, modules(), env=env, out=lines.append))
    assert summary["status"] == "learning"
    assert count(path, "call_sessions") == 0 and not any("baselines:" in line for line in lines)


def test_an_unreadable_database_falls_back_to_the_json_file(tmp_path, caplog):
    link, lines = Link(), []
    env = {"UW_DATA_DIR": str(tmp_path / "data"), "UW_RUNS_DIR": str(tmp_path / "runs"), "UW_BASELINE_NEEDED": "2",
           "CALL_DRIVER": "fixed", "DATABASE_PATH": str(tmp_path / "not_migrated.sqlite3")}  # an empty file: no tables
    try:
        with caplog.at_level("WARNING"):
            summary = asyncio.run(backend_entry.run_backend_call(link, modules(), env=env, out=lines.append))
            settle(shared_writer(tmp_path / "not_migrated.sqlite3"))
    finally:
        close_all_writers()
    assert summary["status"] == "learning"  # the call itself is unchanged
    assert any("cannot be read" in r.getMessage() or "recording is off" in r.getMessage() for r in caplog.records)
    assert BaselineStore(PERSON, tmp_path / "data", 2).load_baselines()["smile"]["sessions"] == 1  # json still learns
