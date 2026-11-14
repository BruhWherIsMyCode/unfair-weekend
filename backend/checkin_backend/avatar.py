"""Prepare Simli video clips before calls; calls serve only local cached MP4s."""

import asyncio
import base64
import hashlib
import json
import math
import os
from asyncio import sleep
from email.utils import parsedate_to_datetime
from pathlib import Path
from time import time
from urllib.parse import urlsplit

import httpx

from .provider_io import PendingAssets, ProviderError, ProviderHTTPError, private_write, provider_bytes
from .speech import check_pcm

MAX_VIDEO_BYTES = 30 * 1024 * 1024
DEFAULT_FACE_ID = "fa57b877-4b8a-4650-bc43-4d52ae795cdc"
MAX_DOWNLOAD_ATTEMPTS = 20
DOWNLOAD_WAIT_TIMEOUT_S = 300.0
MAX_ETA_WAIT_S = 60.0


def mp4_header(data: bytes) -> bool:
    # This is a container check, not a decoder or a playback-quality assessment.
    return len(data) >= 16 and data[4:8] == b"ftyp" and int.from_bytes(data[:4], "big") >= 16


def check_download_url(value: object, *, cdn_redirect: bool = False) -> str:
    if not isinstance(value, str):
        raise ProviderError("Simli did not return an MP4 URL")
    try:
        url = urlsplit(value)
        host = "simlimicrocdn.blob.core.windows.net" if cdn_redirect else "api.simli.ai"
        prefixes = ("/mp4/",) if cdn_redirect else ("/static/mp4/", "/mp4/")
        permitted = (
            url.scheme == "https" and url.hostname == host
            and url.port in (None, 443) and url.username is None and url.password is None
            and not url.fragment and url.path.startswith(prefixes)
        )
    except ValueError:
        permitted = False
    if not permitted:
        raise ProviderError("Simli returned an unexpected MP4 location")
    return value


def eta_wait(value: object) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
        return 0.0
    if isinstance(value, float) and not math.isfinite(value):
        return 0.0
    return min(value, MAX_ETA_WAIT_S - 1.0) + 1.0


def retry_after_wait(value: str | None) -> float:
    if value is None:
        return 0.0
    try:
        seconds = float(value)
    except ValueError:
        try:
            seconds = parsedate_to_datetime(value).timestamp() - time()
        except (ValueError, TypeError, OverflowError):
            return 0.0
    return max(0.0, seconds) if math.isfinite(seconds) else 0.0


class ClipGateway:
    def __init__(
        self, *, api_key: str = "", face_id: str = DEFAULT_FACE_ID,
        cache_dir: Path | None = None, transport: httpx.AsyncBaseTransport | None = None,
        enabled: bool = True,
    ) -> None:
        self._key = api_key.strip()
        self._face_id = face_id
        self._enabled = enabled
        self._cache_dir = cache_dir or Path(__file__).resolve().parents[1] / ".cache" / "avatar"
        self._client = httpx.AsyncClient(timeout=90, transport=transport, follow_redirects=False)
        self._pending = PendingAssets()

    @classmethod
    def from_environment(cls) -> "ClipGateway":
        mode = os.environ.get("AVATAR_PREGENERATED_CLIPS", "on").strip().lower()
        if mode not in ("on", "off"):
            raise ValueError("AVATAR_PREGENERATED_CLIPS must be on or off")
        return cls(
            api_key=os.environ.get("SIMLI_API_KEY") or os.environ.get("SMILI_API_KEY", ""),
            face_id=os.environ.get("SIMLI_FACE_ID", DEFAULT_FACE_ID),
            enabled=mode == "on",
        )

    def _path(self, audio: bytes) -> Path:
        key = hashlib.sha256(json.dumps([
            "simli-static-v1", self._face_id, hashlib.sha256(audio).hexdigest(), "pcm16", 16000, 1,
        ]).encode()).hexdigest()
        return self._cache_dir / f"{key}.mp4"

    @property
    def enabled(self) -> bool:
        return self._enabled

    def ready(self, audio: bytes) -> Path | None:
        """Never performs generation or network I/O on the call's critical path."""
        if not self.enabled:
            return None
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
        if not self.enabled:
            raise ProviderError("Prepared avatar videos are disabled")
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
        # Simli returns before rendering finishes. Retrying GET must never repeat the paid POST.
        data = await self._download(url, eta_wait(result.get("mp4_availablility_eta_seconds")))
        if not mp4_header(data):
            raise ProviderError("Simli did not return an MP4 container")
        path = self._path(audio)
        await asyncio.to_thread(private_write, path, data)
        return path

    async def _download(self, url: str, initial_wait_s: float) -> bytes:
        redirected = False
        delay_s = 3.0

        async def fetch() -> bytes:
            # Preserve signed query parameters; downloads never receive the account API key.
            return await provider_bytes(
                self._client, "GET", url, provider="Simli video", limit=MAX_VIDEO_BYTES, timeout_s=30,
            )

        try:
            async with asyncio.timeout(DOWNLOAD_WAIT_TIMEOUT_S):
                if initial_wait_s:
                    await sleep(initial_wait_s)
                for attempt in range(MAX_DOWNLOAD_ATTEMPTS):
                    try:
                        try:
                            return await fetch()
                        except ProviderHTTPError as exc:
                            if exc.status_code != 307 or redirected:
                                raise
                            url = check_download_url(exc.location, cdn_redirect=True)
                            redirected = True
                            return await fetch()
                    except ProviderHTTPError as exc:
                        if exc.status_code not in (404, 429):
                            raise
                        if attempt + 1 == MAX_DOWNLOAD_ATTEMPTS:
                            break
                        await sleep(max(delay_s, retry_after_wait(exc.retry_after)))
                        delay_s = min(delay_s * 1.5, 15.0)
        except TimeoutError:
            raise ProviderError("Simli video did not become ready within the time limit") from None
        raise ProviderError("Simli video did not become ready within the download attempt limit")

    async def aclose(self) -> None:
        await self._pending.aclose()
        await self._client.aclose()
