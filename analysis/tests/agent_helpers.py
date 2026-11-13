"""Shared helpers for the v6 tests: a whole-call rig with the real smile and arms modules on fake readers.

No mediapipe, no cv2, no network, no wall-clock waits: time moves only through FakeClock. Made-up numbers.
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional, Sequence

from analysis.face_metric import FrameReading
from analysis.hand_metric import HandFrameReading
from analysis.session import limits
from analysis.session.adapters import FakeAdapter
from analysis.session.agent_tools import ToolHandlers, load_script
from analysis.session.call_session import CallSession
from analysis.session.clock import FakeClock
from analysis.tasks.stream_arms import StreamArms, hold_windows
from analysis.tasks.stream_sentence import StreamSentence
from analysis.tasks.stream_smile import StreamSmile

SMILE_BASELINE = {"snapshot_id": "baseline_s", "ready": True, "sessions": 12, "needed": 5,
                  "usual_low": -0.02, "usual_high": 0.03}
_HAND = {"ready": True, "sessions": 12, "needed": 5, "usual_low": -0.10, "usual_high": 0.10}
ARMS_BASELINE = {"snapshot_id": "baseline_a", "ready": True, "sessions": 12, "needed": 5,
                 "hand_left": dict(_HAND), "hand_right": dict(_HAND)}
ANSWERS = {"q_medicine": "Yes, after breakfast.", "q_feeling": "I feel fine, thank you.",
           "q_unusual": "Nothing unusual."}
FACT_KEYS = {"task", "outcome", "quality", "reasons", "interruption", "attempts_left", "needs_another_reading",
             "skip", "resources"}


def identity_decode(sample: Dict[str, Any]) -> Any:
    return sample


class FakeSmile:
    """Relaxed before 1.5 s, then smiling with mouth-corner height `corner`."""

    def __init__(self) -> None:
        self.corner = 0.0

    def __call__(self, image: Any, ts_ms: int) -> FrameReading:
        smiling = ts_ms >= 1500
        h = self.corner if smiling else 0.0
        return FrameReading(usable=True, unit=100.0, below_left=1.0 + h / 2, below_right=1.0 - h / 2,
                            corner_height=h, smile_strength=0.9 if smiling else 0.1, timestamp_ms=ts_ms)


class FakeHands:
    """A free hand steady at 0.5 eye distances below the eye line; in the second half of its hold it sinks by
    sink[side]. `usable(side, ts)` can hide the hand."""

    def __init__(self, sink: Optional[Dict[str, float]] = None, usable: Any = None) -> None:
        self.sink = {"left": 0.0, "right": 0.0}
        self.sink.update(sink or {})
        self.usable = usable or (lambda side, ts: True)
        self.calls: List[tuple] = []

    def __call__(self, image: Any, ts_ms: int, side: str) -> HandFrameReading:
        self.calls.append((ts_ms, side))
        if not self.usable(side, ts_ms):
            return HandFrameReading(usable=False, reason="no hand on your %s side" % side.upper(), face_ok=True,
                                    unit=100.0, timestamp_ms=ts_ms)
        start, end = hold_windows()[side]
        height = 0.5 + (self.sink[side] if ts_ms >= (start + end) / 2 else 0.0)
        return HandFrameReading(usable=True, face_ok=True, unit=100.0, height=height, palm_width_ratio=1.0,
                                timestamp_ms=ts_ms)


def camera_event(event_id: str, state: str, sequence: int) -> Dict[str, Any]:
    return {"event_id": event_id, "type": "resource.changed", "command_id": None, "resource": "camera",
            "state": state, "sequence": sequence, "reason": "track_ended" if state == "unavailable" else None}


class CallRig:
    """A session with the real smile and arms modules on fake readers, a fake adapter and a fake clock."""

    def __init__(self, answers: Optional[Dict[str, Any]] = None, corners: Sequence[float] = (0.01,),
                 sinks: Sequence[Dict[str, float]] = ({},), required_tasks: Sequence[str] = limits.TASKS,
                 label: str = "live", smile_baseline: Optional[Dict[str, Any]] = SMILE_BASELINE,
                 arms_baseline: Optional[Dict[str, Any]] = ARMS_BASELINE) -> None:
        self.clock = FakeClock()
        self.adapter = FakeAdapter(person_name="Ona", answers=ANSWERS if answers is None else answers)
        self.smile = FakeSmile()
        self.hands = FakeHands()
        self.corners = list(corners)  # mouth-corner height per smile attempt (the last one repeats)
        self.sinks = list(sinks)  # hand sink per arms attempt (the last one repeats)
        modules = {"smile": StreamSmile(self.smile, identity_decode, use_executor=False),
                   "arms": StreamArms(self.hands, identity_decode, use_executor=False),
                   "sentence": StreamSentence()}
        baselines: Dict[str, Any] = {}
        if smile_baseline is not None:
            baselines["smile"] = smile_baseline
        if arms_baseline is not None:
            baselines["arms"] = arms_baseline
        self.session = CallSession("call_t", "p_t", "Ona", self.adapter, modules, baselines, self.clock, label=label,
                                   required_tasks=required_tasks,
                                   initial_resources={"camera": "available", "microphone": "available"})
        self.adapter.session = self.session
        self.handlers = ToolHandlers(self.session, self.adapter, load_script())
        self.attempts_by_task: Dict[str, int] = {}
        self.restore_camera = True  # False: the camera never comes back after it was lost
        self._seen: set = set()
        self._lost_at: Optional[float] = None
        self._restored = False

    def kinds(self, kind: str) -> List[Dict[str, Any]]:
        return [e for e in self.session.log if e["kind"] == kind]

    async def stream(self, attempt_id: str, samples: int, finish: bool = True) -> None:
        for i in range(samples):
            self.adapter.push_sample(attempt_id, {"sequence": i, "captured_ms": i * 200, "format": "jpeg",
                                                  "data_base64": "AAAA"})
            await self.clock.advance(0.2)
        if finish:
            self.adapter.finish_capture(attempt_id)
            await self.clock.settle()

    async def drive(self, call: Any, lose_camera_in_first_arms: bool = False, limit_s: float = 400.0) -> Any:
        """Run `call` (a coroutine) while a fake client streams samples for every attempt the session starts.
        The first arms attempt can lose the camera at 4 s; the camera is back 2 s later."""
        main = asyncio.ensure_future(call)
        seen = self._seen
        while not main.done():
            await self.clock.settle()
            if main.done():
                break
            assert self.clock.now() < limit_s, "the call did not finish in %.0f s of fake time" % limit_s
            attempt_id = self.session.active_attempt_id
            if attempt_id is not None and attempt_id not in seen:
                seen.add(attempt_id)
                task = self.session.attempts[attempt_id].task
                n = self.attempts_by_task.get(task, 0)
                self.attempts_by_task[task] = n + 1
                if task == "smile":
                    self.smile.corner = self.corners[min(n, len(self.corners) - 1)]
                if task == "arms":
                    self.hands.sink.update({"left": 0.0, "right": 0.0})
                    self.hands.sink.update(self.sinks[min(n, len(self.sinks) - 1)])
                if task == "arms" and lose_camera_in_first_arms and self._lost_at is None:
                    await self.stream(attempt_id, 20, finish=False)
                    self.session.handle_resource_event(camera_event("evt_lost", "unavailable", 1))
                    self._lost_at = self.clock.now()
                else:
                    await self.stream(attempt_id, {"smile": 26, "arms": 61}.get(task, 0))
                continue
            if (self._lost_at is not None and not self._restored and self.restore_camera
                    and self.clock.now() - self._lost_at >= 2.0):
                self.session.handle_resource_event(camera_event("evt_back", "available", 2))
                self._restored = True
                continue
            await self.clock.advance(0.1)
        return await main

    async def run_tool(self, name: str, args: Dict[str, Any], **drive_kw: Any) -> Dict[str, Any]:
        return await self.drive(self.handlers.execute(name, args), **drive_kw)

    async def greet(self) -> None:
        assert "error" not in await self.handlers.execute("say", {"line_id": "greet"})
