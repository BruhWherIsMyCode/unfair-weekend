"""Concern alerts stay alerts through SQLite, an offline caretaker and a backend restart."""

import asyncio
import json
from contextlib import closing

import httpx
import pytest

from checkin_backend import runner
from checkin_backend.people import Person
from checkin_backend.store import Store
from storage import connect_database
from tests.client_sim import SimClient
from tests.test_call_service import KEY, Server, run_call


@pytest.mark.anyio
@pytest.mark.parametrize("family_connected,final_summary", [(True, "calmer"), (False, "absent"), (False, "concern")])
async def test_concern_alert_survives_storage_and_restart(monkeypatch, tmp_path, family_connected, final_summary):
    concern = {"raised_by": "agent", "reason": "asked_for_help", "note": "synthetic test"}
    summary = {"status": "concern_raised", "signs": [], "concern": concern}
    expected_summary = {"status": "as_usual", "signs": []} if final_summary == "calmer" else (
        {} if final_summary == "absent" else summary)

    async def driver(link):
        await link.say("greet")
        if final_summary == "calmer":
            await link.publish(summary, "mid_call")
            await link.publish(expected_summary, "final")
        elif final_summary == "absent":
            link.delivery.publish(link.call_id, link.person, "concern_raised", "final", concern=concern)
        else:
            await link.publish(summary, "final")
        return expected_summary

    monkeypatch.setattr(runner, "run_scripted_call", driver)
    database = tmp_path / "shared.sqlite3"
    live_messages = []
    ready = asyncio.Event()

    async def receive_alerts(base):
        async with httpx.AsyncClient(base_url=base, headers={"X-Demo-Key": KEY}, timeout=None) as http:
            response = await http.post("/v1/devices", json={"device_id": "family_test", "role": "family",
                                                           "family_id": "f_demo"})
            assert response.status_code == 204
            async with http.stream("GET", "/v1/devices/family_test/events") as stream:
                ready.set()
                event = None
                async for line in stream.aiter_lines():
                    if line.startswith("event: "):
                        event = line[7:]
                    elif line.startswith("data: ") and event == "message.new":
                        live_messages.append(json.loads(line[6:]))
                        if live_messages[-1]["phase"] == "final":
                            return

    async with Server("scripted", database_path=str(database)) as server:
        listener = asyncio.create_task(receive_alerts(server.base)) if family_connected else None
        try:
            if listener:
                await asyncio.wait_for(ready.wait(), 5)
            status = await run_call(server, SimClient(base=server.base, key=KEY))
            if listener:
                await asyncio.wait_for(listener, 5)
            await server.app.state.store.flush()
            assert server.app.state.store.failures == 0
            async with httpx.AsyncClient(base_url=server.base, headers={"X-Demo-Key": KEY}) as http:
                feed = (await http.get("/v1/families/f_demo/messages")).json()
        finally:
            if listener and not listener.done():
                listener.cancel()
                await asyncio.gather(listener, return_exceptions=True)

    message = status["message"]
    assert message["kind"] == "concern_raised" and message["phase"] == "final"
    assert message["text"].endswith("call 112.")
    assert len(feed) == 1 and feed[0]["kind"] == "concern_raised"
    assert feed[0]["message_id"] == message["message_id"] and feed[0]["text"] == message["text"]
    if family_connected:
        assert [item["phase"] for item in live_messages] == ["mid_call", "final"]
        assert all(item["kind"] == "concern_raised" and item["message_id"] == message["message_id"]
                   for item in live_messages)

    with closing(connect_database(database)) as db:
        stored = db.execute("SELECT kind, template_id FROM family_messages").fetchone()
        assert tuple(stored) == ("incomplete", "family.concern_raised.v1")
        final = db.execute("SELECT status, summary_json FROM session_summaries WHERE phase = 'final'").fetchone()
        assert final["status"] == "incomplete" and json.loads(final["summary_json"]) == expected_summary
        payloads = [json.loads(row[0]) for row in db.execute(
            "SELECT payload_json FROM notification_outbox WHERE purpose = 'family_message'")]
        assert len(payloads) == (2 if family_connected else 0)
        assert all(payload["kind"] == "concern_raised" for payload in payloads)
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []

    async with Server("scripted", database_path=str(database)) as restarted:
        async with httpx.AsyncClient(base_url=restarted.base, headers={"X-Demo-Key": KEY}) as http:
            await http.post("/v1/devices", json={"device_id": "late_family_test", "role": "family",
                                                "family_id": "f_demo"})
            reloaded = (await http.get("/v1/families/f_demo/messages")).json()
            call = (await http.get(f"/v1/calls/{status['call_id']}")).json()
    assert reloaded == feed
    assert call["stored"] and call["message"]["kind"] == "concern_raised"
    assert call["message"]["message_id"] == message["message_id"]
    assert call["summary"] == expected_summary


@pytest.mark.anyio
async def test_an_incomplete_message_keeps_its_kind_when_the_summary_mentions_a_concern(tmp_path):
    """Decode the published template, rather than guessing the message kind from arbitrary summary data."""
    person = Person("p_test", "Ona", "she", "f_test")
    store = Store(tmp_path / "shared.sqlite3")
    store.open([person])
    try:
        store.record_call_started("call_test", person, [], 30, {})
        store.record_message("call_test", person.family_id, {
            "message_id": "message_test", "kind": "incomplete", "text": "Please call her. If she seems unwell, call 112.",
        }, "final", {"status": "concern_raised"}, [])
        [message] = await store.family_messages(person.family_id)
        assert store.failures == 0 and message["kind"] == "incomplete"
    finally:
        store.close()
