import asyncio
from types import SimpleNamespace

import httpx
import pytest

from checkin_backend import prepare as prepare_command
from checkin_backend.avatar import ClipGateway
from checkin_backend.delivery import FamilyDelivery
from checkin_backend.hub import Device, DeviceHub
from checkin_backend.link import ClientLink
from checkin_backend.live_avatar import LiveAvatarGateway
from checkin_backend.people import Person
from checkin_backend.provider_io import ProviderError
from checkin_backend.speech import SpeechGateway, load_script
from tests.test_avatar import MP4, PCM


def configured_clips(tmp_path, requests):
    def unexpected_request(request):
        requests.append(request)
        raise AssertionError("Disabled prepared avatars must not contact Simli")

    class LocalClips(ClipGateway):
        def __init__(self, **kwargs):
            super().__init__(**kwargs, cache_dir=tmp_path,
                             transport=httpx.MockTransport(unexpected_request))

    return LocalClips.from_environment()


@pytest.mark.anyio
async def test_off_blocks_generation_and_ignores_existing_video_without_deleting_it(monkeypatch, tmp_path):
    monkeypatch.setenv("AVATAR_PREGENERATED_CLIPS", "off")
    monkeypatch.setenv("SIMLI_API_KEY", "test-key")
    requests = []
    clips = configured_clips(tmp_path, requests)
    cached = clips._path(PCM)
    cached.write_bytes(MP4)
    try:
        assert clips.ready(PCM) is None
        with pytest.raises(ProviderError, match="disabled"):
            await clips.prepare(PCM)
        assert requests == []
        assert cached.read_bytes() == MP4
    finally:
        await clips.aclose()


@pytest.mark.anyio
async def test_video_prepare_command_prepares_all_voice_lines_without_rendering_video(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("AVATAR_PREGENERATED_CLIPS", "off")
    requests, voice_requests = [], []
    clips = configured_clips(tmp_path, requests)

    def tts(request):
        voice_requests.append(request)
        return httpx.Response(200, content=PCM)

    speech = SpeechGateway(api_key="test-key", transport=httpx.MockTransport(tts))
    monkeypatch.setattr(prepare_command, "ClipGateway", SimpleNamespace(from_environment=lambda: clips))
    monkeypatch.setattr(prepare_command, "SpeechGateway", SimpleNamespace(from_environment=lambda: speech))
    code = await prepare_command.prepare(video=True, only_line=None)
    output = capsys.readouterr().out
    expected = {line for line in load_script() if line != "sentence"}
    prepared = {line.split(":", 1)[0] for line in output.splitlines() if ": voice ready" in line}
    assert code == 0
    assert prepared == expected
    assert len(voice_requests) > 1
    assert "video ready" not in output
    assert requests == []


@pytest.mark.anyio
async def test_disabled_cached_video_is_not_offered_but_live_and_voice_remain_available(monkeypatch, tmp_path):
    monkeypatch.setenv("AVATAR_PREGENERATED_CLIPS", "off")
    monkeypatch.setenv("AVATAR_MODE", "live")
    monkeypatch.setenv("SIMLI_API_KEY", "test-key")
    requests = []
    clips = configured_clips(tmp_path, requests)
    clips._path(PCM).write_bytes(MP4)
    live = LiveAvatarGateway.from_environment()
    speech = SpeechGateway(api_key="test-key", transport=httpx.MockTransport(
        lambda request: httpx.Response(200, content=PCM)))
    hub = DeviceHub()
    hub.register(Device("phone", "parent", "p_demo", None))
    _, queue = hub.subscribe("phone")
    link = ClientLink(call_id="call_test", person=Person("p_demo", "Test", "she", "f_demo"),
                      hub=hub, speech=speech, clips=clips, delivery=FamilyDelivery(hub))
    pending = asyncio.create_task(link.say("task_arms"))
    try:
        event = await asyncio.wait_for(queue.get(), 1)
        command = event.data["command"]
        assert "video_path" not in command
        assert link.speech_audio(command["command_id"]) == PCM
        assert live.available
        link.on_event({"event_id": "played", "type": "playback.completed",
                       "command_id": command["command_id"], "error": None})
        await pending
        assert requests == []
    finally:
        link.close()
        await asyncio.gather(pending, return_exceptions=True)
        await speech.aclose()
        await clips.aclose()
        await live.aclose()


@pytest.mark.anyio
@pytest.mark.parametrize("mode", [None, "on"])
async def test_enabled_or_unset_keeps_prepared_cache_available(monkeypatch, tmp_path, mode):
    if mode is None:
        monkeypatch.delenv("AVATAR_PREGENERATED_CLIPS", raising=False)
    else:
        monkeypatch.setenv("AVATAR_PREGENERATED_CLIPS", mode)
    clips = configured_clips(tmp_path, [])
    cached = clips._path(PCM)
    cached.write_bytes(MP4)
    try:
        assert clips.ready(PCM) == cached
        assert await clips.prepare(PCM) == cached
    finally:
        await clips.aclose()


@pytest.mark.parametrize("mode", ["invalid", "false", ""])
def test_invalid_prepared_clip_mode_is_rejected(monkeypatch, tmp_path, mode):
    monkeypatch.setenv("AVATAR_PREGENERATED_CLIPS", mode)
    with pytest.raises(ValueError, match="AVATAR_PREGENERATED_CLIPS"):
        configured_clips(tmp_path, [])
