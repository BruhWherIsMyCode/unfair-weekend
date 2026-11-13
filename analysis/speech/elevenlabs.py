"""ElevenLabs access: the ONLY module that talks to ElevenLabs. Standard library only (urllib), no SDK.

Shapes are taken from the ElevenLabs reference pages (single-use token, speech-to-text convert, text-to-speech convert).

    mint_token(kind)            SERVER side. POST /v1/single-use-token/{kind} with the API key in the `xi-api-key`
                                header -> a token (valid 15 minutes, used once).
    transcribe(wav, token)      CLIENT side. POST /v1/speech-to-text?token=<batch_scribe token>, multipart form
                                (model_id scribe_v2, file, language_code, timestamps_granularity word). NO API key.
    synthesize(text, voice_id)  SERVER side. POST /v1/text-to-speech/{voice_id}?output_format=pcm_16000, JSON body,
                                `xi-api-key` header -> raw PCM 16 kHz mono signed 16-bit little-endian bytes.

The API key lives only in the server's environment (ELEVENLABS_API_KEY). `_server_headers()` is the one function that
reads it, and only to put it in a request header; it is never logged, returned in an error, or sent over the link.

Every request goes through an injectable `http(method, url, headers, body, timeout) -> (status, headers, body_bytes)`
so tests use a fake. Any non-2xx answer raises SpeechError with the HTTP status and the first 300 characters of the
body (with the key and the token cut out).

STATUS: never run against the real service in the build session; the request shapes follow the reference pages only.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

from analysis.session import limits

BASE_URL = "https://api.elevenlabs.io"
TOKEN_KINDS = ("realtime_scribe", "batch_scribe", "tts_websocket")
STT_MODEL = "scribe_v2"
TTS_OUTPUT_FORMAT = "pcm_16000"
EXCERPT_CHARS = 300

KEY_ENV = "ELEVENLABS_API_KEY"
VOICE_ENV = "UW_TTS_VOICE_ID"
TTS_MODEL_ENV = "UW_TTS_MODEL"
LANGUAGE_ENV = "UW_STT_LANGUAGE"

# UNVERIFIED: the voice id from the ElevenLabs text-to-speech docs example. It has never been checked against the
# owner's account, so the server refuses --tts elevenlabs unless UW_TTS_VOICE_ID is set on purpose.
PLACEHOLDER_VOICE_ID = "JBFqnCBsd6RMkjVDRZzb"
DEFAULT_TTS_MODEL = "eleven_multilingual_v2"  # the documented default model
DEFAULT_LANGUAGE = "en"

HttpFunction = Callable[[str, str, Dict[str, str], Optional[bytes], float], Tuple[int, Mapping[str, str], bytes]]


class SpeechError(Exception):
    """A failed ElevenLabs request. `status` is the HTTP status (None for a network failure)."""

    def __init__(self, message: str, status: Optional[int] = None, excerpt: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.excerpt = excerpt


class SpeechNotConfigured(SpeechError):
    """ELEVENLABS_API_KEY is not set in the server's environment."""


# ---- configuration (env, read in one place) -------------------------------------------------------------

@dataclass(frozen=True)
class SpeechConfig:
    voice_id: str
    voice_explicit: bool  # False: the unverified placeholder is in use
    tts_model: str
    language: str


def load_config(environ: Optional[Mapping[str, str]] = None) -> SpeechConfig:
    """UW_TTS_VOICE_ID, UW_TTS_MODEL and UW_STT_LANGUAGE (the key is NOT read here, see _server_headers)."""
    env = os.environ if environ is None else environ
    voice = (env.get(VOICE_ENV) or "").strip()
    return SpeechConfig(voice or PLACEHOLDER_VOICE_ID, bool(voice), (env.get(TTS_MODEL_ENV) or "").strip()
                        or DEFAULT_TTS_MODEL, (env.get(LANGUAGE_ENV) or "").strip() or DEFAULT_LANGUAGE)


def _server_headers(environ: Optional[Mapping[str, str]] = None) -> Tuple[Dict[str, str], str]:
    """THE one place the API key is read: (headers with xi-api-key, the key for scrubbing error text)."""
    env = os.environ if environ is None else environ
    key = (env.get(KEY_ENV) or "").strip()
    if not key:
        raise SpeechNotConfigured("speech_not_configured")
    return {"xi-api-key": key}, key


def key_configured(environ: Optional[Mapping[str, str]] = None) -> bool:
    """True when the server has an API key (the key itself is never returned)."""
    try:
        _server_headers(environ)
    except SpeechNotConfigured:
        return False
    return True


# ---- transport ----------------------------------------------------------------------------------------------

def urllib_http(method: str, url: str, headers: Dict[str, str], body: Optional[bytes], timeout: float
                ) -> Tuple[int, Mapping[str, str], bytes]:
    """The real HTTP function. An HTTP error status is returned, not raised; a network failure raises SpeechError."""
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 (fixed https base url)
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers or {}), exc.read()
    except Exception as exc:  # timeouts, DNS, TLS: the class name only
        raise SpeechError("speech service unreachable (%s)" % type(exc).__name__) from None


def _scrub(text: str, secrets: List[str]) -> str:
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[removed]")
    return text


