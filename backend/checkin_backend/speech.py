"""Speech gateway: fixed lines to PCM, and answer audio to text.

Prepared speech is read from disk before contacting ElevenLabs. Without a key or cached speech, the demo
uses labelled caption-only playback. Answer audio is sent temporarily to ElevenLabs for transcription.
Provider response bodies and credentials never reach the client.
"""

import asyncio
import hashlib
import json
import logging
import os
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

import httpx

from .provider_io import PendingAssets, ProviderError, private_write, provider_bytes
from .logging_config import log_event

log = logging.getLogger(__name__)

SAMPLE_RATE = 16000
BYTES_PER_SECOND = SAMPLE_RATE * 2
MAX_LINE_SECONDS = 30
MAX_LINE_BYTES = BYTES_PER_SECOND * MAX_LINE_SECONDS
MAX_TEXT_CHARS = 600
MAX_TRANSCRIPT_BYTES = 128 * 1024
MAX_ANSWER_BYTES = 10 * 1024 * 1024
WORDS_PER_SECOND = 2.5
PROVIDER_TIMEOUT_S = 25.0
STT_TIMEOUT_S = 20.0

ROOT = Path(__file__).resolve().parents[2]
SCRIPT_CANDIDATES = (
    ROOT / "contract" / "script.en.json",
    ROOT / "backend" / "proposals" / "script.en.json",
    Path(__file__).with_name("data") / "script.en.json",
)
PROPOSED_LINES = {
    "swap_hands": "Now swap: hold the phone in your other hand and raise the free one.",
}


SpeechError = ProviderError


@dataclass(frozen=True, slots=True)
class PreparedLine:
    text: str
    audio: bytes
    duration_ms: int
    voiced: bool


def load_script() -> dict[str, str]:
    for path in SCRIPT_CANDIDATES:
        if path.exists():
            lines = dict(PROPOSED_LINES)
            lines.update(json.loads(path.read_text("utf-8")))
            return lines
    raise FileNotFoundError("No English script found")


def render_line(script: dict[str, str], line_id: str, name: str) -> str:
    try:
        template = script[line_id]
    except KeyError:
        raise SpeechError(f"Unknown line {line_id}") from None
    return template.replace("{name}", name)


def silence_for(text: str) -> bytes:
    words = max(1, len(text.split()))
    seconds = min(MAX_LINE_SECONDS, words / WORDS_PER_SECOND + 0.5)
    frames = int(seconds * SAMPLE_RATE)
    return bytes(frames * 2)


def duration_ms(audio: bytes) -> int:
    return max(1, round(len(audio) * 1000 / BYTES_PER_SECOND))


def check_pcm(audio: bytes) -> None:
    if not audio or len(audio) % 2 or audio[:4] == b"RIFF":
        raise SpeechError("ElevenLabs returned an unexpected audio format")
    if len(audio) > MAX_LINE_BYTES:
        raise SpeechError("Line is longer than 30 seconds")


