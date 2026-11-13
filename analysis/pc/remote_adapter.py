"""RemoteAdapter: the CallAdapter Protocol (analysis/session/adapters.py) over a link Endpoint.

The server's receive loop validates each client message and hands it to `dispatch`. The adapter turns the session's
calls into messages and the client's messages into what the session expects:

    say            -> `say` {line_id, text}; waits for `said` (limits.PREPARE_PLAYBACK_S on the clock, then logs and returns)
    listen         -> `listen`; waits for `answer` (None for a null transcript or after timeout_s + a short grace)
    start_capture  -> `start_capture` {attempt_id, task, settings, cues, phases (arms only)}
    cancel_capture -> `cancel_capture`
    samples        -> one sample per `reading` (format "reading", data = the reading dict); ends on `capture_ended`
    capture_started / cancel_ack -> session.handle_capture_started / handle_cancel_ack
    status         -> CallSession resource events (camera / microphone), only on a change
    hang_up        -> session.hang_up()
    publish        -> the family stand-in (family.py)

After the client is gone every call returns at once and sends nothing.
"""
from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator, Dict, List, Optional

from analysis.pc.link import Endpoint, TransportClosed
from analysis.session import limits

_END = object()
_CAMERA_REASONS = {"camera_blocked": "track_ended", "camera_lost": "device_missing",
                   "permission_denied": "permission_denied", "track_ended": "track_ended",
                   "device_missing": "device_missing"}
_PROGRESS_TEXT = {"look_at_camera": "Please look at the camera.", "show_both_arms": "Please keep your hand beside your face."}
_ANSWER_GRACE_S = 2.0


def line_text(line_id: str, script: Dict[str, str], name: str) -> str:
    return script.get(line_id, line_id).replace("{name}", name)


