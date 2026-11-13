"""What the call session needs from Agent 2's modules, as typing Protocols, plus fakes for tests and the demo.

Nothing here touches the network. Samples are MediaSample-shaped dicts: `sequence`, `captured_ms`, `format`
and `data_base64` (str), or `data` (bytes) when the media ingress has already decoded them.

Client acknowledgments (capture.started, command.cancelled) are not adapter calls: Agent 2's call service
reports them to the session with `handle_capture_started` and `handle_cancel_ack`.
"""
from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator, Dict, List, Optional, Protocol, Tuple, runtime_checkable


@runtime_checkable
class CallAdapter(Protocol):
    async def say(self, line_id: str) -> None:
        """Play a fixed script line on the client; return when actual playback has ended."""

    async def start_capture(self, attempt_id: str, task: str, settings: Dict[str, Any]) -> None:
        """Send the capture command for this attempt (command_id == attempt_id)."""

    async def cancel_capture(self, attempt_id: str, reason: str) -> None:
        """Send command.cancel for this attempt. Duplicates are harmless."""

    async def listen(self, question_id: str, timeout_s: float) -> Optional[str]:
        """The transcript of her answer after a question line played (Agent 2's speech-to-text).
        None when nothing was heard within `timeout_s`."""

    def samples(self, attempt_id: str) -> AsyncIterator[Dict[str, Any]]:
        """The ordered samples of this attempt; the iterator ends when the client finished its input."""

    async def publish(self, summary: Dict[str, Any], phase: str, message_id: Optional[str] = None) -> Dict[str, Any]:
        """Family delivery. `summary` is a SessionResult dict, `phase` is 'mid_call' or 'final'.
        Returns {'message_id': str, 'delivery': str}; a later publish for the same call updates the same message."""

    async def send_progress(self, event: Dict[str, Any]) -> None:
        """Optional neutral task.progress projection for the parent screen."""


_THING = {"face": ("smile", "looks"), "arm": ("arm", "looks"), "speech": ("speech", "sounds")}


def render_family_text(summary: Dict[str, Any], name: str) -> str:
    """A stand-in for Agent 2's approved templates, used by the fake and the demo only."""
    status = summary.get("status")
    if status == "change_noticed":
        confirmed = [s for s in summary["signs"] if s["confirmed"]]
        noun, verb = _THING[confirmed[0]["sign"]]
        return "%s's %s %s different from her usual. Call her now. If she seems unwell, call 112." % (name, noun, verb)
    if status == "incomplete":
        return "%s's check-in could not be finished. Please call her. If she seems unwell, call 112." % name
    if status == "learning":
        return "%s's check-in is done. We are still learning her usual, so there is nothing to compare yet." % name
    return "%s's check-in today looked as usual." % name


class FakeAdapter:
    """Records every call. Tests push samples and trigger client events by hand."""

    CLIENT_CALLS = ("say", "start_capture", "cancel_capture", "listen")  # calls that go to the parent client
    SILENT = None  # a scripted answer meaning "she said nothing"

    def __init__(self, person_name: str = "Ona", message_id: str = "msg_demo",
                 auto_ack_start: bool = True, auto_ack_cancel: bool = True,
                 answers: Optional[Dict[str, Any]] = None) -> None:
        # question_id -> transcript, or SILENT, or a list consumed one entry per listen() (e.g. [SILENT, "yes"]).
        # A question that is not scripted is silent.
        self.answers: Dict[str, Any] = dict(answers or {})
        self.silent = False  # True: every listen() hears nothing
        self.person_name = person_name
        self.message_id = message_id
        self.auto_ack_start = auto_ack_start
        self.auto_ack_cancel = auto_ack_cancel
        self.session: Any = None  # set to the CallSession so the fake client can acknowledge
        self.calls: List[Tuple[str, tuple]] = []
        self.said: List[str] = []
        self.say_gate: Optional[asyncio.Event] = None  # when set, say() waits for it (a long playback)
        self.fail_say = False
        self.progress: List[Dict[str, Any]] = []
        self.published: List[Dict[str, Any]] = []
        self._queues: Dict[str, "asyncio.Queue"] = {}

    def client_calls(self) -> List[Tuple[str, tuple]]:
        return [c for c in self.calls if c[0] in self.CLIENT_CALLS]

    def _queue(self, attempt_id: str) -> "asyncio.Queue":
        return self._queues.setdefault(attempt_id, asyncio.Queue())

    async def say(self, line_id: str) -> None:
        self.calls.append(("say", (line_id,)))
        if self.fail_say:
            raise RuntimeError("fake playback failed")
        if self.say_gate is not None:
            await self.say_gate.wait()
        self.said.append(line_id)

    async def start_capture(self, attempt_id: str, task: str, settings: Dict[str, Any]) -> None:
        self.calls.append(("start_capture", (attempt_id, task)))
        self._queue(attempt_id)
        if self.auto_ack_start and self.session is not None:
            self.session.handle_capture_started(attempt_id)

    async def cancel_capture(self, attempt_id: str, reason: str) -> None:
        self.calls.append(("cancel_capture", (attempt_id, reason)))
        self._queue(attempt_id).put_nowait(None)
        if self.auto_ack_cancel and self.session is not None:
            self.session.handle_cancel_ack(attempt_id)

    async def listen(self, question_id: str, timeout_s: float) -> Optional[str]:
        self.calls.append(("listen", (question_id,)))
        if self.silent:
            return None
        scripted = self.answers.get(question_id)
        if isinstance(scripted, list):
            scripted = scripted.pop(0) if scripted else None
        return scripted

    def samples(self, attempt_id: str) -> AsyncIterator[Dict[str, Any]]:
        self.calls.append(("samples", (attempt_id,)))
        queue = self._queue(attempt_id)

        async def gen() -> AsyncIterator[Dict[str, Any]]:
            while True:
                item = await queue.get()
                if item is None:
                    return
                yield item

        return gen()

    async def publish(self, summary: Dict[str, Any], phase: str, message_id: Optional[str] = None) -> Dict[str, Any]:
        self.calls.append(("publish", (phase,)))
        self.published.append({"phase": phase, "summary": summary, "message_id": self.message_id,
                               "text": render_family_text(summary, self.person_name)})
        return {"message_id": self.message_id, "delivery": "stored"}

    async def send_progress(self, event: Dict[str, Any]) -> None:
        self.calls.append(("send_progress", (event["attempt_id"],)))
        self.progress.append(event)

    # ---- test helpers: what the client would do ----------------------------------------------------

    def push_sample(self, attempt_id: str, sample: Dict[str, Any]) -> None:
        self._queue(attempt_id).put_nowait(sample)

    def finish_capture(self, attempt_id: str) -> None:
        self._queue(attempt_id).put_nowait(None)
