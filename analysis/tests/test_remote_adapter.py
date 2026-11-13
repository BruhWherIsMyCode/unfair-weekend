"""RemoteAdapter against a fake session, a fake clock and an in-memory link. Also the reading codecs."""
from __future__ import annotations

import asyncio

import pytest

from analysis.face_metric import FrameReading
from analysis.hand_metric import HandFrameReading
from analysis.pc import link
from analysis.pc.remote_adapter import RemoteAdapter
from analysis.pc.remote_tasks import (RemoteHandReader, RemoteSmileReader, hand_from_dict, hand_to_dict,
                                      passthrough_decode, remote_modules, smile_from_dict, smile_to_dict)
from analysis.session import limits
from analysis.session.clock import FakeClock


class FakeSession:
    def __init__(self):
        self.modules = remote_modules()
        self.notes, self.events, self.started, self.acks = [], [], [], []
        self.hung_up = False

    def note(self, kind, **fields):
        self.notes.append(kind)

    def handle_resource_event(self, event):
        self.events.append(event)

    def handle_capture_started(self, attempt_id):
        self.started.append(attempt_id)

    def handle_cancel_ack(self, attempt_id):
        self.acks.append(attempt_id)

    def hang_up(self):
        self.hung_up = True


class FakeFamily:
    async def publish(self, summary, phase, message_id=None):
        return {"message_id": "m", "delivery": "x"}


def rig(initial=None):
    server_side, client_side = link.MemoryTransport.pair()
    clock = FakeClock()
    adapter = RemoteAdapter(link.Endpoint(server_side), {"greet": "Hello, {name}.", "q_feeling": "How?"}, "Ona", clock,
                            FakeFamily(), initial)
    adapter.session = FakeSession()
    return adapter, client_side, clock


def m(type_, **f):
    d = {"type": type_, "call_id": "c", "seq": 1}
    d.update(f)
    return d


def test_say_sends_the_text_and_waits_for_said():
    async def scenario():
        adapter, client, clock = rig()
        task = asyncio.ensure_future(adapter.say("greet"))
        await clock.settle()
        sent = await client.recv()
        assert sent["type"] == "say" and sent["text"] == "Hello, Ona." and sent["line_id"] == "greet"
        assert not task.done()
        await adapter.dispatch(m("said", line_id="greet"))
        await clock.settle()
        assert task.done() and "say_timeout" not in adapter.session.notes

    asyncio.run(scenario())


def test_say_gives_up_after_the_playback_limit():
    async def scenario():
        adapter, client, clock = rig()
        task = asyncio.ensure_future(adapter.say("greet"))
        await clock.settle()
        await clock.advance(limits.PREPARE_PLAYBACK_S - 1)
        assert not task.done()
        await clock.advance(2)
        assert task.done() and "say_timeout" in adapter.session.notes

    asyncio.run(scenario())


def test_listen_returns_the_answer_or_none():
    async def scenario():
        adapter, client, clock = rig()
        task = asyncio.ensure_future(adapter.listen("q_feeling", 8.0))
        await clock.settle()
        assert (await client.recv())["timeout_s"] == 8.0
        await adapter.dispatch(m("answer", question_id="q_feeling", transcript="fine"))
        assert await task == "fine"
        task = asyncio.ensure_future(adapter.listen("q_feeling", 8.0))
        await clock.settle()
        await adapter.dispatch(m("answer", question_id="q_feeling", transcript=None))
        assert await task is None
        task = asyncio.ensure_future(adapter.listen("q_feeling", 8.0))
        await clock.settle()
        await clock.advance(11)
        assert await task is None and "listen_timeout" in adapter.session.notes

    asyncio.run(scenario())


def test_samples_follow_reading_messages_and_end_on_capture_ended():
    async def scenario():
        adapter, client, clock = rig()
        await adapter.start_capture("A1", "smile", {"duration_ms": 5000})
        start = await client.recv()
        assert start["type"] == "start_capture" and "phases" not in start and start["cues"] == []
        got = []

        async def collect():
            async for s in adapter.samples("A1"):
                got.append(s)

        task = asyncio.ensure_future(collect())
        await adapter.dispatch(m("reading", attempt_id="A1", sequence=0, captured_ms=0, task="smile", data={"usable": True}))
        await adapter.dispatch(m("reading", attempt_id="A1", sequence=1, captured_ms=200, task="smile", data=None))
        await adapter.dispatch(m("capture_ended", attempt_id="A1"))
        await adapter.dispatch(m("reading", attempt_id="A1", sequence=2, captured_ms=400, task="smile", data=None))
        await asyncio.wait_for(task, 2)
        assert [(s["sequence"], s["captured_ms"], s["format"]) for s in got] == [(0, 0, "reading"), (1, 200, "reading")]
        assert got[0]["data"] == {"usable": True} and got[1]["data"] is None
        assert "reading_ignored" in adapter.session.notes

    asyncio.run(scenario())


