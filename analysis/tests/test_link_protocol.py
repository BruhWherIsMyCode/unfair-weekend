"""The link: validation in both directions, an unknown message gets `error` (no crash), one client at a time.
MemoryTransport only, except one loopback websocket test on 127.0.0.1 (skipped when `websockets` is missing)."""
from __future__ import annotations

import asyncio

import pytest

from analysis.pc import link
from analysis.pc.demo_link import ScriptedClient
from analysis.pc.server import PcServer
from analysis.session.clock import FakeClock


def msg(type_, **fields):
    m = {"type": type_, "call_id": None, "seq": 1}
    m.update(fields)
    return m


HELLO = msg("hello", protocol=1, client="pc", person_id="p",
            capabilities={"camera": True, "microphone": False, "measures": ["smile", "arms"], "speech": False})
CLIENT_OK = [
    HELLO, msg("start_call"),
    msg("status", camera="available", camera_reason=None, microphone="unavailable", text="going fine"),
    msg("capture_started", attempt_id="A1"), msg("capture_ended", attempt_id="A1"), msg("cancel_ack", attempt_id="A1"),
    msg("reading", attempt_id="A1", sequence=0, captured_ms=0, task="smile", data=None),
    msg("reading", attempt_id="A1", sequence=1, captured_ms=200, task="smile", data={"usable": True}),
    msg("said", line_id="greet"), msg("answer", question_id="q_feeling", transcript=None),
    msg("answer", question_id="q_feeling", transcript="fine"), msg("hang_up"),
]
SERVER_OK = [
    msg("hello_ok", protocol=1, server_version="pc-1"), msg("say", line_id="greet", text="Hello"),
    msg("listen", question_id="q_feeling", timeout_s=8.0),
    msg("start_capture", attempt_id="A1", task="smile", settings={}, cues=[]),
    msg("start_capture", attempt_id="A2", task="arms", settings={}, cues=[{"at_ms": 5000, "line_id": "swap_hands"}],
        phases=[["settle", 0, 1000, None], ["hold_left", 1000, 5000, "left"]]),
    msg("cancel_capture", attempt_id="A1", reason="x"), msg("progress", text="hi"),
    msg("call_result", summary={"status": "as_usual"}, family_text="ok"), msg("error", code="busy", text="t"),
]


def test_valid_messages_pass_in_both_directions():
    for m in CLIENT_OK:
        assert link.validate_client_message(m) is None, m["type"]
    for m in SERVER_OK:
        assert link.validate_server_message(m) is None, m["type"]


def test_invalid_messages_are_described_not_raised():
    assert link.validate_client_message("nope")
    assert link.validate_client_message({"call_id": None, "seq": 1})  # no type
    assert "unknown type" in link.validate_client_message(msg("explode"))
    assert "unknown type" in link.validate_client_message(msg("say", line_id="a", text="b"))  # server type, wrong way
    assert link.validate_client_message({"type": "hang_up", "seq": 1})  # call_id missing
    assert link.validate_client_message({"type": "hang_up", "call_id": None, "seq": "1"})
    assert "missing field" in link.validate_client_message(msg("said"))
    assert link.validate_client_message(msg("reading", attempt_id="A", sequence=True, captured_ms=0, task="smile", data=None))
    assert link.validate_client_message(msg("status", camera="maybe", camera_reason=None, microphone="available", text=""))
    assert "missing field" in link.validate_server_message(msg("say", line_id="greet"))
    assert link.validate_server_message(msg("start_capture", attempt_id="A", task="arms", settings={}, cues=[],
                                            phases=[["settle", 0]]))
    assert link.validate_server_message(msg("listen", question_id="q", timeout_s="soon"))


def make_server(tmp_path, **kw):
    return PcServer("t", "Ona", "off", True, "live", tmp_path / "data", tmp_path / "runs", FakeClock(),
                    lambda s: None, **kw)


