"""Long polling: the device event stream for networks that buffer an open stream (a Cloudflare quick tunnel)."""
import asyncio
import json

import httpx
import pytest

from checkin_backend.hub import Device, DeviceHub
from tests.client_sim import SimClient
from tests.test_call_service import KEY, Server, run_call


def test_cursor_replays_what_was_sent_between_polls_and_survives_a_restart():
    hub = DeviceHub()
    hub.register(Device("dev_f", "family", None, "f1"))
    hub.send("dev_f", "message.new", {"n": 1})  # before the first poll, not active: not replayed
    events, queue, cursor = hub.subscribe_after("dev_f", None)
    hub.unsubscribe("dev_f", queue)
    assert events == [] and cursor == 1
    hub.send("dev_f", "message.new", {"n": 2})  # between two polls
    events, queue, cursor = hub.subscribe_after("dev_f", cursor)
    hub.unsubscribe("dev_f", queue)
    assert [e.data["n"] for e in events] == [2] and cursor == 2
    events, queue, _ = hub.subscribe_after("dev_f", 999)  # a cursor from before a restart: a fresh start
    hub.unsubscribe("dev_f", queue)
    assert events == []


@pytest.mark.anyio
async def test_a_family_phone_that_only_polls_gets_the_message_of_a_scripted_call():
    async with Server("scripted") as server:
        family: list[dict] = []

        async def family_poll():
            async with httpx.AsyncClient(base_url=server.base, headers={"X-Demo-Key": KEY}, timeout=30) as http:
                await http.post("/v1/devices", json={"device_id": "dev_family", "role": "family", "family_id": "f_demo"})
                cursor = None
                while True:
                    params = {"wait_s": 2} | ({"after": cursor} if cursor is not None else {})
                    body = (await http.get("/v1/devices/dev_family/events/poll", params=params)).json()
                    cursor = body["cursor"]
                    for frame in body["events"]:
                        if frame["event"] == "message.new":
                            family.append(json.loads(frame["data"]))
                            return

        feed = asyncio.create_task(family_poll())
        await asyncio.sleep(0.1)
        status = await run_call(server, SimClient(base=server.base, key=KEY))
        await asyncio.wait_for(feed, 5)
        async with httpx.AsyncClient(base_url=server.base, headers={"X-Demo-Key": KEY}) as http:
            unknown = await http.get("/v1/devices/nope/events/poll", params={"wait_s": 0})
            too_long = await http.get("/v1/devices/dev_family/events/poll", params={"wait_s": 60})
            denied = await httpx.AsyncClient(base_url=server.base).get("/v1/devices/dev_family/events/poll")
    assert status["state"] == "ended"
    assert family and family[0]["text"].endswith("If she seems unwell, call 112.")
    assert unknown.status_code == 404 and too_long.status_code == 400 and denied.status_code == 401


@pytest.mark.anyio
async def test_a_poll_returns_as_soon_as_an_event_arrives():
    async with Server("scripted") as server:
        hub = server.app.state.hub
        async with httpx.AsyncClient(base_url=server.base, headers={"X-Demo-Key": KEY}, timeout=30) as http:
            await http.post("/v1/devices", json={"device_id": "dev_f2", "role": "family", "family_id": "f_demo"})
            first = (await http.get("/v1/devices/dev_f2/events/poll", params={"wait_s": 0})).json()
            waiting = asyncio.create_task(http.get("/v1/devices/dev_f2/events/poll",
                                                   params={"after": first["cursor"], "wait_s": 20}))
            await asyncio.sleep(0.2)
            hub.send("dev_f2", "message.new", {"text": "hello"})
            body = (await asyncio.wait_for(waiting, 3)).json()
    assert first["events"] == []
    assert [json.loads(f["data"]) for f in body["events"]] == [{"text": "hello"}]
    assert body["cursor"] > first["cursor"]
