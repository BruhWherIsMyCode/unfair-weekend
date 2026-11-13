"""Whole calls: the server logic and a scripted fake client over MemoryTransport, FakeClock, fake readings."""
from __future__ import annotations

import pytest

import asyncio
import json

from analysis.pc import link
from analysis.pc.demo_link import ANSWERS, ScriptedClient, seed_store
from analysis.pc.server import PcServer
from analysis.pc.store import BaselineStore
from analysis.session.clock import FakeClock


async def play(tmp_path, seed=True, floor=True, label="live", calls=({},)):
    clock, out = FakeClock(), []
    server = PcServer("t", "Ona", "off", floor, label, tmp_path / "data", tmp_path / "runs", clock, out.append)
    if seed:
        seed_store(server.store)
    s, c = link.MemoryTransport.pair()
    task = asyncio.ensure_future(server.handle_connection(s))
    client = ScriptedClient(c, clock)
    await client.connect()
    results = []
    for kw in calls:
        results.append(await client.run_call(**kw))
    await client.close()
    await asyncio.wait_for(task, 5)
    return server, client, results, out


def record(tmp_path, result):
    return json.loads((tmp_path / "runs" / ("%s.json" % result["call_id"])).read_text("utf-8"))


def kinds(rec, kind):
    return [e for e in rec["session_log"] if e["kind"] == kind]


def test_full_call_with_a_ready_baseline_ends_as_usual(tmp_path):
    server, client, [result], out = asyncio.run(play(tmp_path))
    s = result["summary"]
    assert s["status"] == "as_usual" and s["baseline_sessions"] == 6
    assert [x["sign"] for x in s["signs"]] == ["face", "arm"]
    assert result["family_text"] == "Ona's check-in today looked as usual."
    assert result["call_id"] and result["call_id"].startswith("call_")
    rec = record(tmp_path, result)
    assert rec["summary"] == s and rec["family_text"] == result["family_text"] and rec["session_log"]
    assert "started_at" in rec["timings"]
    log = (tmp_path / "runs" / "family_messages.log").read_text("utf-8")
    assert "final" in log and "mid_call" not in log
    types = [m["type"] for m in client.received]
    assert types[0] == "hello_ok" and types[-1] == "call_result"
    assert any("FAMILY MESSAGE (final)" in line for line in out)


def test_full_call_without_a_baseline_is_learning_and_stores_the_values(tmp_path):
    server, client, [result], out = asyncio.run(play(tmp_path, seed=False))
    assert result["summary"]["status"] == "learning" and result["summary"]["baseline_sessions"] == 0
    stored = json.loads(server.store.path.read_text("utf-8"))["sessions"]
    assert len(stored) == 1 and set(stored[0]["values"]) == {
        "corner_height_v1", "arms.hand_left.hand_sink", "arms.hand_right.hand_sink"}


def test_five_calls_make_the_baseline_ready(tmp_path):
    async def scenario():
        statuses = []
        for _ in range(6):
            _, _, [result], _ = await play(tmp_path, seed=False)
            statuses.append(result["summary"]["status"])
        return statuses

    statuses = asyncio.run(scenario())
    assert statuses[:5] == ["learning"] * 5 and statuses[5] == "as_usual"


@pytest.mark.usefixtures("alert_mid_call")
def test_left_hand_sinking_twice_is_published_mid_call(tmp_path):
    server, client, [result], out = asyncio.run(play(tmp_path, calls=({"sinks": ({"left": 0.4},)},)))
    assert result["summary"]["status"] == "change_noticed"
    assert result["summary"]["signs"][1]["side"] == "left" and result["summary"]["signs"][1]["confirmed"] is True
    phases = [line.split("\t")[2] for line in (tmp_path / "runs" / "family_messages.log").read_text().splitlines()]
    assert phases == ["mid_call", "final"]
    rec = record(tmp_path, result)
    assert len(kinds(rec, "change_confirmed")) == 1
    published = kinds(rec, "published")
    assert [p["phase"] for p in published] == ["mid_call", "final"] and len({p["message_id"] for p in published}) == 1
    assert "call 112" in result["family_text"]
    assert len(json.loads(server.store.path.read_text())["sessions"]) == 6  # a confirmed change is not stored


def test_camera_blocked_in_arms_takes_the_recovery_path(tmp_path):
    server, client, [result], out = asyncio.run(play(tmp_path, calls=({"block_camera_in_first_arms": True},)))
    rec = record(tmp_path, result)
    assert result["summary"]["status"] == "as_usual"
    assert any(e["kind"] == "attempt_finished" and e["outcome"] == "interrupted" for e in rec["session_log"])
    assert [e["result"] for e in kinds(rec, "recovery")] == ["restored"]
    assert [e["resource"] + ":" + e["state"] for e in kinds(rec, "resource_changed")] == [
        "camera:unavailable", "camera:available"]
    types = [m["type"] for m in client.received]
    assert "cancel_capture" in types and any(m.get("line_id") == "camera_unavailable" for m in client.received)
    assert [m["task"] for m in client.received if m["type"] == "start_capture"] == ["smile", "arms", "arms"]


def test_safety_floor_off_does_not_publish_mid_call_and_says_so(tmp_path):
    server, client, [result], out = asyncio.run(
        play(tmp_path, floor=False, calls=({"sinks": ({"left": 0.4},)},)))
    assert result["summary"]["status"] != "change_noticed"
    assert result["summary"]["signs"][1]["confirmed"] is True  # the sign is still kept
    rec = record(tmp_path, result)
    assert kinds(rec, "floor_off_suppressed") and not kinds(rec, "change_confirmed")
    assert [p["phase"] for p in kinds(rec, "published")] == ["final"]
    assert rec["safety_floor"] is False


def test_acted_calls_never_store_baseline_values(tmp_path):
    server, client, [result], out = asyncio.run(play(tmp_path, label="acted"))
    assert result["summary"]["label"] == "acted"
    assert len(json.loads(server.store.path.read_text())["sessions"]) == 6  # only the seeded ones


def test_a_second_call_can_follow_on_the_same_connection(tmp_path):
    server, client, results, out = asyncio.run(play(tmp_path, calls=({}, {})))
    assert [r["summary"]["status"] for r in results] == ["as_usual", "as_usual"]
    assert results[0]["call_id"] != results[1]["call_id"]
    assert results[1]["summary"]["baseline_sessions"] == 7  # the first call's values entered the baseline


def test_start_call_while_a_call_runs_gets_an_error(tmp_path):
    async def scenario():
        clock = FakeClock()
        server = PcServer("t", "Ona", "off", True, "live", tmp_path / "d", tmp_path / "r", clock, lambda s: None)
        s, c = link.MemoryTransport.pair()
        task = asyncio.ensure_future(server.handle_connection(s))
        client = ScriptedClient(c, clock)
        await client.connect()
        await client.send("start_call")
        await client.send("start_call")
        for _ in range(100):
            await clock.settle()
            await clock.advance(0.1)
        await client.send("hang_up")
        await clock.settle()
        inbox = client.endpoint.transport._inbox
        seen = []
        while not inbox.empty():
            seen.append(inbox.get_nowait())
        await client.close()
        await asyncio.wait_for(task, 5)
        return seen

    seen = asyncio.run(scenario())
    errors = [m for m in seen if isinstance(m, dict) and m.get("type") == "error"]
    assert [e["code"] for e in errors] == ["call_in_progress"]
    assert BaselineStore("t", tmp_path / "d").path.name == "t_calls.json"
