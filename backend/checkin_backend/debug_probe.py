"""Camera debug for the parent app: run Agent 3's real smile or arms module on frames from the phone, outside a call.

Pure debug, no assistant and no call. The phone opens a run, posts its camera frames on the task's own timeline and
gets back, for the latest analysed frame, what the module saw (face, hands, which side of her face each hand is on),
the module's live observations (quality and reasons) and finally its result. A FrameProbe stands in for
analysis.debug_view.DebugView: the modules call it per frame; it only reports and never changes a reading.

Nothing is stored: no frames, no call record, no baseline update. Her usual is only read, to show whether a reading
would be inside it. Runs live in memory and are dropped after RUN_TTL_S.
"""

import asyncio
import dataclasses
import math
import os
import threading
from time import monotonic
from typing import Any
from uuid import uuid4

from .runner import ensure_analysis_path

RUN_TTL_S = 10 * 60
MAX_RUNS = 4
FINISH_TIMEOUT_S = 30.0


class DebugUnavailable(RuntimeError):
    pass


def plain(value: Any) -> Any:
    """JSON-safe: numpy numbers to float, tuples to lists, dataclasses to dicts; non-finite numbers to None."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return plain(dataclasses.asdict(value))
    if isinstance(value, dict):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value
    if isinstance(value, int):
        return value
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    return number if math.isfinite(number) else None


class FrameProbe:
    """Duck-typed debug hook for StreamArms/StreamSmile. Called from the analysis executor thread."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._latest: dict[str, Any] | None = None
        self.frames = 0
        # Arms: per phase, how many frames had the hand on her left, her right, no hand, or no face.
        self._tally: dict[str, dict[str, int]] = {}

    def latest(self) -> dict[str, Any] | None:
        with self._lock:
            return self._latest

    def tally(self) -> dict[str, dict[str, int]]:
        with self._lock:
            return {phase: dict(counts) for phase, counts in self._tally.items()}

    def _keep(self, frame: dict[str, Any]) -> None:
        with self._lock:
            self.frames += 1
            self._latest = frame

    @staticmethod
    def _size(image: Any) -> tuple[int | None, int | None]:
        shape = getattr(image, "shape", None)
        return (int(shape[1]), int(shape[0])) if shape is not None and len(shape) >= 2 else (None, None)

    def arms_frame(self, attempt_id: str, ms: int, expected_side: str, image: Any, face_obs: Any,
                   hands: Any, mirrored: bool) -> None:
        from analysis.debug_view import CATEGORIES, arms_phase_of, frame_category, hand_side_facts

        facts = hand_side_facts(face_obs, hands, expected_side, mirrored)
        width, height = self._size(image)
        with self._lock:
            counts = self._tally.setdefault(arms_phase_of(ms), {c: 0 for c in CATEGORIES})
            counts[frame_category(facts)] += 1
        eyes = facts["eye_points"]
        roll = (math.degrees(math.atan2(eyes[1][1] - eyes[0][1], eyes[1][0] - eyes[0][0]))
                if len(eyes) == 2 else None)
        self._keep(plain({
            "task": "arms", "ms": ms, "expected": expected_side, "mirrored": bool(mirrored),
            "width": width, "height": height,
            "face": facts["face"], "face_small": facts["face_small"], "midline_x": facts["midline_x"],
            "eye_distance_px": facts["unit"], "roll_deg": roll, "eye_points": eyes,
            "hands": [{k: h.get(k) for k in ("side", "eye_widths", "if_mirrored_flipped", "centre", "box")}
                      for h in facts["hands"]],
        }))

    def smile_frame(self, attempt_id: str, ms: int, image: Any, obs: Any, reading: Any, mirrored: bool) -> None:
        width, height = self._size(image)
        found = obs is not None and getattr(obs, "face_found", False)
        points = dict(obs.points) if found else {}
        fields = ("usable", "reason", "smile_strength", "corner_height", "yaw_deg", "pitch_deg", "roll_deg")
        self._keep(plain({
            "task": "smile", "ms": ms, "mirrored": bool(mirrored), "width": width, "height": height,
            "face": bool(found),
            "eye_points": [points[k] for k in ("her_right_eye", "her_left_eye") if k in points],
            "mouth_points": [points[k] for k in ("her_right_mouth", "her_left_mouth") if k in points],
            "reading": {k: getattr(reading, k, None) for k in fields} if reading is not None else None,
        }))