def test_arms_start_capture_carries_phases_and_cues_and_acks_reach_the_session():
    async def scenario():
        adapter, client, clock = rig()
        await adapter.start_capture("A2", "arms", {"duration_ms": 15000})
        start = await client.recv()
        assert start["phases"] == [list(p) for p in limits.ARMS_PHASES]
        assert start["cues"] == [{"at_ms": 6500, "line_id": "swap_hands"}]
        await adapter.dispatch(m("capture_started", attempt_id="A2"))
        await adapter.cancel_capture("A2", "task_interrupted")
        assert (await client.recv())["type"] == "cancel_capture"
        await adapter.dispatch(m("cancel_ack", attempt_id="A2"))
        assert adapter.session.started == ["A2"] and adapter.session.acks == ["A2"]
        assert [s async for s in adapter.samples("A2")] == []  # the ack ended the stream

    asyncio.run(scenario())


def test_status_becomes_resource_events_only_on_change():
    async def scenario():
        adapter, _, _ = rig({"camera": "available", "microphone": "available"})

        def status(camera, reason=None, mic="available"):
            return m("status", camera=camera, camera_reason=reason, microphone=mic, text="")

        await adapter.dispatch(status("available"))
        await adapter.dispatch(status("available"))
        assert adapter.session.events == []
        await adapter.dispatch(status("unavailable", "camera_blocked"))
        await adapter.dispatch(status("unavailable", "camera_blocked"))
        assert len(adapter.session.events) == 1
        e = adapter.session.events[0]
        assert (e["resource"], e["state"], e["reason"], e["sequence"]) == ("camera", "unavailable", "track_ended", 1)
        await adapter.dispatch(status("available"))
        assert len(adapter.session.events) == 2
        assert adapter.session.events[1]["state"] == "available" and adapter.session.events[1]["reason"] is None
        assert adapter.session.events[1]["sequence"] == 2
        assert adapter.session.events[0]["event_id"] != adapter.session.events[1]["event_id"]
        await adapter.dispatch(status("available", mic="unavailable"))
        assert adapter.session.events[-1]["resource"] == "microphone"

    asyncio.run(scenario())


def test_hang_up_reaches_the_session_and_frees_waiters():
    async def scenario():
        adapter, client, clock = rig()
        task = asyncio.ensure_future(adapter.say("greet"))
        await clock.settle()
        await adapter.dispatch(m("hang_up"))
        await clock.settle()
        assert task.done() and adapter.session.hung_up
        await adapter.say("greet")  # after the client left nothing is sent and nothing waits
        await adapter.start_capture("A9", "smile", {})
        assert client.closed is False
        await adapter.publish({"status": "as_usual"}, "final")

    asyncio.run(scenario())


def test_connection_lost_is_a_hang_up():
    async def scenario():
        adapter, client, clock = rig()
        await client.close()
        await adapter.say("greet")  # the send fails: treated as gone, returns
        assert adapter.gone and adapter.session.hung_up

    asyncio.run(scenario())


# ---- the reading codecs ------------------------------------------------------------------------------------

def test_smile_and_hand_readings_round_trip():
    r = FrameReading(usable=True, unit=100.0, below_left=1.1, below_right=0.9, corner_height=0.2, smile_strength=0.8,
                     timestamp_ms=400)
    assert smile_from_dict(smile_to_dict(r)) == r
    h = HandFrameReading(usable=False, reason="wrong hand: use your LEFT hand", face_ok=True, wrong_side=True,
                         unit=90.0, timestamp_ms=1200)
    assert hand_from_dict(hand_to_dict(h)) == h
    h2 = HandFrameReading(usable=True, face_ok=True, unit=90.0, height=0.5, palm_width_ratio=1.0, timestamp_ms=1)
    assert hand_from_dict(hand_to_dict(h2)) == h2


def test_bad_readings_are_rejected_and_readers_handle_null():
    with pytest.raises(ValueError):
        smile_from_dict({"usable": "yes"})
    with pytest.raises(ValueError):
        smile_from_dict({"usable": True, "corner_height": "high"})
    with pytest.raises(ValueError):
        hand_from_dict("nope")
    assert RemoteSmileReader()({"data": None}, 10) is None
    assert RemoteHandReader()({"data": None}, 10, "left") is None
    bad = RemoteSmileReader()({"data": {"usable": 3}}, 10)
    assert bad.usable is False and bad.timestamp_ms == 10
    ok = RemoteSmileReader()({"data": smile_to_dict(FrameReading(usable=True, corner_height=0.1))}, 777)
    assert ok.usable and ok.timestamp_ms == 777  # the module's strictly increasing time wins
    sample = {"sequence": 0, "captured_ms": 0, "format": "reading", "data": None}
    assert passthrough_decode(sample) is sample
