"""The call service's durable records in the shared SQLite schema (checkin_backend/store.py).

Full calls run through a real Uvicorn server and the HTTP/SSE protocol (scripted runner, simulated parent
client), then the rows are checked against what happened and against the schema's own constraints.
"""

import asyncio
from contextlib import closing

import httpx
import pytest

from checkin_backend.people import Person
from checkin_backend.store import Store
from storage import apply_migrations, connect_database
from tests.client_sim import SimClient
from tests.test_call_service import KEY, Server, run_call

ONA = Person(person_id="p_demo", name="Ona", pronoun="she", family_id="f_demo")


def rows(path, sql, *args):
    with closing(connect_database(path)) as db:
        return [dict(row) for row in db.execute(sql, args).fetchall()]


def assert_consistent(path):
    with closing(connect_database(path)) as db:
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        for (call_id,) in db.execute("SELECT call_id FROM calls").fetchall():
            positions = [r[0] for r in db.execute(
                "SELECT position FROM call_events WHERE call_id = ? ORDER BY position", (call_id,))]
            assert positions == list(range(len(positions)))


async def stored_server_call(path, family: bool = True, **sim_options) -> tuple[str, dict]:
    async with Server("scripted", database_path=str(path)) as server:
        async with httpx.AsyncClient(base_url=server.base, headers={"X-Demo-Key": KEY}) as http:
            if family:
                r = await http.post("/v1/devices", json={"device_id": "dev_family", "role": "family",
                                                         "family_id": "f_demo"})
                assert r.status_code == 204
        sim = SimClient(base=server.base, key=KEY, **sim_options)
        status = await run_call(server, sim)
        store = server.app.state.store
        await store.flush()
        assert store.failures == 0
    return status["call_id"], status


@pytest.mark.anyio
async def test_whole_call_is_stored_and_survives_a_restart(tmp_path):
    path = tmp_path / "checkin.sqlite3"
    call_id, status = await stored_server_call(path)
    assert status["reason"] == "completed"

    [call] = rows(path, "SELECT * FROM calls")
    assert (call["call_id"], call["state"], call["end_reason"]) == (call_id, "ended", "completed")
    assert call["parent_device_id"] == "dev_parent" and call["label"] == "live"
    assert call["answered_at_ms"] is not None and call["ended_at_ms"] >= call["answered_at_ms"]

    devices = {r["device_id"]: r for r in rows(path, "SELECT * FROM devices")}
    assert devices["dev_parent"]["role"] == "parent" and devices["dev_parent"]["family_id"] == "f_demo"
    assert devices["dev_family"]["role"] == "family" and devices["dev_family"]["person_id"] is None

    types = [r["event_type"] for r in rows(path, "SELECT event_type FROM call_events ORDER BY position")]
    assert types[0] == "call.incoming" and types[-1] == "call.ended"
    assert {"call.answered", "client.ready", "command.execute", "playback.completed", "capture.started"} <= set(types)

    commands = rows(path, "SELECT kind, state, ended_at_ms, sent_at_ms FROM client_commands")
    assert commands and all(c["ended_at_ms"] is not None and c["sent_at_ms"] is not None for c in commands)
    says = [c for c in commands if c["kind"] == "say"]
    assert says and all(c["state"] == "completed" for c in says)
    captures = [c for c in commands if c["kind"] == "capture"]
    assert captures and all(c["state"] in ("completed", "cancelled") for c in captures)

    [message] = rows(path, "SELECT * FROM family_messages")
    assert message["kind"] == "incomplete" and message["text"].endswith("If she seems unwell, call 112.")
    assert message["message_id"] == status["message"]["message_id"]
    [summary] = rows(path, "SELECT * FROM session_summaries")
    assert (summary["phase"], summary["status"], summary["revision"]) == ("final", "incomplete", 1)

    outbox = rows(path, "SELECT purpose, state, attempts FROM notification_outbox ORDER BY purpose")
    assert [(o["purpose"], o["state"], o["attempts"]) for o in outbox] == [
        ("family_message", "accepted", 1), ("incoming_call", "accepted", 1)]
    assert len(rows(path, "SELECT * FROM delivery_attempts WHERE state = 'accepted'")) == 2
    assert_consistent(path)

    # A new process reads the same history.
    async with Server("scripted", database_path=str(path)) as server:
        async with httpx.AsyncClient(base_url=server.base, headers={"X-Demo-Key": KEY}) as http:
            feed = (await http.get("/v1/families/f_demo/messages")).json()
            stored = (await http.get(f"/v1/calls/{call_id}")).json()
            missing = await http.get("/v1/calls/call_nope")
    assert [m["message_id"] for m in feed] == [message["message_id"]]
    assert feed[0]["phase"] == "final" and feed[0]["person_id"] == "p_demo"
    assert (stored["state"], stored["reason"], stored["stored"]) == ("ended", "completed", True)
    assert stored["message"]["kind"] == "incomplete" and stored["log"]
    assert missing.status_code == 404


