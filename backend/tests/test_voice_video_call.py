import asyncio

import httpx
import pytest

from checkin_backend.avatar import ClipGateway
from checkin_backend.delivery import FamilyDelivery, render
from checkin_backend.hub import DeviceHub
from checkin_backend.link import ClientLink
from checkin_backend.people import Person
from checkin_backend.speech import SpeechGateway
from tests.client_sim import SimClient
from tests.test_avatar import MP4, MP4_URL, PCM
from tests.test_call_service import KEY, Server, run_call


class VideoClient(SimClient):
    async def execute(self, http, command):
        if command.get("video_path"):
            video = await http.get(command["video_path"])
            assert video.status_code == 200
            assert video.headers["content-type"] == "video/mp4"
            assert video.headers["cache-control"] == "no-store"
            assert video.content == MP4
            denied = await http.get(command["video_path"], headers={"X-Demo-Key": "incorrect"})
            assert denied.status_code == 401
            partial = await http.get(command["video_path"], headers={"Range": "bytes=0-7"})
            assert partial.status_code == 206
            assert partial.content == MP4[:8]
        await super().execute(http, command)


@pytest.mark.anyio
async def test_call_carries_voice_video_and_transcribed_answers_over_http(tmp_path):
    tts, stt = [], []

    def voice_provider(request):
        if request.url.path.endswith("/speech-to-text"):
            stt.append(request)
            return httpx.Response(200, json={"text": "I slept well."})
        tts.append(request)
        return httpx.Response(200, content=PCM)

    def video_provider(request):
        return httpx.Response(200, json={"mp4_url": MP4_URL}) if request.method == "POST" else httpx.Response(200, content=MP4)

    speech = SpeechGateway(api_key="eleven-account-key", cache_dir=tmp_path / "speech",
                           transport=httpx.MockTransport(voice_provider))
    clips = ClipGateway(api_key="simli-account-key", cache_dir=tmp_path / "avatar",
                        transport=httpx.MockTransport(video_provider))
    await clips.prepare(PCM)
    async with Server("scripted", speech=speech, clips=clips) as server:
        sim = VideoClient(base=server.base, key=KEY)
        status = await run_call(server, sim)
        says = [data["command"] for kind, data in sim.seen if kind == "command.execute" and data["command"]["kind"] == "say"]
        assert all(command.get("video_path") for command in says)
        assert status["summary"]["answers"]["feeling"] == "I slept well."
        assert status["summary"]["answers"]["unusual"] == "I slept well."
        assert status["summary"]["status"] == "incomplete"  # This driver performs no measuring.
        assert status["reason"] == "completed"
        async with httpx.AsyncClient(base_url=server.base, headers={"X-Demo-Key": KEY}) as http:
            assert (await http.get(says[0]["video_path"])).status_code == 410
            assert (await http.get(says[0]["audio_path"])).status_code == 410
            assert (await http.get("/v1/health?key=" + KEY, headers={"X-Demo-Key": ""})).status_code == 401
    assert len(stt) == 3
    assert len(tts) == len(says)
    assert "eleven-account-key" not in str(sim.seen)
    assert "simli-account-key" not in str(sim.seen)
    assert "temporary-secret" not in str(sim.seen)
    assert not list(tmp_path.rglob("*.wav"))


@pytest.mark.anyio
async def test_say_waits_for_client_completion_and_never_generates_video_during_call(tmp_path):
    from checkin_backend.hub import Device

    def unexpected_video_request(request):
        raise AssertionError("Video generation must be explicitly requested before a call")

    speech = SpeechGateway(api_key="key", transport=httpx.MockTransport(lambda r: httpx.Response(200, content=PCM)))
    clips = ClipGateway(api_key="key", cache_dir=tmp_path, transport=httpx.MockTransport(unexpected_video_request))
    hub = DeviceHub()
    hub.register(Device("phone", "parent", "p_demo", None))
    link = ClientLink(call_id="call_1", person=Person("p_demo", "Ona", "she", "f_demo"),
                      hub=hub, speech=speech, clips=clips, delivery=FamilyDelivery(hub))
    _, queue = hub.subscribe("phone")
    pending = asyncio.create_task(link.say("greet"))
    try:
        event = await asyncio.wait_for(queue.get(), 1)
        command = event.data["command"]
        assert "video_path" not in command
        assert link.speech_audio(command["command_id"]) == PCM
        await asyncio.sleep(0.01)
        assert not pending.done()
        link.on_event({"event_id": "played", "type": "playback.completed", "command_id": command["command_id"], "error": None})
        await pending
    finally:
        link.close()
        await speech.aclose()
        await clips.aclose()


@pytest.mark.anyio
async def test_voice_provider_failure_ends_call_with_an_incomplete_family_message():
    speech = SpeechGateway(api_key="private-key", transport=httpx.MockTransport(
        lambda r: httpx.Response(401, json={"detail": "private-key and request text"})))
    async with Server("scripted", speech=speech) as server:
        sim = SimClient(base=server.base, key=KEY)
        status = await run_call(server, sim)
    assert status["reason"] == "connection_lost"
    assert status["summary"] is None
    assert status["message"]["kind"] == "incomplete"
    assert sim.said == []
    assert "private-key" not in str(status)
    assert "request text" not in str(status)


@pytest.mark.parametrize("pronoun", ["she", "he"])
def test_family_messages_require_confirmation_and_keep_the_handoff(pronoun):
    person = Person("p_demo", "Ona", pronoun, "f_demo")
    for kind in ("change_noticed", "incomplete", "no_answer"):
        signs = [{"sign": "face", "confirmed": True}] if kind == "change_noticed" else []
        assert render(kind, person, signs).endswith(f"If {pronoun} seems unwell, call 112.")
    for signs in ([], [{"sign": "face", "confirmed": False}], [{"sign": "face", "confirmed": "true"}]):
        with pytest.raises(ValueError, match="confirmed"):
            render("change_noticed", person, signs)
