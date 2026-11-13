"""One attempt of one task: the state machine and its input bookkeeping.

States: preparing -> capturing -> finalizing -> completed, or any active state -> stopping ->
interrupted / failed / cancelled. An attempt finalizes exactly once. Late samples, observations or results
for a stopping or terminal attempt are refused (the caller logs them) and never change the state.

Pure Python, no I/O. Times are seconds from the session clock.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional, Tuple

from analysis.session.models import TaskInterrupt, TaskObservation

PREPARING = "preparing"
CAPTURING = "capturing"
FINALIZING = "finalizing"
COMPLETED = "completed"
STOPPING = "stopping"
INTERRUPTED = "interrupted"
FAILED = "failed"
CANCELLED = "cancelled"

ACTIVE = (PREPARING, CAPTURING, FINALIZING)
TERMINAL = (COMPLETED, INTERRUPTED, FAILED, CANCELLED)

_ALLOWED = {
    PREPARING: (CAPTURING, STOPPING),
    CAPTURING: (FINALIZING, STOPPING),
    FINALIZING: (COMPLETED, STOPPING),
    STOPPING: (INTERRUPTED, FAILED, CANCELLED),
}

# Results of accept_sample / accept_observation.
ACCEPTED = "accepted"
DUPLICATE = "duplicate"  # identical repeat: ignored
CONFLICT = "conflict"  # same id or sequence with different content, or an older unseen sequence: rejected
LATE = "late"  # attempt is stopping, terminal or its input is closed: ignored
EARLY = "early"  # no capture was commanded yet: rejected
OBSOLETE = "obsolete"  # belongs to another attempt or call: ignored


class IllegalTransition(Exception):
    pass


def _fingerprint(payload: Any) -> str:
    if isinstance(payload, (bytes, bytearray)):
        return hashlib.sha1(bytes(payload)).hexdigest()
    return hashlib.sha1(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()


class Attempt:
    def __init__(self, attempt_id: str, call_id: str, task: str, created_at: float, line_id: str,
                 ordinal: int) -> None:
        self.attempt_id = attempt_id
        self.call_id = call_id
        self.task = task
        self.line_id = line_id  # the instruction line played for this attempt
        self.ordinal = ordinal  # 1 = first attempt of this task in the call
        self.created_at = created_at
        self.state = PREPARING
        self.history: List[Tuple[float, str, str]] = [(created_at, PREPARING, "created")]
        # input bookkeeping, used by the watchdogs
        self.capture_command_at: Optional[float] = None
        self.capture_started_at: Optional[float] = None
        self.first_sample_at: Optional[float] = None
        self.last_sample_at: Optional[float] = None
        self.samples_received = 0
        self.input_closed = False
        self.last_analyzer_at: Optional[float] = None
        self.framing_bad_since: Optional[float] = None
        self.framing_bad_event_id: Optional[str] = None
        self.unperformable_event_id: Optional[str] = None
        # partial evidence: kept in the log, never a final reading
        self.observations: List[TaskObservation] = []
        self.latest_observation: Optional[TaskObservation] = None
        self._obs_ids: Dict[str, str] = {}
        self._last_obs_sequence = -1
        self._sample_prints: Dict[int, str] = {}
        self._last_sample_sequence = -1
        # stop and result
        self.stop_kind: Optional[str] = None  # "interrupted" / "failed" / "cancelled"
        self.stop_interrupt: Optional[TaskInterrupt] = None
        self.stop_reason: Optional[str] = None
        self.result: Optional[Dict[str, Any]] = None

    # ---- state ------------------------------------------------------------------------------

    @property
    def is_active(self) -> bool:
        return self.state in ACTIVE

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL

    @property
    def accepts_samples(self) -> bool:
        return self.state in (PREPARING, CAPTURING) and not self.input_closed

    def transition(self, new_state: str, now: float, why: str = "") -> None:
        if new_state not in _ALLOWED.get(self.state, ()):
            raise IllegalTransition("%s: %s -> %s" % (self.attempt_id, self.state, new_state))
        self.state = new_state
        self.history.append((now, new_state, why))

    def mark_capture_started(self, now: float) -> bool:
        """capture.started acknowledged, or the first sample arrived. True when this changed the state."""
        if self.state != PREPARING:
            return False
        self.capture_started_at = now
        self.last_analyzer_at = now
        self.transition(CAPTURING, now, "capture started")
        return True

    def close_input(self, now: float) -> bool:
        """No further samples: capturing -> finalizing. Idempotent; False when the attempt is not active."""
        if not self.is_active:
            return False
        if self.state == PREPARING:
            self.mark_capture_started(now)
        self.input_closed = True
        if self.state == CAPTURING:
            self.transition(FINALIZING, now, "input closed")
        return True

    def begin_stop(self, now: float, kind: str, why: str,
                   interrupt: Optional[TaskInterrupt] = None) -> bool:
        """Atomically mark the attempt stopping. False (and no change) when it is not active."""
        if not self.is_active:
            return False
        self.transition(STOPPING, now, why)
        self.stop_kind = kind
        self.stop_reason = why
        self.stop_interrupt = interrupt
        return True

    def finalize(self, result: Dict[str, Any], terminal_state: str, now: float) -> bool:
        """Store the one result and enter the terminal state. Returns False when already finalized."""
        if self.result is not None or self.is_terminal:
            return False
        if self.state == PREPARING:  # a module that finished before any acknowledgment or sample
            self.mark_capture_started(now)
        if self.state == CAPTURING:  # a window that ended before the client closed its input
            self.input_closed = True
            self.transition(FINALIZING, now, "result ready")
        self.transition(terminal_state, now, "finalized")
        self.result = result
        return True

    # ---- input ------------------------------------------------------------------------------

    def accept_sample(self, sequence: int, captured_ms: int, payload: Any, now: float) -> str:
        if not self.accepts_samples:
            return LATE
        if self.capture_command_at is None:
            return EARLY
        print_ = _fingerprint([captured_ms, _fingerprint(payload)])
        if sequence in self._sample_prints:
            return DUPLICATE if self._sample_prints[sequence] == print_ else CONFLICT
        if sequence <= self._last_sample_sequence:
            return CONFLICT  # an unseen older sequence
        self._sample_prints[sequence] = print_
        self._last_sample_sequence = sequence
        self.samples_received += 1
        self.last_sample_at = now
        if self.first_sample_at is None:
            self.first_sample_at = now
        if self.state == PREPARING:
            self.mark_capture_started(now)
        return ACCEPTED

    def accept_observation(self, obs: TaskObservation, now: float) -> str:
        if obs.attempt_id != self.attempt_id or obs.call_id != self.call_id:
            return OBSOLETE
        if not self.is_active:
            return LATE
        print_ = _fingerprint(obs.to_dict())
        if obs.event_id in self._obs_ids:
            return DUPLICATE if self._obs_ids[obs.event_id] == print_ else CONFLICT
        if obs.sequence <= self._last_obs_sequence:
            return CONFLICT
        self._obs_ids[obs.event_id] = print_
        self._last_obs_sequence = obs.sequence
        self.observations.append(obs)
        self.latest_observation = obs
        self.last_analyzer_at = now
        if obs.signal == "framing_unusable":
            if self.framing_bad_since is None:  # the clock starts at the FIRST bad observation
                self.framing_bad_since = now
                self.framing_bad_event_id = obs.event_id
        else:
            self.framing_bad_since = None  # a good observation clears the pending condition
            self.framing_bad_event_id = None
        if obs.signal == "task_unperformable":
            self.unperformable_event_id = obs.event_id
        return ACCEPTED
