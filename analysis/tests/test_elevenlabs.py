"""The ElevenLabs module against a fake http function. No network, no real key."""
from __future__ import annotations

import json
import urllib.parse

import pytest

from analysis.speech import elevenlabs
from analysis.speech.elevenlabs import SpeechError, SpeechNotConfigured

KEY = "test-key-should-never-leak-123"
ENV = {"ELEVENLABS_API_KEY": KEY}


class FakeHttp:
    def __init__(self, status=200, body=b"{}"):
        self.status, self.body, self.calls = status, body, []

    def __call__(self, method, url, headers, body, timeout):
        self.calls.append({"method": method, "url": url, "headers": dict(headers), "body": body, "timeout": timeout})
        return self.status, {}, self.body


def json_body(obj):
    return json.dumps(obj).encode()


def test_mint_token_posts_with_the_key_header_to_the_right_path():
    http = FakeHttp(200, json_body({"token": "sutkn_1"}))
    assert elevenlabs.mint_token("batch_scribe", http, ENV) == "sutkn_1"
    call = http.calls[0]
    assert call["method"] == "POST"
    assert call["url"] == "https://api.elevenlabs.io/v1/single-use-token/batch_scribe"
    assert call["headers"]["xi-api-key"] == KEY
    assert call["timeout"] == 15


def test_mint_token_other_kinds_and_bad_kind():
    http = FakeHttp(200, json_body({"token": "t"}))
    elevenlabs.mint_token("tts_websocket", http, ENV)
    assert http.calls[0]["url"].endswith("/single-use-token/tts_websocket")
    with pytest.raises(ValueError):
        elevenlabs.mint_token("everything", http, ENV)


def test_without_a_key_nothing_is_sent():
    http = FakeHttp()
    with pytest.raises(SpeechNotConfigured):
        elevenlabs.mint_token("batch_scribe", http, {})
    with pytest.raises(SpeechNotConfigured):
        elevenlabs.synthesize("hi", "voice", None, http, {"ELEVENLABS_API_KEY": "  "})
    assert http.calls == [] and elevenlabs.key_configured({}) is False and elevenlabs.key_configured(ENV) is True


STT_ANSWER = {"language_code": "en", "text": "Hello world", "words": [
    {"text": "Hello", "type": "word", "start": 0.1, "end": 0.5, "logprob": -0.1},
    {"text": " ", "type": "spacing", "start": 0.5, "end": 0.6, "logprob": 0.0},
    {"text": "(laughter)", "type": "audio_event", "start": 0.6, "end": 0.9, "logprob": -0.5},
    {"text": "world", "type": "word", "start": 0.9, "end": 1.4, "logprob": -0.2}]}


def test_transcribe_sends_the_token_in_the_query_and_no_key_header():
    http = FakeHttp(200, json_body(STT_ANSWER))
    result = elevenlabs.transcribe(b"RIFFwavbytes", "sutkn_abc", "en", http)
    call = http.calls[0]
    parsed = urllib.parse.urlparse(call["url"])
    assert parsed.path == "/v1/speech-to-text" and urllib.parse.parse_qs(parsed.query) == {"token": ["sutkn_abc"]}
    assert {k.lower() for k in call["headers"]} == {"content-type"}  # no xi-api-key
    assert call["headers"]["Content-Type"].startswith("multipart/form-data; boundary=")
    body = call["body"]
    for part in (b'name="model_id"\r\n\r\nscribe_v2', b'name="language_code"\r\n\r\nen',
                 b'name="timestamps_granularity"\r\n\r\nword', b'name="file"; filename="answer.wav"', b"RIFFwavbytes"):
        assert part in body
    assert KEY.encode() not in body
    assert result["text"] == "Hello world" and result["language_code"] == "en"


def test_only_words_are_kept():
    result = elevenlabs.transcribe(b"x", "t", http=FakeHttp(200, json_body(STT_ANSWER)))
    assert result["words"] == [{"text": "Hello", "start": 0.1, "end": 0.5, "logprob": -0.1},
                               {"text": "world", "start": 0.9, "end": 1.4, "logprob": -0.2}]


def test_a_multichannel_shaped_answer_uses_the_first_channel():
    http = FakeHttp(200, json_body({"transcripts": [STT_ANSWER, {"text": "other", "words": []}]}))
    assert elevenlabs.transcribe(b"x", "t", http=http)["text"] == "Hello world"


def test_non_2xx_raises_with_status_and_a_body_excerpt():
    long_body = ("x" * 1000).encode()
    for fn in (lambda h: elevenlabs.mint_token("batch_scribe", h, ENV),
               lambda h: elevenlabs.transcribe(b"x", "tok", http=h),
               lambda h: elevenlabs.synthesize("hello", "voice1", None, h, ENV)):
        with pytest.raises(SpeechError) as info:
            fn(FakeHttp(401, long_body))
        assert info.value.status == 401 and len(info.value.excerpt) == 300 and "401" in str(info.value)


def test_the_key_and_the_token_never_appear_in_an_exception():
    echo = ('{"detail": "bad key %s and token sutkn_secret"}' % KEY).encode()
    with pytest.raises(SpeechError) as info:
        elevenlabs.mint_token("batch_scribe", FakeHttp(403, echo), ENV)
    assert KEY not in str(info.value) and KEY not in info.value.excerpt and "[removed]" in info.value.excerpt
    with pytest.raises(SpeechError) as info:
        elevenlabs.transcribe(b"x", "sutkn_secret", http=FakeHttp(422, echo))
    assert "sutkn_secret" not in str(info.value)


def test_a_bad_answer_is_a_speech_error():
    with pytest.raises(SpeechError):
        elevenlabs.mint_token("batch_scribe", FakeHttp(200, b"not json"), ENV)
    with pytest.raises(SpeechError):
        elevenlabs.mint_token("batch_scribe", FakeHttp(200, json_body({"nope": 1})), ENV)
    with pytest.raises(SpeechError):
        elevenlabs.synthesize("hello", "v", None, FakeHttp(200, b""), ENV)


def test_synthesize_asks_for_raw_pcm_16k():
    http = FakeHttp(200, b"\x01\x00\x02\x00" * 10)
    pcm = elevenlabs.synthesize("Hello, Ona.", "voice 1", "eleven_x", http, ENV)
    call = http.calls[0]
    assert pcm == b"\x01\x00\x02\x00" * 10 and call["method"] == "POST"
    assert call["url"] == "https://api.elevenlabs.io/v1/text-to-speech/voice%201?output_format=pcm_16000"
    assert call["headers"]["xi-api-key"] == KEY and call["headers"]["Content-Type"] == "application/json"
    assert json.loads(call["body"]) == {"text": "Hello, Ona.", "model_id": "eleven_x"}
    elevenlabs.synthesize("Hi", "v", None, http, ENV)
    assert json.loads(http.calls[1]["body"])["model_id"] == "eleven_multilingual_v2"
    assert call["timeout"] == 15


def test_config_defaults_and_overrides():
    cfg = elevenlabs.load_config({})
    assert cfg.voice_explicit is False and cfg.voice_id == elevenlabs.PLACEHOLDER_VOICE_ID
    assert cfg.tts_model == "eleven_multilingual_v2" and cfg.language == "en"
    cfg = elevenlabs.load_config({"UW_TTS_VOICE_ID": "abc", "UW_TTS_MODEL": "m", "UW_STT_LANGUAGE": "lt"})
    assert (cfg.voice_id, cfg.voice_explicit, cfg.tts_model, cfg.language) == ("abc", True, "m", "lt")
    assert KEY not in repr(elevenlabs.load_config(ENV))  # the config never holds the key
