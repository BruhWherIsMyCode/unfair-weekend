"""Speech gateway: fixed lines to PCM, and answer audio to text.

ElevenLabs is used when ELEVENLABS_API_KEY is set. Without it, lines become silence of an estimated length so
the call still runs with captions only, and answers are never transcribed. Provider error bodies are never
forwarded, and keys never reach the client.
"""

import asyncio
import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path

import httpx

SAMPLE_RATE = 16000
BYTES_PER_SECOND = SAMPLE_RATE * 2
MAX_LINE_SECONDS = 30
MAX_LINE_BYTES = BYTES_PER_SECOND * MAX_LINE_SECONDS
WORDS_PER_SECOND = 2.5
PROVIDER_TIMEOUT_S = 25.0
STT_TIMEOUT_S = 20.0

ROOT = Path(__file__).resolve().parents[2]
SCRIPT_CANDIDATES = (
    ROOT / "contract" / "script.en.json",
    ROOT / "backend" / "proposals" / "script.en.json",
)
PROPOSED_LINES = {
    "swap_hands": "Now swap: hold the phone in your other hand and raise the free one.",
}


class SpeechError(RuntimeError):
    pass


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
    raise FileNotFoundError("No script.en.json found in contract/ or backend/proposals/")


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


class SpeechGateway:
    def __init__(
        self,
        *,
        api_key: str = "",
        voice_id: str = "JBFqnCBsd6RMkjVDRZzb",
        model_id: str = "eleven_flash_v2_5",
        stt_model_id: str = "scribe_v1",
        cache_dir: Path | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._key = api_key.strip()
        self._voice_id = voice_id
        self._model_id = model_id
        self._stt_model_id = stt_model_id
        self._cache_dir = cache_dir
        self._memory: dict[str, bytes] = {}
        self._pending: dict[str, asyncio.Task[bytes]] = {}
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
            cache_dir=cache,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def prepare(self, text: str) -> PreparedLine:
        if not self._key:
            audio = silence_for(text)
            return PreparedLine(text, audio, duration_ms(audio), False)
        key = hashlib.sha256(json.dumps([self._voice_id, self._model_id, text, "pcm_16000"]).encode()).hexdigest()
        audio = await self._cached(key, text)
        return PreparedLine(text, audio, duration_ms(audio), True)

    async def _cached(self, key: str, text: str) -> bytes:
        if key in self._memory:
            return self._memory[key]
        path = None
        if self._cache_dir is not None:
            path = self._cache_dir / f"{key}.pcm"
            if path.exists():
                audio = path.read_bytes()
                self._memory[key] = audio
                return audio
        if key not in self._pending:
            self._pending[key] = asyncio.create_task(self._generate(text))
        try:
            audio = await asyncio.shield(self._pending[key])
        finally:
            done = self._pending.get(key)
            if done is not None and done.done():
                self._pending.pop(key, None)
        self._memory[key] = audio
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            temporary = path.with_suffix(".tmp")
            temporary.write_bytes(audio)
            temporary.replace(path)
        return audio

    async def _generate(self, text: str) -> bytes:
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{self._voice_id}"
        try:
            response = await self._client.post(
                url,
                params={"output_format": "pcm_16000"},
                headers={"xi-api-key": self._key},
                json={"text": text, "model_id": self._model_id},
            )
        except httpx.HTTPError:
            raise SpeechError("ElevenLabs could not be reached") from None
        if response.status_code != 200:
            raise SpeechError(f"ElevenLabs returned HTTP {response.status_code}")
        audio = response.content
        if len(audio) == 0:
            raise SpeechError("ElevenLabs returned no audio")
        if len(audio) % 2 != 0:
            raise SpeechError("ElevenLabs returned an unexpected audio format")
        if audio[:4] == b"RIFF":
            raise SpeechError("ElevenLabs returned an unexpected audio format")
        if len(audio) > MAX_LINE_BYTES:
            raise SpeechError("Line is longer than 30 seconds")
        return audio

    async def transcribe(self, audio: bytes, content_type: str) -> str | None:
        """Her words, or None when nothing could be heard or no provider is configured."""
        if not self._key:
            return None
        suffix = content_type.split("/")[-1]
        try:
            response = await self._client.post(
                "https://api.elevenlabs.io/v1/speech-to-text",
                headers={"xi-api-key": self._key},
                data={"model_id": self._stt_model_id},
                files={"file": (f"answer.{suffix}", audio, content_type)},
                timeout=STT_TIMEOUT_S,
            )
        except httpx.HTTPError:
            raise SpeechError("ElevenLabs could not be reached") from None
        if response.status_code != 200:
            raise SpeechError(f"ElevenLabs returned HTTP {response.status_code}")
        try:
            text = response.json().get("text")
        except ValueError:
            raise SpeechError("ElevenLabs returned an unexpected transcript") from None
        if not isinstance(text, str):
            return None
        text = text.strip()
        if not text:
            return None
        return text[:2000]
