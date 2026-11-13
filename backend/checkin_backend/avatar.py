"""Prepare Simli video clips before calls; calls serve only local cached MP4s."""

import asyncio
import base64
import hashlib
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from .provider_io import PendingAssets, ProviderError, private_write, provider_bytes
from .speech import check_pcm

MAX_VIDEO_BYTES = 30 * 1024 * 1024
DEFAULT_FACE_ID = "bf13113a-355a-4d97-8b90-4edccd1da7b2"


def mp4_header(data: bytes) -> bool:
    # This is a container check, not a decoder or a playback-quality assessment.
    return len(data) >= 16 and data[4:8] == b"ftyp" and int.from_bytes(data[:4], "big") >= 16


def check_download_url(value: object) -> str:
    if not isinstance(value, str):
        raise ProviderError("Simli did not return an MP4 URL")
    try:
        url = urlsplit(value)
        permitted = (
            url.scheme == "https" and url.hostname == "api.simli.ai"
            and url.port in (None, 443) and url.username is None and url.password is None
            and not url.fragment and url.path.startswith(("/static/mp4/", "/mp4/"))
        )
    except ValueError:
        permitted = False
    if not permitted:
        raise ProviderError("Simli returned an unexpected MP4 location")
    return value


class ClipGateway:
    def __init__(
        self, *, api_key: str = "", face_id: str = DEFAULT_FACE_ID,
        cache_dir: Path | None = None, transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._key = api_key.strip()
        self._face_id = face_id
        self._cache_dir = cache_dir or Path(__file__).resolve().parents[1] / ".cache" / "avatar"
        self._client = httpx.AsyncClient(timeout=90, transport=transport, follow_redirects=False)
        self._pending = PendingAssets()

    @classmethod
    def from_environment(cls) -> "ClipGateway":
        return cls(
            api_key=os.environ.get("SIMLI_API_KEY") or os.environ.get("SMILI_API_KEY", ""),
            face_id=os.environ.get("SIMLI_FACE_ID", DEFAULT_FACE_ID),
        )

    def _path(self, audio: bytes) -> Path:
        key = hashlib.sha256(json.dumps([
            "simli-static-v1", self._face_id, hashlib.sha256(audio).hexdigest(), "pcm16", 16000, 1,
        ]).encode()).hexdigest()
        return self._cache_dir / f"{key}.mp4"

    def ready(self, audio: bytes) -> Path | None:
        """Never performs generation or network I/O on the call's critical path."""
        path = self._path(audio)
        try:
            if not 16 <= path.stat().st_size <= MAX_VIDEO_BYTES:
                return None
            with path.open("rb") as handle:
                if mp4_header(handle.read(32)):
                    return path
        except FileNotFoundError:
            pass
        return None

    async def prepare(self, audio: bytes) -> Path:
        check_pcm(audio)
        cached = self.ready(audio)
        if cached is not None:
            return cached
        return await self._pending.get(str(self._path(audio)), lambda: self._generate(audio))

    async def _generate(self, audio: bytes) -> Path:
        if not self._key:
            raise ProviderError("SIMLI_API_KEY is required to prepare uncached video")
        body = await provider_bytes(
            self._client, "POST", "https://api.simli.ai/static/audio", provider="Simli",
            limit=64 * 1024, timeout_s=90,
            headers={"x-simli-api-key": self._key},
            json={"faceId": self._face_id, "audioBase64": base64.b64encode(audio).decode("ascii"),
                  "audioFormat": "pcm16", "audioSampleRate": 16000, "audioChannelCount": 1},
        )
        try:
            result = json.loads(body)
        except (ValueError, UnicodeError):
            raise ProviderError("Simli returned an unexpected video response") from None
        url = check_download_url(result.get("mp4_url") if isinstance(result, dict) else None)
        # Signed URL is used verbatim. Never send the account API key to a download URL.
        data = await provider_bytes(
            self._client, "GET", url, provider="Simli video", limit=MAX_VIDEO_BYTES, timeout_s=30,
        )
        if not mp4_header(data):
            raise ProviderError("Simli did not return an MP4 container")
        path = self._path(audio)
        await asyncio.to_thread(private_write, path, data)
        return path

    async def aclose(self) -> None:
        await self._pending.aclose()
        await self._client.aclose()