class SpeechGateway:
    def __init__(
        self,
        *,
        api_key: str = "",
        voice_id: str = "JBFqnCBsd6RMkjVDRZzb",
        model_id: str = "eleven_flash_v2_5",
        stt_model_id: str = "scribe_v2",
        cache_dir: Path | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._key = api_key.strip()
        self._voice_id = voice_id
        self._model_id = model_id
        self._stt_model_id = stt_model_id
        self._cache_dir = cache_dir
        self._memory: OrderedDict[str, bytes] = OrderedDict()
        self._pending = PendingAssets()
        self._client = httpx.AsyncClient(timeout=PROVIDER_TIMEOUT_S, transport=transport)

    @property
    def voiced(self) -> bool:
        return bool(self._key)

    @classmethod
    def from_environment(cls) -> "SpeechGateway":
        cache = Path(__file__).resolve().parents[1] / ".cache" / "speech"
        return cls(
            api_key=os.environ.get("ELEVENLABS_API_KEY", ""),
            voice_id=os.environ.get("ELEVENLABS_VOICE_ID", "JBFqnCBsd6RMkjVDRZzb"),
            model_id=os.environ.get("ELEVENLABS_MODEL_ID", "eleven_flash_v2_5"),
            stt_model_id=os.environ.get("ELEVENLABS_STT_MODEL_ID", "scribe_v2"),
            cache_dir=cache,
        )

    async def aclose(self) -> None:
        await self._pending.aclose()
        await self._client.aclose()

    async def prepare(self, text: str, *, require_voice: bool = False) -> PreparedLine:
        if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT_CHARS:
            raise SpeechError("A line must contain between 1 and 600 characters")
        # Read prepared audio before checking credentials: the voice fallback works offline.
        key = hashlib.sha256(json.dumps([self._voice_id, self._model_id, "en", text, "pcm_16000"]).encode()).hexdigest()
        audio = await self._pending.get(key, lambda: self._cached(key, text))
        if audio is None:
            if require_voice:
                raise SpeechError("ELEVENLABS_API_KEY is required to prepare uncached speech")
            audio = silence_for(text)
            log_event(log, logging.WARNING, "speech.caption_fallback", duration_ms=duration_ms(audio))
            return PreparedLine(text, audio, duration_ms(audio), False)
        return PreparedLine(text, audio, duration_ms(audio), True)

    async def _cached(self, key: str, text: str) -> bytes | None:
        if key in self._memory:
            log_event(log, logging.DEBUG, "speech.cache_hit", cache="memory")
            self._memory.move_to_end(key)
            return self._memory[key]
        path = None
        if self._cache_dir is not None:
            path = self._cache_dir / f"{key}.pcm"
            if path.exists() and 0 < path.stat().st_size <= MAX_LINE_BYTES:
                audio = await asyncio.to_thread(path.read_bytes)
                try:
                    check_pcm(audio)
                except SpeechError:
                    log.warning("speech.cache_invalid")  # Regenerate an interrupted or invalid cache entry.
                else:
                    log_event(log, logging.DEBUG, "speech.cache_hit", cache="disk")
                    self._remember(key, audio)
                    return audio
        if not self._key:
            return None
        audio = await self._generate(text)
        if path is not None:
            await asyncio.to_thread(private_write, path, audio)
        self._remember(key, audio)
        return audio

    def _remember(self, key: str, audio: bytes) -> None:
        self._memory[key] = audio
        if len(self._memory) > 64:
            self._memory.popitem(last=False)

    async def _generate(self, text: str) -> bytes:
        audio = await provider_bytes(
            self._client, "POST", f"https://api.elevenlabs.io/v1/text-to-speech/{quote(self._voice_id, safe='')}",
            provider="ElevenLabs", limit=MAX_LINE_BYTES, timeout_s=PROVIDER_TIMEOUT_S,
            params={"output_format": "pcm_16000"}, headers={"xi-api-key": self._key},
            json={"text": text, "model_id": self._model_id, "language_code": "en"},
        )
        check_pcm(audio)
        return audio

    async def transcribe(self, audio: bytes, content_type: str) -> str | None:
        """Her words, or None when nothing could be heard or no provider is configured."""
        if not self._key:
            log.info("speech.transcription_unconfigured")
            return None
        suffix = {"audio/mp4": "m4a", "audio/webm": "webm", "audio/wav": "wav"}.get(content_type)
        if suffix is None or not audio or len(audio) > MAX_ANSWER_BYTES:
            raise SpeechError("Answer audio has an unsupported type or size")
        body = await provider_bytes(
            self._client, "POST", "https://api.elevenlabs.io/v1/speech-to-text",
            provider="ElevenLabs", limit=MAX_TRANSCRIPT_BYTES, timeout_s=STT_TIMEOUT_S,
            headers={"xi-api-key": self._key},
            data={"model_id": self._stt_model_id, "language_code": "en", "diarize": "false",
                  "tag_audio_events": "false", "timestamps_granularity": "none"},
            files={"file": (f"answer.{suffix}", audio, content_type)},
        )
        try:
            result = json.loads(body)
            text = result.get("text") if isinstance(result, dict) else None
        except (ValueError, UnicodeError):
            raise SpeechError("ElevenLabs returned an unexpected transcript") from None
        if not isinstance(text, str):
            return None
        text = text.strip()
        if not text:
            return None
        return text[:2000]
