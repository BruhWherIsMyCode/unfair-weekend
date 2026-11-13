"""The link between the client program and the server program (protocol version 2, see protocol.md).

One JSON object per message; every message has `type`, `call_id` (null before a call) and `seq` (the sender's own
counter). Program logic only ever uses the `Transport` interface (send, recv, close):

    MemoryTransport.pair()   in-memory, for tests and the offline demo
    WebSocketTransport       a real websocket; `websockets` (>= 12) is imported ONLY in this file

`Endpoint` wraps a transport with the sender's seq counter and the current call id. `validate_client_message` and
`validate_server_message` check one received message and return a plain error text or None; the receiver logs it
and answers `error`, it never crashes.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

PROTOCOL_VERSION = 2  # v2 adds speech; a v1 client (no speech) still works
SUPPORTED_PROTOCOLS = (1, 2)
MAX_TEXT_CHARS = 2000


class TransportClosed(Exception):
    """The other side closed the link (or it broke)."""


class Transport:
    """What all program logic uses. Messages are dicts that survive json.dumps."""

    async def send(self, message: Dict[str, Any]) -> None:
        raise NotImplementedError

    async def recv(self) -> Dict[str, Any]:
        """The next message; raises TransportClosed when the link is closed."""
        raise NotImplementedError

    async def close(self) -> None:
        raise NotImplementedError


_CLOSED = object()


class MemoryTransport(Transport):
    def __init__(self) -> None:
        self._inbox: "asyncio.Queue" = asyncio.Queue()
        self._peer: Optional["MemoryTransport"] = None
        self.closed = False

    @classmethod
    def pair(cls) -> Tuple["MemoryTransport", "MemoryTransport"]:
        a, b = cls(), cls()
        a._peer, b._peer = b, a
        return a, b

    async def send(self, message: Dict[str, Any]) -> None:
        if self.closed or self._peer is None or self._peer.closed:
            raise TransportClosed()
        self._peer._inbox.put_nowait(json.loads(json.dumps(message)))  # a copy that is plain JSON, like the wire

    async def recv(self) -> Dict[str, Any]:
        if self.closed and self._inbox.empty():
            raise TransportClosed()
        item = await self._inbox.get()
        if item is _CLOSED:
            self.closed = True
            raise TransportClosed()
        return item

    async def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        if self._peer is not None:
            self._peer._inbox.put_nowait(_CLOSED)
        self._inbox.put_nowait(_CLOSED)


class WebSocketTransport(Transport):
    """A websocket connection (client or server side). A message that is not a JSON object is passed on as
    {"type": "_invalid_json"} so the receiver answers `error` like for any other invalid message."""

    def __init__(self, websocket: Any) -> None:
        self._ws = websocket

    async def send(self, message: Dict[str, Any]) -> None:
        try:
            await self._ws.send(json.dumps(message))
        except Exception as exc:
            raise TransportClosed() from exc

    async def recv(self) -> Dict[str, Any]:
        try:
            raw = await self._ws.recv()
        except Exception as exc:  # ConnectionClosed and friends
            raise TransportClosed() from exc
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            return {"type": "_invalid_json"}
        return data if isinstance(data, dict) else {"type": "_invalid_json"}

    async def close(self) -> None:
        try:
            await self._ws.close()
        except Exception:
            pass


async def connect(url: str) -> WebSocketTransport:
    import websockets  # imported only here

    return WebSocketTransport(await websockets.connect(url))


class WebSocketServer:
    def __init__(self, server: Any, host: str, port: int) -> None:
        self._server = server
        self.host = host
        self.port = port

    async def close(self) -> None:
        self._server.close()
        await self._server.wait_closed()


async def serve(handler: Callable[[Transport], Awaitable[None]], host: str, port: int) -> WebSocketServer:
    """Listen on host:port; `handler(transport)` runs once per connection. Port 0 picks a free port (tests)."""
    import websockets  # imported only here

    async def on_connection(websocket: Any) -> None:
        transport = WebSocketTransport(websocket)
        try:
            await handler(transport)
        finally:
            await transport.close()

    server = await websockets.serve(on_connection, host, port)
    real_port = port
    for sock in getattr(server, "sockets", None) or []:
        real_port = sock.getsockname()[1]
        break
    return WebSocketServer(server, host, real_port)


class Endpoint:
    """One side of the link: numbers its messages and stamps the call id."""

    def __init__(self, transport: Transport) -> None:
        self.transport = transport
        self.call_id: Optional[str] = None
        self._seq = 0

    async def send(self, type_: str, **fields: Any) -> Dict[str, Any]:
        self._seq += 1
        message = {"type": type_, "call_id": self.call_id, "seq": self._seq}
        message.update(fields)
        await self.transport.send(message)
        return message

    async def recv(self) -> Dict[str, Any]:
        return await self.transport.recv()

    async def close(self) -> None:
        await self.transport.close()


# ---- validation -----------------------------------------------------------------------------------------

def _is_int(x: Any) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def _is_num(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _is_str(x: Any) -> bool:
    return isinstance(x, str) and len(x) <= MAX_TEXT_CHARS


def _is_id(x: Any) -> bool:
    return isinstance(x, str) and 0 < len(x) <= 128


def _opt(check: Callable[[Any], bool]) -> Callable[[Any], bool]:
    return lambda x: x is None or check(x)


def _enum(*values: str) -> Callable[[Any], bool]:
    return lambda x: x in values


def _capabilities(x: Any) -> bool:
    return (isinstance(x, dict) and isinstance(x.get("camera"), bool) and isinstance(x.get("microphone"), bool)
            and isinstance(x.get("speech"), bool) and isinstance(x.get("measures"), list)
            and all(isinstance(m, str) for m in x["measures"]))


def _phases(x: Any) -> bool:
    return (isinstance(x, list) and all(isinstance(p, list) and len(p) == 4 and isinstance(p[0], str)
                                        and _is_int(p[1]) and _is_int(p[2]) and (p[3] is None or isinstance(p[3], str))
                                        for p in x))


MAX_WORDS = 200
MAX_AUDIO_B64_CHARS = 700_000  # a websocket message stays under the default 1 MiB limit


def _words(x: Any) -> bool:
    """Speech-to-text words: [{"text", "start", "end", "logprob"}], start / end / logprob may be null."""
    return (isinstance(x, list) and len(x) <= MAX_WORDS
            and all(isinstance(w, dict) and isinstance(w.get("text"), str) and len(w["text"]) <= 100
                    and all(w.get(k) is None or _is_num(w.get(k)) for k in ("start", "end", "logprob"))
                    for w in x))


def _audio_b64(x: Any) -> bool:
    return isinstance(x, str) and len(x) <= MAX_AUDIO_B64_CHARS


def _cues(x: Any) -> bool:
    return isinstance(x, list) and all(isinstance(c, dict) and _is_int(c.get("at_ms")) and _is_id(c.get("line_id"))
                                       for c in x)


# type -> {field: checker}. Fields not listed are tolerated (and ignored); fields listed are required unless the
# checker accepts None and the key is in OPTIONAL_KEYS.
CLIENT_FIELDS: Dict[str, Dict[str, Callable[[Any], bool]]] = {
    "hello": {"protocol": _is_int, "client": _is_str, "person_id": _is_id, "capabilities": _capabilities},
    "start_call": {},
    "status": {"camera": _enum("available", "unavailable"), "camera_reason": _opt(_is_str),
               "microphone": _enum("available", "unavailable"), "text": _is_str},
    "capture_started": {"attempt_id": _is_id},
    "reading": {"attempt_id": _is_id, "sequence": _is_int, "captured_ms": _is_int, "task": _is_id,
                "data": _opt(lambda d: isinstance(d, dict))},
    "capture_ended": {"attempt_id": _is_id},
    "cancel_ack": {"attempt_id": _is_id},
    "said": {"line_id": _is_id},
    "answer": {"question_id": _is_id, "transcript": _opt(_is_str)},
    "hang_up": {},
    # protocol v2 (speech)
    "stt_token_request": {"purpose": _enum("answer", "sentence"), "ref": _is_id},
    "speech_result": {"attempt_id": _is_id, "transcript": _opt(_is_str), "words": _words,
                      "duration_recorded_s": _is_num},
}
SERVER_FIELDS: Dict[str, Dict[str, Callable[[Any], bool]]] = {
    "hello_ok": {"protocol": _is_int, "server_version": _is_str},
    "say": {"line_id": _is_id, "text": _is_str},
    "listen": {"question_id": _is_id, "timeout_s": _is_num},
    "start_capture": {"attempt_id": _is_id, "task": _is_id, "settings": lambda d: isinstance(d, dict),
                      "cues": _cues},
    "cancel_capture": {"attempt_id": _is_id, "reason": _is_str},
    "progress": {"text": _is_str},
    "call_result": {"summary": lambda d: isinstance(d, dict), "family_text": _is_str},
    "error": {"code": _is_id, "text": _is_str},
    "stt_token": {"ref": _is_id, "token": _opt(_is_str), "error": _opt(_is_str)},  # protocol v2
}
OPTIONAL_FIELDS = {("start_capture", "phases"): _opt(_phases),  # arms only
                   ("say", "audio_pcm16k_b64"): _opt(_audio_b64)}  # v2: ElevenLabs line audio
CLIENT_OPTIONAL_FIELDS = {("answer", "words"): _opt(_words), ("answer", "stt_error"): _opt(_is_str),
                          ("speech_result", "stt_error"): _opt(_is_str)}  # v2: speech


def _validate(message: Any, table: Dict[str, Dict[str, Callable[[Any], bool]]], optional: Dict[Tuple[str, str], Any]
              ) -> Optional[str]:
    if not isinstance(message, dict):
        return "message is not a JSON object"
    kind = message.get("type")
    if not isinstance(kind, str):
        return "missing type"
    if kind not in table:
        return "unknown type %s" % kind[:40]
    if "call_id" not in message or not (message["call_id"] is None or _is_id(message["call_id"])):
        return "call_id must be present (a string or null)"
    if not _is_int(message.get("seq")):
        return "seq must be an integer"
    for name, check in table[kind].items():
        if name not in message:
            return "%s: missing field %s" % (kind, name)
        if not check(message[name]):
            return "%s: bad value for %s" % (kind, name)
    for (type_, name), check in optional.items():
        if type_ == kind and name in message and not check(message[name]):
            return "%s: bad value for %s" % (kind, name)
    return None


def validate_client_message(message: Any) -> Optional[str]:
    """None when a client -> server message is well formed, else a short plain reason."""
    return _validate(message, CLIENT_FIELDS, CLIENT_OPTIONAL_FIELDS)


def validate_server_message(message: Any) -> Optional[str]:
    """None when a server -> client message is well formed, else a short plain reason."""
    return _validate(message, SERVER_FIELDS, OPTIONAL_FIELDS)


def client_message_types() -> List[str]:
    return list(CLIENT_FIELDS)


def server_message_types() -> List[str]:
    return list(SERVER_FIELDS)
