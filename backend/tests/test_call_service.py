"""End to end through a real Uvicorn server and the real HTTP/SSE protocol, with a scripted parent client.

No provider is contacted: without ELEVENLABS_API_KEY the speech gateway returns silence of the estimated line
length. The `analysis` tests need Agent 3's package on PYTHONPATH and are skipped without it.
"""

import asyncio
import base64
import socket

import httpx
import pytest
import uvicorn

from checkin_backend.app import create_app
from checkin_backend.config import Settings
from checkin_backend.media import MediaRejected, check_sample, jpeg_size
from checkin_backend.runner import analysis_available
from checkin_backend.speech import SpeechGateway
from tests.client_sim import SimClient, tiny_jpeg

KEY = "test-key-only"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Server:
    def __init__(self, runner: str, *, speech=None, clips=None, live=None, **settings) -> None:
        self.port = free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        self.speech = speech if speech is not None else SpeechGateway(api_key="")
        self.clips = clips
        app = create_app(Settings(demo_key=KEY, call_runner=runner, **settings), speech=self.speech, clips=clips,
                         live=live)
        self.app = app
        self.server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="warning"))

    async def __aenter__(self) -> "Server":
        self.task = asyncio.create_task(self.server.serve())
        while not self.server.started:
            await asyncio.sleep(0.01)
        return self

    async def __aexit__(self, *exc) -> None:
        self.server.should_exit = True
        await asyncio.wait_for(self.task, 10)
        await self.speech.aclose()
        if self.clips is not None:
            await self.clips.aclose()


async def run_call(server: Server, sim: SimClient, timeout: float = 60) -> dict:
    task = asyncio.create_task(sim.run())
    async with httpx.AsyncClient(base_url=server.base, headers={"X-Demo-Key": KEY}) as http:
        for _ in range(100):
            if server.app.state.hub.parents_of(sim.person_id):
                break
            await asyncio.sleep(0.02)
        r = await http.post("/v1/calls", json={"person_id": sim.person_id})
        assert r.status_code == 201, r.text
        call_id = r.json()["call_id"]
        await asyncio.wait_for(task, timeout)
        status = (await http.get(f"/v1/calls/{call_id}")).json()
    return status


@pytest.mark.anyio
async def test_scripted_call_end_to_end_over_http():
    async with Server("scripted") as server:
        family = []

        async def family_feed():
            async with httpx.AsyncClient(base_url=server.base, headers={"X-Demo-Key": KEY}, timeout=None) as http:
                await http.post("/v1/devices", json={"device_id": "dev_family", "role": "family", "family_id": "f_demo"})
                async with http.stream("GET", "/v1/devices/dev_family/events") as stream:
                    async for line in stream.aiter_lines():
                        if line.startswith("event: message.new"):
                            family.append(line)
                            return

        feed = asyncio.create_task(family_feed())
        await asyncio.sleep(0.1)
        sim = SimClient(base=server.base, key=KEY)
        status = await run_call(server, sim)
        await asyncio.wait_for(feed, 5)
    assert sim.said[0] == "greet"
    assert sim.said[-1] == "bye"
    assert {"q_medicine", "q_feeling", "q_unusual", "task_smile", "task_arms", "task_sentence"} <= set(sim.said)
    assert status["state"] == "ended"
    assert status["reason"] == "completed"
    assert status["message"]["kind"] == "incomplete"
    assert status["message"]["text"].endswith("If she seems unwell, call 112.")
    samples = [e for e in status["log"] if e["kind"] == "scripted_task"]
    assert [e["samples"] for e in samples] == [30, 100, 48]  # smile 6 s; arms 20 s since v12
    assert family


@pytest.mark.anyio
async def test_decline_and_ring_timeout_publish_no_answer():
    async with Server("scripted", ring_timeout_s=0.3) as server:
        async with httpx.AsyncClient(base_url=server.base, headers={"X-Demo-Key": KEY}) as http:
            assert (await http.post("/v1/calls", json={"person_id": "p_demo"})).status_code == 503
            await http.post("/v1/devices", json={"device_id": "d1", "role": "parent", "person_id": "p_demo"})
            call_id = (await http.post("/v1/calls", json={"person_id": "p_demo"})).json()["call_id"]
            assert (await http.post(f"/v1/calls/{call_id}/decline")).status_code == 204
            await asyncio.sleep(0.05)
            status = (await http.get(f"/v1/calls/{call_id}")).json()
            assert status["state"] == "no_answer"
            assert status["message"]["kind"] == "no_answer"
            call_id = (await http.post("/v1/calls", json={"person_id": "p_demo"})).json()["call_id"]
            await asyncio.sleep(0.5)
            assert (await http.get(f"/v1/calls/{call_id}")).json()["reason"] == "ring_timeout"
            assert (await http.post(f"/v1/calls/{call_id}/answer")).status_code == 409


