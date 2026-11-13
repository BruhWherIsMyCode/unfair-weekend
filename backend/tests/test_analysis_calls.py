"""Whole calls through the real HTTP/SSE stack and Agent 3's backend entry, with the REAL StreamSmile and StreamArms
classes. Only the per-frame readers are made up (no mediapipe, no opencv); the usual comes from a temporary baseline
store seeded through BaselineStore. The simulated phone runs at a fraction of real time but the session clock is real,
so the arms capture has to last longer than the 5 s swap cue (a slower sample pace in the first test)."""

import asyncio
import json

import httpx
import pytest

from checkin_backend.runner import ensure_analysis_path
from tests.client_sim import SimClient
from tests.test_call_service import KEY, Server, run_call

ensure_analysis_path()
agent_helpers = pytest.importorskip("analysis.tests.agent_helpers", reason="Agent 3's analysis package is not available")
backend_entry = pytest.importorskip("analysis.backend_entry")

from analysis.pc.store import BaselineStore  # noqa: E402
from analysis.tasks.stream_arms import StreamArms  # noqa: E402
from analysis.tasks.stream_smile import StreamSmile  # noqa: E402

PERSON = "p_demo"
SEED = ({"corner_height_v1": 0.0, "arms.hand_left.hand_sink": 0.0, "arms.hand_right.hand_sink": 0.0},
        {"corner_height_v1": 0.01, "arms.hand_left.hand_sink": 0.01, "arms.hand_right.hand_sink": 0.01})


class ScriptedSmile:
    """Made-up smile frames: mouth-corner height per attempt (the last value repeats). A new attempt starts at 0 ms."""

    def __init__(self, corners) -> None:
        self.corners, self.attempt, self.inner = list(corners), -1, agent_helpers.FakeSmile()

    def __call__(self, image, ts_ms):
        if ts_ms == 0:
            self.attempt += 1
            self.inner.corner = self.corners[min(self.attempt, len(self.corners) - 1)]
        return self.inner(image, ts_ms)


class ScriptedHands:
    """Made-up hand frames: both hands steady unless a sink is given. A new attempt starts when time goes back."""

    def __init__(self, sinks=({},)) -> None:
        self.sinks, self.attempt, self.last, self.inner = list(sinks), -1, None, agent_helpers.FakeHands()

    def __call__(self, image, ts_ms, side):
        if self.last is None or ts_ms < self.last:
            self.attempt += 1
            self.inner.sink.update({"left": 0.0, "right": 0.0})
            self.inner.sink.update(self.sinks[min(self.attempt, len(self.sinks) - 1)])
        self.last = ts_ms
        return self.inner(image, ts_ms, side)


