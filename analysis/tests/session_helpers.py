"""Shared helpers for the call-session tests: a scripted task module, a session factory and a fake client."""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from analysis.session.adapters import FakeAdapter
from analysis.session.call_session import CallSession
from analysis.session.clock import FakeClock
from analysis.session.models import (BaselineInfo, Metric, Quality, Reading, TaskContext, TaskObservation, TaskResult)
from analysis.tasks import stream_base

BASELINE = {"snapshot_id": "baseline_t", "ready": True, "sessions": 12, "needed": 5,
            "usual_low": -0.02, "usual_high": 0.03}


class ScriptedModule:
    """A fake task module: it waits for the test, which tells it to emit observations or to finish."""

    name = "smile"

    def __init__(self, task: str = "smile") -> None:
        self.task = task
        self.context: Optional[TaskContext] = None
        self._emit: Any = None
        self._done: Optional["asyncio.Future"] = None
        self._seq = 0
        self.started = asyncio.Event()
        self.cancelled = False

    def definition(self) -> Dict[str, Any]:
        return stream_base.build_definition(self.task, "fake_metric_v1", "ratio")

    async def run(self, context: TaskContext, samples: Any, emit_observation: Any) -> TaskResult:
        self.context, self._emit = context, emit_observation
        self._seq = 0
        self._done = asyncio.get_running_loop().create_future()
        self.started.set()
        try:
            return await self._done
        except asyncio.CancelledError:
            self.cancelled = True
            raise

    def emit(self, status: str = "usable", signal: str = "none", elapsed_ms: int = 1000,
             metrics: Optional[List[Metric]] = None, reasons: Optional[List[str]] = None, attempt_id: Optional[str] = None
             ) -> str:
        reasons = reasons if reasons is not None else (["face_not_visible"] if status == "unusable" else [])
        obs = TaskObservation("obs_%s_%d" % (attempt_id or self.context.attempt_id, self._seq),
                              self.context.call_id, attempt_id or self.context.attempt_id, self.task, self._seq,
                              elapsed_ms, "capturing", Quality(status, reasons), metrics or [], signal)
        self._seq += 1
        return self._emit(obs)

    def finish(self, result: TaskResult) -> None:
        self._done.set_result(result)

    def usable_result(self, value: float, baseline: Optional[Dict[str, Any]] = None) -> TaskResult:
        b = dict(BASELINE if baseline is None else baseline)
        binfo = stream_base.baseline_info(b)
        if binfo.ready:
            outside = value > b["usual_high"] or value < b["usual_low"]
            side = ("left" if value > b["usual_high"] else "right") if outside else None
            reading = Reading("fake_metric_v1", "ratio", value, b["usual_low"], b["usual_high"], outside, side)
        else:
            reading = Reading("fake_metric_v1", "ratio", value, None, None, None, None)
        return TaskResult(self.task, self.context.attempt_id, "completed", Quality("usable", []), "observed",
                          binfo, reading)


def resource_event(event_id: str, resource: str, state: str, sequence: int, reason: Optional[str] = None
                   ) -> Dict[str, Any]:
    if state == "unavailable" and reason is None:
        reason = "track_ended"
    return {"event_id": event_id, "type": "resource.changed", "command_id": None, "resource": resource,
            "state": state, "sequence": sequence, "reason": reason}


class Rig:
    """A session with fake clock, fake adapter and scripted modules, plus a fake client."""

    def __init__(self, tasks=("smile",), baselines: Optional[Dict[str, Any]] = None, label: str = "live",
                 resources: Optional[Dict[str, str]] = None, auto_ack_cancel: bool = True,
                 required_tasks=("smile",)) -> None:
        self.clock = FakeClock()
        self.adapter = FakeAdapter(person_name="Ona", auto_ack_cancel=auto_ack_cancel)
        self.modules = {t: ScriptedModule(t) for t in tasks}
        self.session = CallSession(
            "call_t", "p_t", "Ona", self.adapter, self.modules,
            baselines if baselines is not None else {t: BASELINE for t in tasks}, self.clock, label=label,
            required_tasks=required_tasks,
            initial_resources=resources or {"camera": "available", "microphone": "available"})
        self.adapter.session = self.session
        self._seq: Dict[str, int] = {}
        self.task: Optional["asyncio.Future"] = None

    @property
    def module(self) -> ScriptedModule:
        return self.modules["smile"]

    @property
    def attempt_id(self) -> str:
        return list(self.session.attempts)[-1]

    async def start(self, task: str = "smile") -> str:
        self.task = asyncio.ensure_future(self.session.run_task(task))
        await self.clock.settle()
        return self.attempt_id

    async def tick(self, seconds: float, sample_every: Optional[float] = 0.5, attempt_id: Optional[str] = None) -> None:
        """Advance fake time; the fake client sends a sample every `sample_every` seconds (None = silence)."""
        attempt_id = attempt_id or self.attempt_id
        remaining = seconds
        step = sample_every if sample_every else seconds
        while remaining > 1e-9:
            dt = min(step, remaining)
            if sample_every:
                n = self._seq.get(attempt_id, 0)
                self.adapter.push_sample(attempt_id, {"sequence": n, "captured_ms": n * 100, "format": "jpeg",
                                                      "data_base64": "AAAA"})
                self._seq[attempt_id] = n + 1
            await self.clock.advance(dt)
            remaining -= dt

    def kinds(self, kind: str) -> List[Dict[str, Any]]:
        return [e for e in self.session.log if e["kind"] == kind]