@pytest.mark.anyio
async def test_hang_up_mid_call_stops_everything():
    async with Server("scripted") as server:
        sim = SimClient(base=server.base, key=KEY, hang_up_after="q_feeling")
        status = await run_call(server, sim)
    assert status["reason"] == "hangup"
    assert "bye" not in sim.said
    assert "task_smile" not in sim.said


@pytest.mark.anyio
async def test_protocol_rejections():
    async with Server("scripted") as server:
        async with httpx.AsyncClient(base_url=server.base, headers={"X-Demo-Key": KEY}) as http:
            assert (await http.get("/v1/devices/nope/events")).status_code == 404
            r = await http.post("/v1/devices", json={"device_id": "d1", "role": "parent", "person_id": "nobody"})
            assert r.status_code == 400
            await http.post("/v1/devices", json={"device_id": "d1", "role": "parent", "person_id": "p_demo"})
            call_id = (await http.post("/v1/calls", json={"person_id": "p_demo"})).json()["call_id"]
            r = await http.post(f"/v1/calls/{call_id}/client-events", json={"event_id": "e1", "type": "client.ready",
                                                                             "command_id": None, "error": None})
            assert r.status_code == 409  # not answered yet
            await http.post(f"/v1/calls/{call_id}/answer")
            bad = {"event_id": "e2", "type": "resource.changed", "command_id": None, "resource": "camera",
                   "state": "unavailable", "sequence": 1, "reason": None}
            assert (await http.post(f"/v1/calls/{call_id}/client-events", json=bad)).status_code == 400
            ok = dict(bad, reason="permission_denied")
            assert (await http.post(f"/v1/calls/{call_id}/client-events", json=ok)).status_code == 204
            assert (await http.post(f"/v1/calls/{call_id}/client-events", json=ok)).status_code == 204
            clash = dict(ok, sequence=2)
            assert (await http.post(f"/v1/calls/{call_id}/client-events", json=clash)).status_code == 409
            sample = {"sequence": 0, "captured_ms": 0, "format": "jpeg",
                      "data_base64": base64.b64encode(tiny_jpeg()).decode()}
            r = await http.post(f"/v1/calls/{call_id}/attempts/A9/samples", json=sample)
            assert r.status_code == 404
            assert (await http.get(f"/v1/calls/{call_id}/speech/cmd_x")).status_code == 404
            assert (await http.get("/v1/health")).status_code == 200
            assert (await http.get("/v1/health", headers={"X-Demo-Key": "x"})).status_code == 401


def test_media_checks():
    assert jpeg_size(tiny_jpeg(640, 480)) == (640, 480)
    check_sample("jpeg", tiny_jpeg(480, 640))
    for bad in (b"not a jpeg", tiny_jpeg(1280, 720)):
        with pytest.raises(MediaRejected):
            check_sample("jpeg", bad)
    check_sample("pcm_s16le_16000_mono", bytes(8000))
    for bad in (bytes(8001), bytes(8002)):
        with pytest.raises(MediaRejected):
            check_sample("pcm_s16le_16000_mono", bad)


needs_analysis = pytest.mark.skipif(not analysis_available(), reason="Agent 3's analysis package is not on the path")


@needs_analysis
@pytest.mark.anyio
async def test_call_session_drives_the_client_end_to_end(monkeypatch):
    monkeypatch.setenv("CALL_DRIVER", "fixed")
    async with Server("fake") as server:
        sim = SimClient(base=server.base, key=KEY)
        status = await run_call(server, sim, timeout=120)
    assert sim.said[0] == "greet"
    assert sim.said[-1] == "bye"
    assert "task_smile" in sim.said
    assert status["reason"] == "completed"
    assert status["summary"]["status"] == "incomplete"
    assert status["message"]["kind"] == "incomplete"
    observed = [e for e in sim.seen if e[0] == "task.progress"]
    assert observed, "the session should project neutral progress to the client"
    assert all("metrics" not in data for _, data in observed)


@needs_analysis
@pytest.mark.anyio
async def test_camera_loss_interrupts_and_recovery_starts_a_new_attempt(monkeypatch):
    monkeypatch.setenv("CALL_DRIVER", "fixed")
    async with Server("fake") as server:
        sim = SimClient(base=server.base, key=KEY, drop_camera_on="smile", drop_after_samples=5)
        status = await run_call(server, sim, timeout=120)
    cancels = [d for t, d in sim.seen if t == "command.cancel"]
    assert any(c["reason"] == "task_interrupted" for c in cancels)
    assert "camera_unavailable" in sim.said
    smile_captures = [d["command"]["command_id"] for t, d in sim.seen
                      if t == "command.execute" and d["command"].get("purpose", {}).get("task") == "smile"]
    assert len(smile_captures) >= 2
    assert len(set(smile_captures)) == len(smile_captures)
    assert status["reason"] == "completed"
