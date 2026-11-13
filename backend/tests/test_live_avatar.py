import json

import httpx
import pytest

from checkin_backend.live_avatar import LIVE_WS_URL, LiveAvatarGateway
from tests.client_sim import SimClient
from tests.test_call_service import KEY, Server, run_call


def simli(requests, status=200, body=None):
    def respond(request):
        requests.append(request)
        return httpx.Response(status, json=body if body is not None else {"session_token": "short-lived-token"})
    return httpx.MockTransport(respond)


class LiveClient(SimClient):
    """Asks for a live session on each line, as the phone does before streaming the line's audio."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.sessions = []

    async def execute(self, http, command):
        if command["kind"] == "say":
            call_id = command["audio_path"].split("/")[3]
            response = await http.post(f"/v1/calls/{call_id}/avatar/live")
            self.sessions.append((response.status_code, response.json()))
        await super().execute(http, command)


@pytest.mark.anyio
async def test_answered_call_gets_a_live_session_without_the_account_key():
    requests = []
    live = LiveAvatarGateway(api_key="simli-account-key", face_id="face_1", max_session_s=240,
                             transport=simli(requests))
    async with Server("scripted", live=live) as server:
        sim = LiveClient(base=server.base, key=KEY)
        status = await run_call(server, sim)
    await live.aclose()
    assert status["reason"] == "completed"
    assert sim.sessions and all(code == 200 for code, _ in sim.sessions)
    assert sim.sessions[0][1] == {"session_token": "short-lived-token", "ws_url": LIVE_WS_URL}
    assert "simli-account-key" not in json.dumps(sim.sessions)
    sent = json.loads(requests[0].content)
    assert requests[0].headers["x-simli-api-key"] == "simli-account-key"
    assert sent["faceId"] == "face_1" and sent["audioInputFormat"] == "pcm16"
    assert sent["maxSessionLength"] == 240 and sent["handleSilence"] is True


@pytest.mark.anyio
@pytest.mark.parametrize("live", [
    LiveAvatarGateway(api_key=""),
    LiveAvatarGateway(api_key="key", enabled=False),
    LiveAvatarGateway(api_key="key", transport=simli([], status=402, body={"detail": "provider secret"})),
    LiveAvatarGateway(api_key="key", transport=simli([], body={"unexpected": True})),
])
async def test_unavailable_live_avatar_is_a_plain_503(live):
    async with Server("scripted", live=live) as server:
        sim = LiveClient(base=server.base, key=KEY)
        status = await run_call(server, sim)
    await live.aclose()
    assert status["reason"] == "completed"
    assert sim.sessions and all(code == 503 for code, _ in sim.sessions)
    assert all(body["code"] == "avatar_unavailable" for _, body in sim.sessions)
    assert "provider secret" not in json.dumps(sim.sessions)


@pytest.mark.anyio
async def test_live_session_needs_the_demo_key_and_an_answered_call():
    live = LiveAvatarGateway(api_key="key", transport=simli([]))
    async with Server("scripted", live=live) as server:
        async with httpx.AsyncClient(base_url=server.base) as http:
            denied = await http.post("/v1/calls/call_x/avatar/live")
            missing = await http.post("/v1/calls/call_x/avatar/live", headers={"X-Demo-Key": KEY})
    await live.aclose()
    assert denied.status_code == 401
    assert missing.status_code == 404


def test_avatar_mode_switch(monkeypatch):
    monkeypatch.setenv("SIMLI_API_KEY", "key")
    monkeypatch.setenv("AVATAR_MODE", "clips")
    assert not LiveAvatarGateway.from_environment().available
    monkeypatch.setenv("AVATAR_MODE", "live")
    assert LiveAvatarGateway.from_environment().available
    monkeypatch.setenv("AVATAR_MODE", "streaming")
    with pytest.raises(ValueError):
        LiveAvatarGateway.from_environment()