def test_unknown_and_incomplete_messages_get_error_and_the_server_keeps_going(tmp_path):
    async def scenario():
        server = make_server(tmp_path)
        s, c = link.MemoryTransport.pair()
        task = asyncio.ensure_future(server.handle_connection(s))
        await c.send(msg("explode"))
        await c.send(msg("said"))  # before hello and incomplete
        await c.send(msg("start_call"))  # before hello
        errors = [await asyncio.wait_for(c.recv(), 2) for _ in range(3)]
        assert [e["type"] for e in errors] == ["error"] * 3
        assert [e["code"] for e in errors] == ["invalid_message", "invalid_message", "expected_hello"]
        await c.send(HELLO)
        ok = await asyncio.wait_for(c.recv(), 2)
        assert ok["type"] == "hello_ok" and ok["protocol"] == 1
        await c.send(msg("answer", question_id="q"))  # incomplete after hello: error, still alive
        assert (await asyncio.wait_for(c.recv(), 2))["code"] == "invalid_message"
        await c.close()
        await asyncio.wait_for(task, 2)

    asyncio.run(scenario())


def test_wrong_protocol_version_is_refused(tmp_path):
    async def scenario():
        server = make_server(tmp_path)
        s, c = link.MemoryTransport.pair()
        task = asyncio.ensure_future(server.handle_connection(s))
        await c.send(dict(HELLO, protocol=99))  # v8: protocols 1 and 2 are both accepted
        assert (await asyncio.wait_for(c.recv(), 2))["code"] == "protocol"
        await asyncio.wait_for(task, 2)

    asyncio.run(scenario())


def test_a_second_client_is_told_busy_and_closed(tmp_path):
    async def scenario():
        server = make_server(tmp_path)
        s1, c1 = link.MemoryTransport.pair()
        first = asyncio.ensure_future(server.handle_connection(s1))
        await c1.send(HELLO)
        assert (await asyncio.wait_for(c1.recv(), 2))["type"] == "hello_ok"
        s2, c2 = link.MemoryTransport.pair()
        await asyncio.wait_for(server.handle_connection(s2), 2)  # returns at once
        busy = await asyncio.wait_for(c2.recv(), 2)
        assert busy["type"] == "error" and busy["code"] == "busy"
        with pytest.raises(link.TransportClosed):
            await asyncio.wait_for(c2.recv(), 2)
        await c1.send(msg("status", camera="available", camera_reason=None, microphone="available", text="x"))
        await c1.close()  # the first client is unaffected until it leaves
        await asyncio.wait_for(first, 2)
        s3, c3 = link.MemoryTransport.pair()  # after it left, a new client is welcome
        third = asyncio.ensure_future(server.handle_connection(s3))
        await c3.send(HELLO)
        assert (await asyncio.wait_for(c3.recv(), 2))["type"] == "hello_ok"
        await c3.close()
        await asyncio.wait_for(third, 2)

    asyncio.run(scenario())


def test_endpoint_numbers_messages_and_stamps_the_call_id():
    async def scenario():
        a, b = link.MemoryTransport.pair()
        ep = link.Endpoint(a)
        await ep.send("hang_up")
        ep.call_id = "call_1"
        await ep.send("hang_up")
        first, second = await b.recv(), await b.recv()
        assert (first["seq"], first["call_id"], second["seq"], second["call_id"]) == (1, None, 2, "call_1")

    asyncio.run(scenario())


def test_loopback_websocket_hello_and_busy(tmp_path):
    pytest.importorskip("websockets")

    async def scenario():
        server = make_server(tmp_path)
        ws = await link.serve(server.handle_connection, "127.0.0.1", 0)
        try:
            t1 = await asyncio.wait_for(link.connect("ws://127.0.0.1:%d" % ws.port), 5)
            await t1.send(HELLO)
            assert (await asyncio.wait_for(t1.recv(), 5))["type"] == "hello_ok"
            t2 = await asyncio.wait_for(link.connect("ws://127.0.0.1:%d" % ws.port), 5)
            assert (await asyncio.wait_for(t2.recv(), 5))["code"] == "busy"
            await t2.close()
            await t1.close()
        finally:
            await ws.close()

    asyncio.run(scenario())