def install(monkeypatch, tmp_path, corners=(0.01,), sinks=({},), seeded=True, label=None):
    data = tmp_path / "data"
    if seeded:
        store = BaselineStore(PERSON, data, 2)
        for values in SEED:
            assert store.append("normal", values) is True
    monkeypatch.setenv("UW_DATA_DIR", str(data))
    monkeypatch.setenv("UW_RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setenv("UW_BASELINE_NEEDED", "2")
    monkeypatch.setenv("CALL_DRIVER", "fixed")
    if label:
        monkeypatch.setenv("DEMO_SESSION_LABEL", label)

    def fresh_modules(mirrored):  # the real task classes, new per call, with made-up frame readers
        return {"smile": StreamSmile(ScriptedSmile(corners), agent_helpers.identity_decode, use_executor=False),
                "arms": StreamArms(ScriptedHands(sinks), agent_helpers.identity_decode, use_executor=False)}

    monkeypatch.setattr(backend_entry, "default_modules", fresh_modules)
    return data


def usual(data):
    return BaselineStore(PERSON, data, 2).load_baselines()


async def family_messages(server, count, out):
    async with httpx.AsyncClient(base_url=server.base, headers={"X-Demo-Key": KEY}, timeout=None) as http:
        await http.post("/v1/devices", json={"device_id": "dev_family", "role": "family", "family_id": "f_demo"})
        async with http.stream("GET", "/v1/devices/dev_family/events") as stream:
            event = None
            async for line in stream.aiter_lines():
                if line.startswith("event: "):
                    event = line[7:]
                elif line.startswith("data: ") and event == "message.new":
                    out.append(json.loads(line[6:]))
                    if len(out) >= count:
                        return


async def call_with_family(server, sim, count):
    seen = []
    feed = asyncio.create_task(family_messages(server, count, seen))
    await asyncio.sleep(0.1)
    status = await run_call(server, sim, timeout=90)
    await asyncio.wait_for(feed, 5)
    return status, seen


@pytest.mark.anyio
async def test_a_normal_call_says_swap_hands_during_the_arms_capture_is_as_usual_and_adds_her_values(monkeypatch, tmp_path):
    data = install(monkeypatch, tmp_path)
    async with Server("analysis") as server:
        sim = SimClient(base=server.base, key=KEY, time_scale=0.05)  # a sample every 0.1 s: arms lasts about 6 s
        status, family = await call_with_family(server, sim, 1)
    assert status["reason"] == "completed"
    assert status["summary"]["status"] == "as_usual"
    assert status["message"]["kind"] == "as_usual"
    assert "agent_remark" not in sim.said  # no learning introduction once all required checks are ready
    assert [m["phase"] for m in family] == ["final"]
    order = ["greet", "q_medicine", "q_feeling", "q_unusual", "setup_phone", "task_smile", "smile_now", "task_arms", "swap_hands", "bye"]
    positions = [sim.said.index(line) for line in order]
    assert positions == sorted(positions), sim.said
    assert "task_sentence" not in sim.said  # the sentence is not measured on the phone path
    starts = [d["command"] for t, d in sim.seen if t == "command.execute" and d["command"]["kind"] == "capture"
              and d["command"]["purpose"]["kind"] == "task"]
    assert [c["purpose"]["task"] for c in starts] == ["smile", "arms"]
    after = usual(data)
    assert after["smile"]["sessions"] == 3 and after["arms"]["sessions"] == 3
    record = next((tmp_path / "runs").glob("call_*.json"))
    assert json.loads(record.read_text("utf-8"))["summary"]["status"] == "as_usual"


@pytest.mark.anyio
async def test_two_outside_smiles_give_a_change_message_mid_call_that_stays_to_the_end(monkeypatch, tmp_path):
    from analysis.session import limits

    monkeypatch.setattr(limits, "CHANGE_ALERT_AT_END", False)  # the mid-call mode (the demo tells it at the end)
    data = install(monkeypatch, tmp_path, corners=(0.11, 0.11))
    async with Server("analysis") as server:
        sim = SimClient(base=server.base, key=KEY, time_scale=0.02)
        status, family = await call_with_family(server, sim, 2)
    assert [(m["phase"], m["kind"]) for m in family] == [("mid_call", "change_noticed"), ("final", "change_noticed")]
    assert family[0]["message_id"] == family[1]["message_id"]
    assert family[1]["text"].endswith("If she seems unwell, call 112.")
    assert status["summary"]["status"] == "change_noticed"
    assert status["message"]["kind"] == "change_noticed"
    after = usual(data)
    assert after["smile"]["sessions"] == 2 and after["arms"]["sessions"] == 2  # nothing was added


@pytest.mark.anyio
async def test_by_default_one_outside_smile_is_told_once_at_the_end(monkeypatch, tmp_path):
    # v12b demo defaults: one reading confirms, and the family hears it when the call finishes, not mid-call
    install(monkeypatch, tmp_path, corners=(0.11,))
    async with Server("analysis") as server:
        sim = SimClient(base=server.base, key=KEY, time_scale=0.02)
        status, family = await call_with_family(server, sim, 1)
    assert [(m["phase"], m["kind"]) for m in family] == [("final", "change_noticed")]
    assert family[0]["text"].endswith("If she seems unwell, call 112.")
    assert status["summary"]["status"] == "change_noticed"


@pytest.mark.anyio
async def test_a_call_labelled_acted_adds_nothing_to_her_usual(monkeypatch, tmp_path):
    data = install(monkeypatch, tmp_path, label="acted")
    async with Server("analysis") as server:
        sim = SimClient(base=server.base, key=KEY, time_scale=0.02)
        status = await run_call(server, sim, timeout=90)
    assert status["summary"]["label"] == "acted"
    assert status["summary"]["status"] == "as_usual"
    after = usual(data)
    assert after["smile"]["sessions"] == 2 and after["arms"]["sessions"] == 2


@pytest.mark.anyio
async def test_a_learning_usual_gives_a_learning_message_and_collects_the_first_values(monkeypatch, tmp_path):
    data = install(monkeypatch, tmp_path, seeded=False)
    async with Server("analysis") as server:
        sim = SimClient(base=server.base, key=KEY, time_scale=0.02)
        status = await run_call(server, sim, timeout=90)
    assert status["summary"]["status"] == "learning"
    assert status["message"]["kind"] == "learning"
    assert usual(data)["smile"]["sessions"] == 1
    assert sim.said[:3] == ["greet", "agent_remark", "q_medicine"]
    assert "learn what is usual for you" in sim.said_commands[1]["text"]
    assert "2 successful check-in calls in total" in sim.said_commands[1]["text"]
    assert "2 successful calls are still needed" in sim.said_commands[1]["text"]


@pytest.mark.anyio
async def test_two_fresh_calls_train_and_the_third_can_send_a_change_alert(monkeypatch, tmp_path):
    corners = [0.01]
    data = install(monkeypatch, tmp_path, seeded=False, corners=corners)
    monkeypatch.delenv("UW_BASELINE_NEEDED")  # exercise the backend's real default, not a test override
    async with Server("analysis") as server:
        for remaining in (2, 1):
            sim = SimClient(base=server.base, key=KEY, time_scale=0.05)
            status = await run_call(server, sim, timeout=90)
            assert status["summary"]["status"] == "learning"
            assert status["summary"]["baseline_needed"] == 2
            assert sim.said[:3] == ["greet", "agent_remark", "q_medicine"]
            assert f"Including this one, {remaining} successful call" in sim.said_commands[1]["text"]
        assert usual(data)["smile"]["ready"] and usual(data)["arms"]["ready"]
        corners[:] = [0.11]
        sim = SimClient(base=server.base, key=KEY, time_scale=0.05)
        status = await run_call(server, sim, timeout=90)
    assert status["summary"]["status"] == "change_noticed"
    assert status["message"]["kind"] == "change_noticed"
    assert status["message"]["text"].endswith("If she seems unwell, call 112.")
    assert sim.said[:2] == ["greet", "q_medicine"]
    assert usual(data)["smile"]["sessions"] == usual(data)["arms"]["sessions"] == 2


@pytest.mark.anyio
async def test_existing_database_uses_two_call_default_without_rewriting_old_baselines(monkeypatch, tmp_path):
    """Calls collected before the two-call default already contain enough measurements to stop learning."""
    from contextlib import closing
    import sqlite3

    from analysis.storage import pending_retries

    install(monkeypatch, tmp_path, seeded=False, label="normal")
    monkeypatch.setenv("UW_BASELINE_NEEDED", "5")
    database = tmp_path / "shared.sqlite3"

    def snapshots():
        with closing(sqlite3.connect(database)) as db:
            db.row_factory = sqlite3.Row
            return {row["snapshot_id"]: dict(row) for row in db.execute(
                "SELECT * FROM baseline_snapshots WHERE sessions > 0")}

    async with Server("analysis", database_path=str(database)) as server:
        async def finish_writes():
            async with asyncio.timeout(5):
                while True:
                    await server.app.state.store.flush()
                    if pending_retries() == 0:
                        await server.app.state.store.flush()
                        assert server.app.state.store.failures == 0
                        return
                    await asyncio.sleep(0.02)

        for _ in range(2):
            sim = SimClient(base=server.base, key=KEY, time_scale=0.05)
            status = await run_call(server, sim, timeout=90)
            assert status["reason"] == "completed"
            assert status["summary"]["status"] == "learning"
            assert status["summary"]["baseline_needed"] == 5
            await finish_writes()

        historical = snapshots()
        assert sorted((row["sessions"], row["needed"]) for row in historical.values()) == [
            (1, 5), (1, 5), (2, 5), (2, 5)]

        monkeypatch.delenv("UW_BASELINE_NEEDED")  # use the backend's current default of two calls
        sim = SimClient(base=server.base, key=KEY, time_scale=0.05)
        status = await run_call(server, sim, timeout=90)
        await finish_writes()

    after = snapshots()
    assert {key: after[key] for key in historical} == historical
    assert "agent_remark" not in sim.said, sim.said_commands
    assert status["summary"]["status"] == "as_usual"
    assert status["summary"]["baseline_needed"] == 2


@pytest.mark.anyio
async def test_with_a_database_path_the_call_is_recorded_through_the_call_service_store(monkeypatch, tmp_path):
    """Since main has Agent 2's Store (PR #9/#17), the answered call has its `calls` row and Agent 3's recorder writes
    through the same writer thread: the session, attempts and results are recorded, summaries stay the Store's (once)."""
    from contextlib import closing
    import sqlite3

    from analysis.storage import close_all_writers, shared_writer

    data = install(monkeypatch, tmp_path)
    database = tmp_path / "db" / "shared.sqlite3"
    try:
        async with Server("analysis", database_path=str(database)) as server:
            sim = SimClient(base=server.base, key=KEY, time_scale=0.05)
            status, family = await call_with_family(server, sim, 1)
        await asyncio.to_thread(shared_writer(database).flush)
    finally:
        close_all_writers()
    assert status["reason"] == "completed" and status["summary"]["status"] == "as_usual"
    assert status["message"]["kind"] == "as_usual" and [m["phase"] for m in family] == ["final"]
    assert usual(data)["smile"]["sessions"] == 3 and usual(data)["arms"]["sessions"] == 3  # the JSON store still learns
    with closing(sqlite3.connect(database)) as db:
        count = lambda table: db.execute("SELECT count(*) FROM %s" % table).fetchone()[0]  # noqa: E731
        counts = {t: count(t) for t in ("calls", "call_sessions", "task_attempts", "task_results", "model_turns",
                                         "session_summaries", "family_messages")}
        print("recorded rows:", counts)
        assert counts["calls"] == 1 and counts["call_sessions"] == 1
        assert counts["task_attempts"] >= 2 and counts["task_results"] == counts["task_attempts"]
        models = {r[0] for r in db.execute("SELECT DISTINCT model_id FROM model_turns")}
        assert counts["model_turns"] >= 1 and models == {"fixed_order"}  # the fixed driver's steps, no model
        assert counts["family_messages"] == 1 and counts["session_summaries"] == 1  # written once, by the Store


@pytest.mark.anyio
async def test_without_a_database_path_no_database_file_is_made(monkeypatch, tmp_path):
    install(monkeypatch, tmp_path)
    monkeypatch.delenv("DATABASE_PATH", raising=False)
    async with Server("analysis") as server:
        sim = SimClient(base=server.base, key=KEY, time_scale=0.02)
        status = await run_call(server, sim, timeout=90)
    assert status["summary"]["status"] == "as_usual"
    assert not list(tmp_path.rglob("*.sqlite3"))


@pytest.mark.anyio
async def test_a_bad_setting_ends_the_call_with_an_incomplete_family_message(monkeypatch, tmp_path):
    install(monkeypatch, tmp_path)
    monkeypatch.setenv("UW_BASELINE_NEEDED", "zero")
    async with Server("analysis") as server:
        sim = SimClient(base=server.base, key=KEY, time_scale=0.02)
        status = await run_call(server, sim, timeout=90)
    assert status["message"]["kind"] == "incomplete"
    assert sim.said == []