class RemoteAdapter:
    def __init__(self, endpoint: Endpoint, script: Dict[str, str], person_name: str, clock: Any, family: Any,
                 initial_resources: Optional[Dict[str, str]] = None) -> None:
        self.endpoint = endpoint
        self.script = script
        self.person_name = person_name
        self.clock = clock
        self.family = family
        self.session: Any = None  # set by the server, as with FakeAdapter
        self.gone = False
        self._said: Dict[str, List["asyncio.Future"]] = {}
        self._answers: Dict[str, List["asyncio.Future"]] = {}
        self._queues: Dict[str, "asyncio.Queue"] = {}
        self._closed_attempts: set = set()
        self._states: Dict[str, str] = dict(initial_resources or {})
        self._resource_n = 0
        self._resource_seq: Dict[str, int] = {}
        self._last_progress: Optional[str] = None

    # ---- helpers -------------------------------------------------------------------------------------

    def _note(self, kind: str, **fields: Any) -> None:
        if self.session is not None:
            self.session.note(kind, **fields)

    async def _send(self, type_: str, **fields: Any) -> bool:
        if self.gone:
            return False
        try:
            await self.endpoint.send(type_, **fields)
            return True
        except TransportClosed:
            self.connection_lost()
            return False

    async def _wait(self, future: "asyncio.Future", timeout_s: float) -> bool:
        """True when the future resolved before the clock timeout."""
        sleeper = asyncio.ensure_future(self.clock.sleep(timeout_s))
        try:
            await asyncio.wait({future, sleeper}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            sleeper.cancel()
            await asyncio.gather(sleeper, return_exceptions=True)
        return future.done()

    def _release_all(self) -> None:
        for table in (self._said, self._answers):
            for futures in table.values():
                for f in futures:
                    if not f.done():
                        f.set_result(None)
        for attempt_id, queue in self._queues.items():
            if attempt_id not in self._closed_attempts:
                self._closed_attempts.add(attempt_id)
                queue.put_nowait(_END)

    def connection_lost(self) -> None:
        """The link broke or the client left: same as a hang-up."""
        if self.gone:
            return
        self.gone = True
        self._release_all()
        if self.session is not None:
            self.session.hang_up()

    def _queue(self, attempt_id: str) -> "asyncio.Queue":
        return self._queues.setdefault(attempt_id, asyncio.Queue())

    def _close_attempt(self, attempt_id: str) -> None:
        if attempt_id not in self._closed_attempts:
            self._closed_attempts.add(attempt_id)
            self._queue(attempt_id).put_nowait(_END)

    # ---- CallAdapter -----------------------------------------------------------------------------------

    async def say(self, line_id: str) -> None:
        future = asyncio.get_running_loop().create_future()
        self._said.setdefault(line_id, []).append(future)
        try:
            if not await self._send("say", line_id=line_id, text=line_text(line_id, self.script, self.person_name)):
                return
            if not await self._wait(future, limits.PREPARE_PLAYBACK_S):
                self._note("say_timeout", line=line_id)
        finally:
            waiters = self._said.get(line_id, [])
            if future in waiters:
                waiters.remove(future)

    async def listen(self, question_id: str, timeout_s: float) -> Optional[str]:
        future = asyncio.get_running_loop().create_future()
        self._answers.setdefault(question_id, []).append(future)
        try:
            if not await self._send("listen", question_id=question_id, timeout_s=float(timeout_s)):
                return None
            if not await self._wait(future, timeout_s + _ANSWER_GRACE_S):
                self._note("listen_timeout", question=question_id)
                return None
            result = future.result()
            return result if isinstance(result, str) else None
        finally:
            waiters = self._answers.get(question_id, [])
            if future in waiters:
                waiters.remove(future)

    async def start_capture(self, attempt_id: str, task: str, settings: Dict[str, Any]) -> None:
        self._queue(attempt_id)  # readings that arrive at once are kept
        module = self.session.modules.get(task) if self.session is not None else None
        cues: List[Dict[str, Any]] = []
        phases = None
        if module is not None:
            cues = [dict(c) for c in (module.definition().get("cues") or [])]
            if task == "arms":
                phases = [list(p) for p in getattr(module, "phases", limits.ARMS_PHASES)]
        fields: Dict[str, Any] = {"attempt_id": attempt_id, "task": task, "settings": dict(settings), "cues": cues}
        if phases is not None:
            fields["phases"] = phases
        await self._send("start_capture", **fields)

    async def cancel_capture(self, attempt_id: str, reason: str) -> None:
        await self._send("cancel_capture", attempt_id=attempt_id, reason=reason)

    def samples(self, attempt_id: str) -> AsyncIterator[Dict[str, Any]]:
        queue = self._queue(attempt_id)

        async def gen() -> AsyncIterator[Dict[str, Any]]:
            while True:
                item = await queue.get()
                if item is _END:
                    return
                yield item

        return gen()

    async def publish(self, summary: Dict[str, Any], phase: str, message_id: Optional[str] = None) -> Dict[str, Any]:
        return await self.family.publish(summary, phase, message_id)

    async def send_progress(self, event: Dict[str, Any]) -> None:
        text = _PROGRESS_TEXT.get(event.get("hint") or "")
        if text is not None and text != self._last_progress:
            self._last_progress = text
            await self._send("progress", text=text)
        elif text is None:
            self._last_progress = None

    # ---- messages from the client (already validated) --------------------------------------------------

    def set_states(self, states: Dict[str, str]) -> None:
        """Remember the resource states without raising events (used right before a call starts)."""
        self._states.update(states)

    async def dispatch(self, message: Dict[str, Any]) -> None:
        kind = message["type"]
        if kind == "said":
            waiters = self._said.get(message["line_id"]) or []
            for f in list(waiters):
                if not f.done():
                    f.set_result(True)
                    break
        elif kind == "answer":
            waiters = self._answers.get(message["question_id"]) or []
            for f in list(waiters):
                if not f.done():
                    f.set_result(message.get("transcript"))
                    break
        elif kind == "reading":
            attempt_id = message["attempt_id"]
            if attempt_id in self._closed_attempts:
                self._note("reading_ignored", attempt_id=attempt_id, reason="capture_closed")
                return
            self._queue(attempt_id).put_nowait({"sequence": message["sequence"], "captured_ms": message["captured_ms"],
                                                "format": "reading", "data": message.get("data")})
        elif kind == "capture_ended":
            self._close_attempt(message["attempt_id"])
        elif kind == "capture_started":
            if self.session is not None:
                self.session.handle_capture_started(message["attempt_id"])
        elif kind == "cancel_ack":
            if self.session is not None:
                self.session.handle_cancel_ack(message["attempt_id"])
            self._close_attempt(message["attempt_id"])
        elif kind == "status":
            self._on_status(message)
        elif kind == "hang_up":
            self.gone = True
            self._release_all()
            if self.session is not None:
                self.session.hang_up()

    def _on_status(self, message: Dict[str, Any]) -> None:
        new = {"camera": message["camera"], "microphone": message["microphone"]}
        for resource, state in new.items():
            if self._states.get(resource) == state:
                continue
            self._states[resource] = state
            if self.session is None:
                continue
            reason = None
            if state == "unavailable":
                raw = message.get("camera_reason") if resource == "camera" else "device_missing"
                reason = _CAMERA_REASONS.get(raw or "", "unknown") if resource == "camera" else "device_missing"
            self._resource_n += 1
            seq = self._resource_seq.get(resource, 0) + 1
            self._resource_seq[resource] = seq
            self.session.handle_resource_event({
                "event_id": "res_%s_%d" % (resource, self._resource_n), "type": "resource.changed",
                "command_id": None, "resource": resource, "state": state, "sequence": seq, "reason": reason})