class DebugRun:
    def __init__(self, task: str, module: Any, baseline: dict[str, Any], capture: dict[str, Any],
                 cues: list[dict[str, Any]], probe: FrameProbe) -> None:
        self.run_id = f"dbg_{uuid4().hex[:12]}"
        self.task = task
        self.capture = capture
        self.cues = cues
        self.probe = probe
        self.created = monotonic()
        self.observations: list[dict[str, Any]] = []
        self.result: dict[str, Any] | None = None
        self.error: str | None = None
        self._queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(maxsize=200)
        self._last_sequence = -1
        from analysis.session.models import TaskContext

        self._context = TaskContext(f"call_{self.run_id}", self.run_id, task, baseline)
        self._job = asyncio.create_task(self._run(module))

    async def _run(self, module: Any) -> None:
        async def samples():
            while (item := await self._queue.get()) is not None:
                yield item

        def emit(observation: Any) -> None:
            data = observation.to_dict() if hasattr(observation, "to_dict") else plain(observation)
            self.observations.append(plain(data))

        try:
            result = await module.run(self._context, samples(), emit)
            self.result = plain(result.to_dict() if hasattr(result, "to_dict") else result)
        except Exception as exc:  # a debug run reports its failure instead of raising into the request
            self.error = f"{type(exc).__name__}: {exc}"[:300]

    def add(self, sequence: int, captured_ms: int, data: bytes) -> None:
        if self._job.done() or sequence <= self._last_sequence:
            return
        self._last_sequence = sequence
        try:
            self._queue.put_nowait({"sequence": sequence, "captured_ms": captured_ms, "format": "jpeg", "data": data})
        except asyncio.QueueFull:
            pass  # the analysis is behind; a debug run may skip frames

    async def finish(self) -> None:
        if not self._job.done():
            await self._queue.put(None)
        try:
            await asyncio.wait_for(asyncio.shield(self._job), FINISH_TIMEOUT_S)
        except TimeoutError:
            self.error = self.error or "The analysis did not finish in time."

    def cancel(self) -> None:
        self._context.cancel_event.set()
        self._job.cancel()

    def snapshot(self, since: int = 0) -> dict[str, Any]:
        return {"run_id": self.run_id, "task": self.task, "frames_analysed": self.probe.frames,
                "tally": self.probe.tally(),
                "frame": self.probe.latest(), "observations": self.observations[since:],
                "observation_count": len(self.observations), "result": self.result, "error": self.error,
                "done": self._job.done()}


class DebugDesk:
    def __init__(self, person_id: str) -> None:
        self.person_id = person_id
        self.runs: dict[str, DebugRun] = {}

    def start(self, task: str, mirrored: bool | None) -> DebugRun:
        if task not in ("smile", "arms"):
            raise ValueError("task must be smile or arms")
        self._expire()
        ensure_analysis_path()
        probe = FrameProbe()
        module, baseline = self.build(task, mirrored, probe)
        definition = module.definition()
        run = DebugRun(task, module, baseline, definition["capture"], definition.get("cues") or [], probe)
        self.runs[run.run_id] = run
        return run

    def build(self, task: str, mirrored: bool | None, probe: FrameProbe) -> tuple[Any, dict[str, Any]]:
        """Agent 3's production module for `task` with `probe` as its debug hook, and her usual for that task."""
        ensure_analysis_path()
        try:
            from analysis.backend_entry import Settings, make_baseline_source
            from analysis.tasks.stream_arms import StreamArms
            from analysis.tasks.stream_smile import StreamSmile
        except ImportError as exc:
            raise DebugUnavailable(f"Agent 3's analysis is not installed here ({exc.name}).") from None
        settings = Settings(os.environ, self.person_id)
        mirror = settings.mirrored if mirrored is None else mirrored
        module = (StreamSmile if task == "smile" else StreamArms)(mirrored=mirror, debug=probe)
        source = make_baseline_source(settings.person_key, settings.data_dir, settings.baseline_needed)
        return module, source.load_baselines().get(task, {})

    def get(self, run_id: str) -> DebugRun | None:
        self._expire()
        return self.runs.get(run_id)

    def _expire(self) -> None:
        now = monotonic()
        stale = [r for r in self.runs.values() if now - r.created > RUN_TTL_S]
        overflow = sorted(self.runs.values(), key=lambda r: r.created)[:max(0, len(self.runs) - MAX_RUNS + 1)]
        for run in {r.run_id: r for r in stale + overflow}.values():
            run.cancel()
            self.runs.pop(run.run_id, None)

    def close(self) -> None:
        for run in self.runs.values():
            run.cancel()
        self.runs.clear()