def _check(status: int, body: bytes, secrets: List[str]) -> None:
    if 200 <= status < 300:
        return
    excerpt = _scrub(body[:EXCERPT_CHARS * 4].decode("utf-8", "replace"), secrets)[:EXCERPT_CHARS]
    raise SpeechError("speech service answered HTTP %d: %s" % (status, excerpt), status, excerpt)


def _json(body: bytes) -> Dict[str, Any]:
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise SpeechError("speech service answer is not JSON") from None
    if not isinstance(data, dict):
        raise SpeechError("speech service answer is not a JSON object")
    return data


# ---- server side: tokens and line audio -------------------------------------------------------------------

def mint_token(kind: str = "batch_scribe", http: Optional[HttpFunction] = None,
               environ: Optional[Mapping[str, str]] = None) -> str:
    """A single-use token (15 minutes) for a client. Needs the API key; raises SpeechNotConfigured without it."""
    if kind not in TOKEN_KINDS:
        raise ValueError("kind must be one of %s" % ", ".join(TOKEN_KINDS))
    headers, key = _server_headers(environ)
    url = "%s/v1/single-use-token/%s" % (BASE_URL, kind)
    status, _, body = (http or urllib_http)("POST", url, headers, b"", limits.STT_TIMEOUT_S)
    _check(status, body, [key])
    token = _json(body).get("token")
    if not isinstance(token, str) or not token:
        raise SpeechError("speech service answer has no token")
    return token


def synthesize(text: str, voice_id: str, model_id: Optional[str] = None, http: Optional[HttpFunction] = None,
               environ: Optional[Mapping[str, str]] = None) -> bytes:
    """Speak `text` with `voice_id`: raw PCM 16 kHz mono s16le bytes. Needs the API key."""
    if not text.strip() or not voice_id:
        raise ValueError("text and voice_id are required")
    headers, key = _server_headers(environ)
    headers = dict(headers, **{"Content-Type": "application/json"})
    url = "%s/v1/text-to-speech/%s?output_format=%s" % (BASE_URL, urllib.parse.quote(voice_id, safe=""),
                                                         TTS_OUTPUT_FORMAT)
    payload = json.dumps({"text": text, "model_id": model_id or DEFAULT_TTS_MODEL}).encode("utf-8")
    status, _, body = (http or urllib_http)("POST", url, headers, payload, limits.TTS_TIMEOUT_S)
    _check(status, body, [key])
    if len(body) < 2:
        raise SpeechError("speech service returned no audio")
    return body


# ---- client side: speech to text with a token (no key) -----------------------------------------------------

def _multipart(fields: List[Tuple[str, str]], file_name: str, file_bytes: bytes, content_type: str
               ) -> Tuple[bytes, str]:
    boundary = "----uwboundary" + uuid.uuid4().hex
    parts: List[bytes] = []
    for name, value in fields:
        parts.append(('--%s\r\nContent-Disposition: form-data; name="%s"\r\n\r\n%s\r\n' % (boundary, name, value))
                     .encode("utf-8"))
    parts.append(('--%s\r\nContent-Disposition: form-data; name="file"; filename="%s"\r\nContent-Type: %s\r\n\r\n'
                  % (boundary, file_name, content_type)).encode("utf-8"))
    parts.append(file_bytes)
    parts.append(("\r\n--%s--\r\n" % boundary).encode("utf-8"))
    return b"".join(parts), "multipart/form-data; boundary=%s" % boundary


def _word(item: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(item, dict) or item.get("type") != "word" or not isinstance(item.get("text"), str):
        return None

    def num(x: Any) -> Optional[float]:
        return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) else None

    return {"text": item["text"], "start": num(item.get("start")), "end": num(item.get("end")),
            "logprob": num(item.get("logprob"))}


def transcribe(audio_wav_bytes: bytes, token: str, language_code: str = DEFAULT_LANGUAGE,
               http: Optional[HttpFunction] = None) -> Dict[str, Any]:
    """Speech to text with word timings, authenticated by a single-use batch_scribe `token` (no API key here).

    Returns {"text", "words": [{"text", "start", "end", "logprob"}] (type == "word" only), "language_code"}."""
    if not token:
        raise SpeechError("no token")
    fields = [("model_id", STT_MODEL), ("language_code", language_code), ("timestamps_granularity", "word")]
    body, content_type = _multipart(fields, "answer.wav", audio_wav_bytes, "audio/wav")
    url = "%s/v1/speech-to-text?%s" % (BASE_URL, urllib.parse.urlencode({"token": token}))
    status, _, answer = (http or urllib_http)("POST", url, {"Content-Type": content_type}, body, limits.STT_TIMEOUT_S)
    _check(status, answer, [token])
    data = _json(answer)
    if isinstance(data.get("transcripts"), list) and data["transcripts"] and isinstance(data["transcripts"][0], dict):
        data = data["transcripts"][0]  # a multichannel-shaped answer: the first channel
    words = [w for w in (_word(i) for i in data.get("words") or []) if w is not None]
    text = data.get("text")
    return {"text": text if isinstance(text, str) else " ".join(w["text"] for w in words),
            "words": words, "language_code": data.get("language_code") or language_code}
