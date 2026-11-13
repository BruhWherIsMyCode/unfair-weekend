import asyncio
import base64
import json
from email.utils import formatdate

import httpx
import pytest

import checkin_backend.avatar as avatar
from checkin_backend.avatar import ClipGateway, check_download_url
from checkin_backend.provider_io import ProviderError

PCM = b"\x01\x00" * 1600
# Synthetic ISO media box, only for exercising transport/container validation.
MP4 = bytes.fromhex("000000186674797069736f6d0000020069736f6d69736f32")
MP4_URL = "https://api.simli.ai/static/mp4/avatar/line.mp4?token=temporary-secret"
CDN_URL = "https://simlimicrocdn.blob.core.windows.net/mp4/avatar/line.mp4?sig=cdn-secret"


@pytest.fixture
def video_wait(monkeypatch):
    delays = []

    async def wait(seconds):
        delays.append(seconds)
        await asyncio.sleep(0)

    monkeypatch.setattr(avatar, "sleep", wait, raising=False)
    return delays


@pytest.mark.anyio
async def test_delayed_video_retries_throttling_and_follows_the_cdn_once(tmp_path, video_wait):
    requests = []
    responses = iter([
        httpx.Response(200, json={"mp4_url": MP4_URL, "mp4_availablility_eta_seconds": 3}),
        httpx.Response(404, content=b"still rendering"),
        httpx.Response(429, headers={"Retry-After": "12"}),
        httpx.Response(404),
        httpx.Response(307, headers={"Location": CDN_URL}),
        httpx.Response(200, content=MP4),
    ])

    def respond(request):
        requests.append(request)
        return next(responses)

    clips = ClipGateway(api_key="account-secret", cache_dir=tmp_path, transport=httpx.MockTransport(respond))
    try:
        paths = await asyncio.gather(*(clips.prepare(PCM) for _ in range(3)))
        assert all(path == paths[0] for path in paths)
        assert paths[0].read_bytes() == MP4
        assert clips.ready(PCM) == paths[0]
    finally:
        await clips.aclose()
    assert video_wait == [4, 3, 12, 6.75]
    assert [request.method for request in requests] == ["POST", "GET", "GET", "GET", "GET", "GET"]
    assert all(str(request.url) == MP4_URL for request in requests[1:-1])
    assert str(requests[-1].url) == CDN_URL
    assert all("x-simli-api-key" not in request.headers for request in requests[1:])
    assert not list(tmp_path.glob("*.tmp"))


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
    "https://user:password@api.simli.ai/static/mp4/a/b", "https://api.simli.ai/v1/private", CDN_URL,
])
def test_download_locations_are_restricted(url):
    with pytest.raises(ProviderError):
        check_download_url(url)


@pytest.mark.anyio
@pytest.mark.parametrize("status, body", [(200, b"not a video"), (302, b""), (403, b"private provider error")])
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


@pytest.mark.anyio
async def test_cdn_readiness_retries_do_not_poll_the_api_again(tmp_path, video_wait):
    requests = []
    responses = iter([
        httpx.Response(200, json={"mp4_url": MP4_URL}),
        httpx.Response(307, headers={"Location": CDN_URL}),
        httpx.Response(404),
        httpx.Response(429, headers={"Retry-After": "7"}),
        httpx.Response(200, content=MP4),
    ])

    def respond(request):
        requests.append(request)
        return next(responses)

    clips = ClipGateway(api_key="key", cache_dir=tmp_path, transport=httpx.MockTransport(respond))
    try:
        assert (await clips.prepare(PCM)).read_bytes() == MP4
    finally:
        await clips.aclose()
    assert video_wait == [3, 7]
    assert [str(request.url) for request in requests[1:]] == [MP4_URL, CDN_URL, CDN_URL, CDN_URL]
    assert all("x-simli-api-key" not in request.headers for request in requests[1:])


@pytest.mark.anyio
@pytest.mark.parametrize("location", [
    "http://simlimicrocdn.blob.core.windows.net/mp4/file.mp4",
    "https://simlimicrocdn.blob.core.windows.net.evil.example/mp4/file.mp4",
    "https://user:private@simlimicrocdn.blob.core.windows.net/mp4/file.mp4",
    "https://simlimicrocdn.blob.core.windows.net:444/mp4/file.mp4",
    "https://simlimicrocdn.blob.core.windows.net/other/file.mp4",
    "https://127.0.0.1/mp4/file.mp4", MP4_URL, None,
])
async def test_unexpected_redirect_is_rejected_before_a_request(tmp_path, location):
    requests = []

    def respond(request):
        requests.append(request)
        if request.method == "POST":
            return httpx.Response(200, json={"mp4_url": MP4_URL})
        return httpx.Response(307, headers={"Location": location} if location else {})

    clips = ClipGateway(api_key="key", cache_dir=tmp_path, transport=httpx.MockTransport(respond))
    try:
        with pytest.raises(ProviderError) as error:
            await clips.prepare(PCM)
        assert "temporary-secret" not in str(error.value)
        assert "private" not in str(error.value)
        assert clips.ready(PCM) is None
    finally:
        await clips.aclose()
    assert len(requests) == 2


