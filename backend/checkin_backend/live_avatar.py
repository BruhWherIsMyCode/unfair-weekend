"""Mint short-lived Simli live-avatar sessions for the parent phone.

The phone streams each line's PCM to Simli and shows the avatar Simli sends back (LiveKit video and audio).
Only the session token reaches the phone; the account key stays here. Prepared clips remain the fallback.
"""

import json
import os

import httpx

from .avatar import DEFAULT_FACE_ID
from .provider_io import ProviderError, provider_bytes

TOKEN_URL = "https://api.simli.ai/compose/token"
# The WebSocket the phone opens with the token: Simli's LiveKit transport (simli-client 3.2.0).
LIVE_WS_URL = "wss://api.simli.ai/compose/webrtc/livekit"
# The avatar stays connected through answers and tasks, when no audio is sent for a while.
MAX_IDLE_S = 120


class LiveAvatarGateway:
    def __init__(self, *, api_key: str = "", face_id: str = DEFAULT_FACE_ID, enabled: bool = True,
                 max_session_s: int = 300, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._key = api_key.strip()
        self._face_id = face_id
        self._enabled = enabled
        self._max_session_s = max_session_s
        self._client = httpx.AsyncClient(timeout=20, transport=transport, follow_redirects=False)

    @classmethod
    def from_environment(cls, *, call_timeout_s: float = 180.0) -> "LiveAvatarGateway":
        mode = os.environ.get("AVATAR_MODE", "live").strip().lower()
        if mode not in ("live", "clips"):
            raise ValueError("AVATAR_MODE must be live or clips")
        return cls(
            api_key=os.environ.get("SIMLI_API_KEY") or os.environ.get("SMILI_API_KEY", ""),
            face_id=os.environ.get("SIMLI_FACE_ID", DEFAULT_FACE_ID),
            enabled=mode == "live",
            max_session_s=int(call_timeout_s) + 60,
        )

    @property
    def available(self) -> bool:
        return self._enabled and bool(self._key)

    async def session(self) -> dict[str, str]:
        if not self.available:
            raise ProviderError("The live avatar is off or SIMLI_API_KEY is missing")
        body = await provider_bytes(
            self._client, "POST", TOKEN_URL, provider="Simli", limit=64 * 1024, timeout_s=20,
            headers={"x-simli-api-key": self._key},
            json={"faceId": self._face_id, "handleSilence": True, "maxSessionLength": self._max_session_s,
                  "maxIdleTime": MAX_IDLE_S, "audioInputFormat": "pcm16"},
        )
        try:
            token = json.loads(body).get("session_token")
        except (ValueError, UnicodeError, AttributeError):
            token = None
        if not isinstance(token, str) or not token:
            raise ProviderError("Simli did not return a session token")
        return {"session_token": token, "ws_url": LIVE_WS_URL}

    async def aclose(self) -> None:
        await self._client.aclose()
