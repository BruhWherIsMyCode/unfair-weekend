"""In-process connection state, independent of conversation and measurement state.

Use one service on one asyncio event loop. Mutations have no await points, so a
deadline and an answer cannot interleave halfway through a transition. Returned
records are immutable snapshots, not HTTP payloads. Times use a monotonic clock.
"""

import asyncio
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from enum import StrEnum
from uuid import uuid4

from .config import positive_seconds
from .logging_config import log_event

log = logging.getLogger(__name__)


class CallState(StrEnum):
    RINGING = "ringing"
    CONNECTED = "connected"
    NO_ANSWER = "no_answer"
    ENDED = "ended"


class EndReason(StrEnum):
    DECLINED = "declined"
    RING_TIMEOUT = "ring_timeout"
    COMPLETED = "completed"
    HANGUP = "hangup"
    CONNECTION_LOST = "connection_lost"
    DEADLINE = "deadline"
    SHUTDOWN = "shutdown"


class CallConflict(Exception):
    """The requested transition conflicts with the current connection state."""


class CallNotFound(LookupError):
    """The call is not held by this process."""


@dataclass(frozen=True, slots=True)
class Call:
    call_id: str
    person_id: str
    state: CallState
    created_at: float
    ring_deadline: float
    answered_at: float | None = None
    ended_at: float | None = None
    reason: EndReason | None = None

    @property
    def terminal(self) -> bool:
        return self.state in (CallState.NO_ANSWER, CallState.ENDED)


class CallService:
    def __init__(
        self,
        *,
        ring_timeout_s: float = 30.0,
        call_timeout_s: float = 180.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        positive_seconds(ring_timeout_s, "ring_timeout_s")
        positive_seconds(call_timeout_s, "call_timeout_s")
        self._ring_timeout_s = ring_timeout_s
        self._call_timeout_s = call_timeout_s
        self._clock = clock
        self._calls: dict[str, Call] = {}
        self._active_by_person: dict[str, str] = {}
        self._ended: dict[str, asyncio.Future[Call]] = {}
        self._timers: dict[str, asyncio.Task[None]] = {}
        self._timer_tasks: set[asyncio.Task[None]] = set()
        self._closed = False

    @property
    def closed(self) -> bool:
        return self._closed

    def start(self, person_id: str) -> Call:
        if self._closed:
            raise CallConflict("Call service is closed")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", person_id):
            raise ValueError("Invalid person ID")
        if active_id := self._active_by_person.get(person_id):
            if not self.get(active_id).terminal:
                raise CallConflict("Person already has an active call")
        loop = asyncio.get_running_loop()
        now = self._clock()
        call = Call(
            call_id=f"call_{uuid4().hex}",
            person_id=person_id,
            state=CallState.RINGING,
            created_at=now,
            ring_deadline=now + self._ring_timeout_s,
        )
        self._calls[call.call_id] = call
        self._active_by_person[person_id] = call.call_id
        self._ended[call.call_id] = loop.create_future()
        self._arm_timer(call)
        log_event(log, logging.INFO, "call.started", call_id=call.call_id, state=call.state,
                  ring_timeout_s=self._ring_timeout_s)
        return call

    def get(self, call_id: str) -> Call:
        try:
            call = self._calls[call_id]
        except KeyError:
            raise CallNotFound("Unknown call") from None
        return self._expire(call)

    def answer(self, call_id: str) -> Call:
        call = self.get(call_id)
        if call.state == CallState.CONNECTED:
            return call
        if call.state != CallState.RINGING:
            raise CallConflict("Call is no longer ringing")
        call = replace(call, state=CallState.CONNECTED, answered_at=self._clock())
        self._calls[call_id] = call
        self._arm_timer(call)
        log_event(log, logging.INFO, "call.answered", call_id=call_id,
                  ring_duration_ms=round((call.answered_at - call.created_at) * 1000, 2))
        return call

    def decline(self, call_id: str) -> Call:
        call = self.get(call_id)
        if call.reason == EndReason.DECLINED:
            return call
        if call.state != CallState.RINGING:
            raise CallConflict("Call is no longer ringing")
        return self._finish(call, CallState.NO_ANSWER, EndReason.DECLINED)

    def end(self, call_id: str, reason: EndReason = EndReason.HANGUP) -> Call:
        if reason not in (EndReason.COMPLETED, EndReason.HANGUP, EndReason.CONNECTION_LOST):
            raise ValueError("Invalid explicit end reason")
        call = self.get(call_id)
        if call.terminal:
            return call
        if reason == EndReason.COMPLETED and call.state != CallState.CONNECTED:
            raise CallConflict("Call has not been answered")
        return self._finish(call, CallState.ENDED, reason)

    async def wait_ended(self, call_id: str) -> Call:
        self.get(call_id)
        # A disconnected consumer must not cancel the shared terminal future.
        return await asyncio.shield(self._ended[call_id])

    async def aclose(self) -> None:
        self._closed = True
        timers = list(self._timer_tasks)
        for call_id in list(self._calls):
            call = self.get(call_id)
            if not call.terminal:
                self._finish(call, CallState.ENDED, EndReason.SHUTDOWN)
        await asyncio.gather(*timers, return_exceptions=True)

    def _deadline(self, call: Call) -> float:
        if call.state == CallState.RINGING:
            return call.ring_deadline
        assert call.answered_at is not None
        return call.answered_at + self._call_timeout_s

    def _expire(self, call: Call) -> Call:
        if not call.terminal and self._clock() >= self._deadline(call):
            if call.state == CallState.RINGING:
                return self._finish(call, CallState.NO_ANSWER, EndReason.RING_TIMEOUT)
            return self._finish(call, CallState.ENDED, EndReason.DEADLINE)
        return call

    def _arm_timer(self, call: Call) -> None:
        if previous := self._timers.pop(call.call_id, None):
            previous.cancel()
        timer = asyncio.create_task(
            self._watch_deadline(call.call_id), name=f"deadline:{call.call_id}"
        )
        self._timers[call.call_id] = timer
        self._timer_tasks.add(timer)
        timer.add_done_callback(self._timer_tasks.discard)

    async def _watch_deadline(self, call_id: str) -> None:
        while True:
            call = self.get(call_id)
            if call.terminal:
                return
            await asyncio.sleep(max(0.0, self._deadline(call) - self._clock()))

    def _finish(self, call: Call, state: CallState, reason: EndReason) -> Call:
        call = replace(call, state=state, reason=reason, ended_at=self._clock())
        self._calls[call.call_id] = call
        self._active_by_person.pop(call.person_id, None)
        if timer := self._timers.pop(call.call_id, None):
            if timer is not asyncio.current_task():
                timer.cancel()
        self._ended[call.call_id].set_result(call)
        level = logging.WARNING if reason in (EndReason.DEADLINE, EndReason.CONNECTION_LOST) else logging.INFO
        log_event(log, level, "call.ended", call_id=call.call_id, state=state, reason=reason,
                  duration_ms=round((call.ended_at - call.created_at) * 1000, 2))
        return call