@pytest.mark.anyio
async def test_second_redirect_is_not_followed(tmp_path):
    requests = []

    def respond(request):
        requests.append(request)
        if request.method == "POST":
            return httpx.Response(200, json={"mp4_url": MP4_URL})
        return httpx.Response(307, headers={"Location": CDN_URL})

    clips = ClipGateway(api_key="key", cache_dir=tmp_path, transport=httpx.MockTransport(respond))
    try:
        with pytest.raises(ProviderError, match="HTTP 307"):
            await clips.prepare(PCM)
        assert clips.ready(PCM) is None
    finally:
        await clips.aclose()
    assert len(requests) == 3


@pytest.mark.anyio
@pytest.mark.parametrize("status", [404, 429])
async def test_rendering_retries_have_an_attempt_limit_and_never_repeat_generation(tmp_path, video_wait, status):
    requests = []

    def respond(request):
        requests.append(request)
        if request.method == "POST":
            return httpx.Response(200, json={"mp4_url": MP4_URL})
        return httpx.Response(status, content=b"private provider response")

    clips = ClipGateway(api_key="key", cache_dir=tmp_path, transport=httpx.MockTransport(respond))
    try:
        with pytest.raises(ProviderError, match="attempt limit") as error:
            await clips.prepare(PCM)
        assert "private provider response" not in str(error.value)
        assert "temporary-secret" not in str(error.value)
        assert clips.ready(PCM) is None
    finally:
        await clips.aclose()
    assert sum(request.method == "POST" for request in requests) == 1
    assert len(requests) == 21
    assert len(video_wait) == 19
    assert video_wait[:4] == [3, 4.5, 6.75, 10.125]
    assert max(video_wait) == 15


@pytest.mark.anyio
@pytest.mark.parametrize("retry_after, expected", [
    ("42", 42), (formatdate(1042, usegmt=True), 42),
    ("invalid", 3), ("-1", 3), ("nan", 3), ("inf", 3),
])
async def test_retry_after_supports_seconds_and_dates(tmp_path, video_wait, monkeypatch, retry_after, expected):
    monkeypatch.setattr(avatar, "time", lambda: 1000)
    responses = iter([
        httpx.Response(200, json={"mp4_url": MP4_URL}),
        httpx.Response(429, headers={"Retry-After": retry_after}),
        httpx.Response(200, content=MP4),
    ])
    clips = ClipGateway(api_key="key", cache_dir=tmp_path,
                        transport=httpx.MockTransport(lambda request: next(responses)))
    try:
        assert (await clips.prepare(PCM)).read_bytes() == MP4
    finally:
        await clips.aclose()
    assert video_wait == [expected]


@pytest.mark.anyio
@pytest.mark.parametrize("eta, expected", [
    (0, [1]), (2.5, [3.5]), (None, []), ("3", []), (True, []), (-1, []),
    (float("nan"), []), (float("inf"), []), (10**400, [60]),
])
async def test_eta_wait_is_bounded_and_invalid_hints_are_ignored(tmp_path, video_wait, eta, expected):
    def respond(request):
        if request.method == "POST":
            return httpx.Response(200, content=json.dumps({
                "mp4_url": MP4_URL, "mp4_availablility_eta_seconds": eta,
            }).encode())
        return httpx.Response(200, content=MP4)

    clips = ClipGateway(api_key="key", cache_dir=tmp_path, transport=httpx.MockTransport(respond))
    try:
        assert (await clips.prepare(PCM)).read_bytes() == MP4
    finally:
        await clips.aclose()
    assert video_wait == expected


@pytest.mark.anyio
async def test_wait_has_a_wall_clock_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(avatar, "DOWNLOAD_WAIT_TIMEOUT_S", 0.01)
    requests = []

    async def wait_forever(seconds):
        await asyncio.Event().wait()

    monkeypatch.setattr(avatar, "sleep", wait_forever)

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={"mp4_url": MP4_URL, "mp4_availablility_eta_seconds": 3})

    clips = ClipGateway(api_key="key", cache_dir=tmp_path, transport=httpx.MockTransport(respond))
    try:
        with pytest.raises(ProviderError, match="time limit"):
            await clips.prepare(PCM)
        assert clips.ready(PCM) is None
    finally:
        await clips.aclose()
    assert len(requests) == 1  # No GET after the wait budget expires.


@pytest.mark.anyio
async def test_shutdown_cancels_pending_render_waits(tmp_path, monkeypatch):
    entered, cancelled = asyncio.Event(), asyncio.Event()

    async def wait_forever(seconds):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(avatar, "sleep", wait_forever)
    clips = ClipGateway(api_key="key", cache_dir=tmp_path, transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json={"mp4_url": MP4_URL, "mp4_availablility_eta_seconds": 3})))
    task = asyncio.create_task(clips.prepare(PCM))
    await asyncio.wait_for(entered.wait(), 1)
    await clips.aclose()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cancelled.is_set()
    assert clips.ready(PCM) is None