@pytest.mark.anyio
async def test_hang_up_cancels_open_commands(tmp_path):
    path = tmp_path / "checkin.sqlite3"
    _, status = await stored_server_call(path, family=False, hang_up_after="q_feeling")
    assert status["reason"] == "hangup"
    [call] = rows(path, "SELECT state, end_reason FROM calls")
    assert (call["state"], call["end_reason"]) == ("ended", "hangup")
    assert rows(path, "SELECT * FROM client_commands WHERE ended_at_ms IS NULL") == []
    assert "hangup" in [r["event_type"] for r in rows(path, "SELECT event_type FROM call_events")]
    assert_consistent(path)


@pytest.mark.anyio
async def test_decline_and_ring_timeout_are_stored_as_no_answer(tmp_path):
    path = tmp_path / "checkin.sqlite3"
    async with Server("scripted", ring_timeout_s=0.3, database_path=str(path)) as server:
        async with httpx.AsyncClient(base_url=server.base, headers={"X-Demo-Key": KEY}) as http:
            await http.post("/v1/devices", json={"device_id": "d1", "role": "parent", "person_id": "p_demo"})
            declined = (await http.post("/v1/calls", json={"person_id": "p_demo"})).json()["call_id"]
            assert (await http.post(f"/v1/calls/{declined}/decline")).status_code == 204
            await asyncio.sleep(0.05)
            timed_out = (await http.post("/v1/calls", json={"person_id": "p_demo"})).json()["call_id"]
            await asyncio.sleep(0.5)
        await server.app.state.store.flush()
        assert server.app.state.store.failures == 0
    calls = {r["call_id"]: r for r in rows(path, "SELECT * FROM calls")}
    assert (calls[declined]["state"], calls[declined]["end_reason"]) == ("no_answer", "declined")
    assert (calls[timed_out]["state"], calls[timed_out]["end_reason"]) == ("no_answer", "ring_timeout")
    kinds = [r["kind"] for r in rows(path, "SELECT kind FROM family_messages")]
    assert kinds == ["no_answer", "no_answer"]
    assert_consistent(path)


@pytest.mark.anyio
async def test_open_call_left_by_a_crash_is_closed_at_startup(tmp_path):
    path = tmp_path / "checkin.sqlite3"
    with closing(connect_database(path)) as db:
        apply_migrations(db)
        db.execute("BEGIN IMMEDIATE")
        db.execute("INSERT INTO families VALUES ('f_demo', 1)")
        db.execute("INSERT INTO persons VALUES ('p_demo', 'f_demo', 'Ona', 'en', 1)")
        db.execute("INSERT INTO devices VALUES ('d1', 'f_demo', 'p_demo', 'parent', 1, 1)")
        db.execute("INSERT INTO calls (call_id, person_id, parent_device_id, label, state, created_at_ms, "
                   "ring_deadline_at_ms) VALUES ('call_old', 'p_demo', 'd1', 'live', 'ringing', 1, 2)")
        db.commit()

    store = Store(path)
    store.open([ONA])
    try:
        [call] = rows(path, "SELECT state, end_reason FROM calls")
        assert (call["state"], call["end_reason"]) == ("ended", "shutdown")
        # The person can be called again: the one-active-call index is free.
        store.record_call_started("call_new", ONA, ["d1"], 30, {"event_id": "ring_call_new"})
        await store.flush()
        assert store.failures == 0
        assert rows(path, "SELECT state FROM calls WHERE call_id = 'call_new'") == [{"state": "ringing"}]
    finally:
        store.close()
    # Shutdown closes what this process left open, too.
    assert rows(path, "SELECT end_reason FROM calls WHERE call_id = 'call_new'") == [{"end_reason": "shutdown"}]
    assert_consistent(path)


@pytest.mark.anyio
async def test_persistence_off_keeps_the_in_memory_feed(tmp_path):
    async with Server("scripted", database_path="") as server:
        assert server.app.state.store.enabled is False
        async with httpx.AsyncClient(base_url=server.base, headers={"X-Demo-Key": KEY}) as http:
            assert (await http.get("/v1/families/f_demo/messages")).json() == []
    assert list(tmp_path.iterdir()) == []
