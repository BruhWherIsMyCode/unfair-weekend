import asyncio
import base64
import json

import httpx
import pytest

from checkin_backend.avatar import ClipGateway, check_download_url
from checkin_backend.provider_io import ProviderError

PCM = b"\x01\x00" * 1600
# Synthetic ISO media box, only for exercising transport/container validation.
MP4 = bytes.fromhex("000000186674797069736f6d0000020069736f6d69736f32")
MP4_URL = "https://api.simli.ai/static/mp4/avatar/line.mp4?token=temporary-secret"


@pytest.mark.anyio
async def test_video_generation_download_private_cache_and_offline_reuse(tmp_path):
    requests = []

    async def respond(request):
        requests.append(request)
        await asyncio.sleep(0.01)
        if request.method == "POST":
            return httpx.Response(200, json={"mp4_url": MP4_URL})
        return httpx.Response(200, content=MP4)

    clips = ClipGateway(api_key="simli-account-key", face_id="selected-avatar", cache_dir=tmp_path,
                        transport=httpx.MockTransport(respond))
    try:
        paths = await asyncio.gather(*(clips.prepare(PCM) for _ in range(5)))
        assert all(path == paths[0] for path in paths)
        assert paths[0].read_bytes() == MP4
        assert clips.ready(PCM) == paths[0]
        assert paths[0].stat().st_mode & 0o777 == 0o600
    finally:
        await clips.aclose()
    assert len(requests) == 2
    assert requests[0].headers["x-simli-api-key"] == "simli-account-key"
    assert json.loads(requests[0].content) == {
        "faceId": "selected-avatar", "audioBase64": base64.b64encode(PCM).decode(),
        "audioFormat": "pcm16", "audioSampleRate": 16000, "audioChannelCount": 1,
    }
    assert str(requests[1].url) == MP4_URL
    assert "x-simli-api-key" not in requests[1].headers
    offline = ClipGateway(face_id="selected-avatar", cache_dir=tmp_path)
    other_face = ClipGateway(face_id="another-avatar", cache_dir=tmp_path)
    try:
        assert await offline.prepare(PCM) == paths[0]
        assert other_face.ready(PCM) is None
        assert offline.ready(PCM + b"\x00\x00") is None
    finally:
        await offline.aclose()
        await other_face.aclose()


@pytest.mark.parametrize("url", [
    None, "http://api.simli.ai/static/mp4/a/b", "https://example.com/static/mp4/a/b",
    "https://api.simli.ai.example.com/static/mp4/a/b", "https://api.simli.ai:444/static/mp4/a/b",
    "https://user:password@api.simli.ai/static/mp4/a/b", "https://api.simli.ai/v1/private",
])
def test_download_locations_are_restricted(url):
    with pytest.raises(ProviderError):
        check_download_url(url)


@pytest.mark.anyio
@pytest.mark.parametrize("status, body", [(200, b"not a video"), (302, b""), (404, b"private provider error")])
async def test_bad_downloads_are_not_published(status, body, tmp_path):
    def respond(request):
        if request.method == "POST":
            return httpx.Response(200, json={"mp4_url": MP4_URL})
        return httpx.Response(status, content=body, headers={"location": "http://127.0.0.1/private"})

    clips = ClipGateway(api_key="key", cache_dir=tmp_path, transport=httpx.MockTransport(respond))
    try:
        with pytest.raises(ProviderError) as error:
            await clips.prepare(PCM)
        assert "private provider error" not in str(error.value)
        assert "temporary-secret" not in str(error.value)
        assert clips.ready(PCM) is None
        assert not list(tmp_path.glob("*.mp4"))
    finally:
        await clips.aclose()
