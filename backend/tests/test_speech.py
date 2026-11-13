import asyncio
import json
import stat

import httpx
import pytest

from checkin_backend.speech import MAX_LINE_BYTES, SpeechError, SpeechGateway, load_script, render_line
from tests.client_sim import tiny_wav

PCM = b"\x01\x00" * 1600


@pytest.mark.anyio
async def test_speech_format_single_generation_private_cache_and_offline_replay(tmp_path):
    requests = []

    async def respond(request):
        requests.append(request)
        await asyncio.sleep(0.01)
        return httpx.Response(200, content=PCM)

    gateway = SpeechGateway(api_key="provider-secret", cache_dir=tmp_path, transport=httpx.MockTransport(respond))
    try:
        lines = await asyncio.gather(*(gateway.prepare("Hello, Ona.") for _ in range(5)))
    finally:
        await gateway.aclose()
    assert len(requests) == 1
    request = requests[0]
    assert str(request.url) == "https://api.elevenlabs.io/v1/text-to-speech/JBFqnCBsd6RMkjVDRZzb?output_format=pcm_16000"
    assert json.loads(request.content) == {"text": "Hello, Ona.", "model_id": "eleven_flash_v2_5", "language_code": "en"}
    assert request.headers["xi-api-key"] == "provider-secret"
    assert all(line.audio == PCM and line.duration_ms == 100 and line.voiced for line in lines)
    path = next(tmp_path.glob("*.pcm"))
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert not list(tmp_path.glob("*.tmp"))
    offline = SpeechGateway(api_key="", cache_dir=tmp_path)
    try:
        assert (await offline.prepare("Hello, Ona.", require_voice=True)).audio == PCM
        assert (await offline.prepare("Uncached line")).voiced is False
        with pytest.raises(SpeechError, match="ELEVENLABS_API_KEY"):
            await offline.prepare("Uncached line", require_voice=True)
    finally:
        await offline.aclose()


@pytest.mark.anyio
async def test_cancelling_a_waiter_does_not_cancel_generation_for_other_calls():
    entered, release = asyncio.Event(), asyncio.Event()

    async def respond(request):
        entered.set()
        await release.wait()
        return httpx.Response(200, content=PCM)

    gateway = SpeechGateway(api_key="key", transport=httpx.MockTransport(respond))
    first = asyncio.create_task(gateway.prepare("Hello"))
    await entered.wait()
    second = asyncio.create_task(gateway.prepare("Hello"))
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    release.set()
    assert (await second).audio == PCM
    await gateway.aclose()


@pytest.mark.anyio
async def test_shutdown_joins_pending_provider_request():
    entered, cancelled = asyncio.Event(), asyncio.Event()

    async def respond(request):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    gateway = SpeechGateway(api_key="key", transport=httpx.MockTransport(respond))
    task = asyncio.create_task(gateway.prepare("Hello"))
    await entered.wait()
    await gateway.aclose()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cancelled.is_set()


@pytest.mark.anyio
@pytest.mark.parametrize("audio", [b"", b"odd", b"RIFF" + bytes(20), bytes(MAX_LINE_BYTES + 2)])
async def test_bad_provider_audio_is_not_cached(audio, tmp_path):
    gateway = SpeechGateway(api_key="key", cache_dir=tmp_path,
                            transport=httpx.MockTransport(lambda r: httpx.Response(200, content=audio)))
    try:
        with pytest.raises(SpeechError):
            await gateway.prepare("Hello")
        assert not list(tmp_path.glob("*.pcm"))
    finally:
        await gateway.aclose()


@pytest.mark.anyio
async def test_provider_failure_is_redacted_and_next_request_can_retry():
    count = 0

    def respond(request):
        nonlocal count
        count += 1
        return httpx.Response(401, json={"detail": "provider-secret and private text"}) if count == 1 else httpx.Response(200, content=PCM)

    gateway = SpeechGateway(api_key="provider-secret", transport=httpx.MockTransport(respond))
    try:
        with pytest.raises(SpeechError, match="HTTP 401") as error:
            await gateway.prepare("Hello")
        assert "provider-secret" not in str(error.value)
        assert "private text" not in str(error.value)
        assert (await gateway.prepare("Hello")).voiced
    finally:
        await gateway.aclose()


@pytest.mark.anyio
async def test_answer_stt_is_english_multipart_and_never_written(tmp_path):
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={"text": "  I slept well.  "})

    gateway = SpeechGateway(api_key="key", cache_dir=tmp_path, transport=httpx.MockTransport(respond))
    try:
        assert await gateway.transcribe(tiny_wav(), "audio/wav") == "I slept well."
    finally:
        await gateway.aclose()
    request = requests[0]
    assert str(request.url) == "https://api.elevenlabs.io/v1/speech-to-text"
    assert "multipart/form-data" in request.headers["content-type"]
    assert b'name="model_id"\r\n\r\nscribe_v2' in request.content
    assert b'name="language_code"\r\n\r\nen' in request.content
    assert b'filename="answer.wav"' in request.content
    assert not list(tmp_path.iterdir())


@pytest.mark.anyio
@pytest.mark.parametrize("result, expected", [({"text": ""}, None), ({"text": 1}, None), ([], None)])
async def test_empty_and_nontext_transcripts_are_unknown(result, expected):
    gateway = SpeechGateway(api_key="key", transport=httpx.MockTransport(lambda r: httpx.Response(200, json=result)))
    try:
        assert await gateway.transcribe(tiny_wav(), "audio/wav") is expected
    finally:
        await gateway.aclose()


def test_all_script_lines_can_be_personalized():
    script = load_script()
    for line_id in script:
        line = render_line(script, line_id, "Ona")
        assert "{name}" not in line
        assert line.strip()
