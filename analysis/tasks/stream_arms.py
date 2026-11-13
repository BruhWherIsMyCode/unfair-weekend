"""The arms task on streamed JPEG samples: one free hand at a time (v6).

The person holds the phone in ONE hand (owner's decision), so she raises the other hand beside her face, then
swaps. One `arms` task, one 12 s capture window, two halves (limits.ARMS_PHASES, editable there):

    0-1 s settle, 1-5 s hold LEFT hand (her left; the phone is in her right hand),
    5-7 s swap (frames ignored), 7-8 s settle, 8-12 s hold RIGHT hand.

The task definition carries a cue: the session plays the `swap_hands` line 5 s into the capture.

Per frame in a hold phase: decode (analysis/tasks/jpeg.py) -> frame_reader(image, ts_ms, expected_side) ->
HandFrameReading. The production reader wraps HandFrameReader + hand_metric.hand_frame_reading (the same
arithmetic as tasks/hand.py). Frames in settle and swap phases are not read at all.

Final: hand_metric.hand_clip_reading per hand over its hold -> hand_sink per hand -> compared with that hand's
frozen baseline (one-sided: only ABOVE her usual counts as outside, as in tasks/hand.py). One Reading is
reported: the hand that is further outside, or when neither is, the hand closest to its usual_high.
Every number is a first guess; nothing here is validated.
"""
from __future__ import annotations

import asyncio
import math
from statistics import median
from typing import Any, AsyncIterator, Callable, Dict, List, Optional, Sequence, Tuple

from analysis import config
from analysis.hand_metric import HandFrameReading, hand_clip_reading, hand_frame_reading
from analysis.session import limits
from analysis.session.models import (BaselineInfo, Metric, Quality, Reading, TaskContext, TaskObservation, TaskResult)
from analysis.tasks import stream_base

METRIC_ID = limits.ARMS_METRIC_ID  # "hand_sink_v1"
UNIT = "eye_distance"  # how far the palm sank, in units of the eye-to-eye distance (hand_metric)
PRIMARY = config.HAND_PRIMARY  # "hand_sink"
OBSERVATION_PERIOD_MS = 1000
CUE_LINE = "swap_hands"

FrameReader = Callable[[Any, int, str], Optional[HandFrameReading]]
Decoder = Callable[[Dict[str, Any]], Any]
Phase = Tuple[str, int, int, Optional[str]]


class ProductionHandReader:
    """image -> HandFrameReading with the real face and hand extractors. Created lazily; close() when done."""

    def __init__(self, mirrored: bool = False) -> None:
        self.mirrored = mirrored
        self._ctx: Any = None
        self._reader: Any = None

    def __call__(self, image: Any, timestamp_ms: int, expected_side: str) -> HandFrameReading:
        if self._reader is None:
            from analysis.hand_points import HandFrameReader  # imports mediapipe

            for path in (config.MODEL_PATH, config.HAND_MODEL_PATH):
                if not path.exists():
                    raise FileNotFoundError("Model file missing. Run:  python -m analysis.get_models")
            self._ctx = HandFrameReader()
            self._reader = self._ctx.__enter__()
        face_obs, hands = self._reader.process(image, timestamp_ms, mirrored=self.mirrored)
        return hand_frame_reading(face_obs, hands, expected_side)

    def close(self) -> None:
        if self._ctx is not None:
            self._ctx.__exit__(None, None, None)
            self._ctx = None
            self._reader = None


def _default_decoder() -> Decoder:
    from analysis.tasks.jpeg import decode_jpeg

    return decode_jpeg


def phase_at(ms: int, phases: Sequence[Phase] = limits.ARMS_PHASES) -> Optional[Phase]:
    """The phase a sample at `ms` belongs to (start <= ms < end), or None past the window."""
    for phase in phases:
        if phase[1] <= ms < phase[2]:
            return phase
    return None


def hold_windows(phases: Sequence[Phase] = limits.ARMS_PHASES) -> Dict[str, Tuple[int, int]]:
    """side -> (hold_start_ms, hold_end_ms)."""
    return {side: (start, end) for _, start, end, side in phases if side is not None}


def cue_at_ms(phases: Sequence[Phase] = limits.ARMS_PHASES) -> int:
    """The swap cue plays when the swap phase starts."""
    for name, start, _, _ in phases:
        if name == "swap":
            return start
    return phases[0][1]


def provisional_sink(frames: Sequence[HandFrameReading], hold_start_ms: int) -> Optional[float]:
    """The current hand_sink of the active hand: recent palm height minus the height at the start of its hold.

    None until enough usable frames exist. A running estimate for observations only; never a final reading."""
    usable = [f for f in frames if f.usable and f.height is not None and math.isfinite(f.height)]
    start = [f.height for f in usable if f.timestamp_ms < hold_start_ms + config.HAND_WINDOW_S * 1000.0]
    if len(start) < 3 or len(usable) < 4:
        return None
    return float(median([f.height for f in usable[-5:]]) - median(start))


def _baseline_info(baseline: Dict[str, Any], hand_baselines: Dict[str, Dict[str, Any]], ready: bool) -> BaselineInfo:
    hands = [b for b in hand_baselines.values() if isinstance(b, dict)]
    sessions = baseline.get("sessions")
    if sessions is None:
        sessions = min((int(b.get("sessions") or 0) for b in hands), default=0)
    needed = baseline.get("needed")
    if needed is None:
        needed = max((int(b.get("needed") or 0) for b in hands), default=0) or limits.BASELINE_NEEDED_DEFAULT
    return BaselineInfo(str(baseline.get("snapshot_id") or "baseline_none"), int(sessions), int(needed), ready)


def final_result(context: TaskContext, frames_by_side: Dict[str, List[Optional[HandFrameReading]]],
                 phases: Sequence[Phase] = limits.ARMS_PHASES) -> Tuple[TaskResult, Dict[str, float]]:
    """The pure final step: hold frames per hand -> (one schema-valid TaskResult, per-hand hand_sink values).

    The values dict has an entry for a hand only when that hand's hold was usable; a half-usable attempt has no
    reading and its values are returned empty (they must not enter a baseline)."""
    baseline = context.baseline or {}
    hand_baselines = {side: baseline.get("hand_%s" % side) for side in ("left", "right")}
    ready = (all(isinstance(b, dict) and bool(b.get("ready")) for b in hand_baselines.values())
             and baseline.get("ready", True) is not False)
    binfo = _baseline_info(baseline, hand_baselines, ready)

    holds = hold_windows(phases)
    values: Dict[str, float] = {}
    missing: List[str] = []
    for side in ("left", "right"):
        start, end = holds[side]
        clip = hand_clip_reading(frames_by_side.get(side, []), float(start), float(end))
        sink = clip.hand_sink
        if clip.quality != "usable" or sink is None or not math.isfinite(sink):
            missing.append("%s_hand_not_seen" % side)
        else:
            values[side] = float(sink)
    if missing:
        return TaskResult("arms", context.attempt_id, "completed", Quality("unusable", missing), "not_observed",
                          binfo, None, None), {}

    if ready:
        excess = {side: values[side] - float(hand_baselines[side]["usual_high"]) for side in ("left", "right")}
        pick = max(("left", "right"), key=lambda s: excess[s])  # ties go to the left hand
        outside = excess[pick] > 0
        b = hand_baselines[pick]
        reading = Reading(METRIC_ID, UNIT, values[pick], float(b["usual_low"]), float(b["usual_high"]), outside,
                          pick if outside else None)
    else:  # learning: no comparison at all
        pick = max(("left", "right"), key=lambda s: values[s])
        reading = Reading(METRIC_ID, UNIT, values[pick], None, None, None, None)
    return TaskResult("arms", context.attempt_id, "completed", Quality("usable", []), "observed", binfo, reading,
                      None), values


class StreamArms:
    name = "arms"

    def __init__(self, frame_reader: Optional[FrameReader] = None, decode: Optional[Decoder] = None,
                 phases: Sequence[Phase] = limits.ARMS_PHASES, period_ms: int = OBSERVATION_PERIOD_MS,
                 use_executor: bool = True) -> None:
        self._frame_reader = frame_reader
        self._decode = decode
        self.phases = tuple(phases)
        self.period_ms = period_ms
        self.use_executor = use_executor  # False runs the frame work inline (tests, demo)
        self.window_ms = max(p[2] for p in self.phases)
        self._values: Dict[str, Dict[str, float]] = {}  # attempt_id -> {"left": sink, "right": sink}

    def definition(self) -> Dict[str, Any]:
        d = stream_base.build_definition("arms", METRIC_ID, UNIT)
        d["cues"] = [{"at_ms": cue_at_ms(self.phases), "line_id": CUE_LINE}]
        return d

    def attempt_values(self, attempt_id: str) -> Dict[str, float]:
        """Per-hand hand_sink of a finished, fully usable attempt (for the per-hand baselines), else {}."""
        return dict(self._values.get(attempt_id, {}))

    def _measure(self, reader: FrameReader, decode: Decoder, sample: Dict[str, Any], ts_ms: int, side: str
                 ) -> HandFrameReading:
        """Synchronous and CPU-heavy: runs in an executor so the event loop keeps handling events."""
        image = decode(sample)
        if image is None:
            return HandFrameReading(usable=False, reason="image could not be decoded", timestamp_ms=ts_ms)
        reading = reader(image, ts_ms, side)
        if reading is None:
            return HandFrameReading(usable=False, reason="no reading", timestamp_ms=ts_ms)
        reading.timestamp_ms = ts_ms  # hold windows are cut by this time
        return reading

    async def run(self, context: TaskContext, samples: AsyncIterator[Dict[str, Any]],
                  emit_observation: Callable[[Any], Any]) -> TaskResult:
        decode = self._decode or _default_decoder()
        owned_reader: Optional[ProductionHandReader] = None
        reader = self._frame_reader
        if reader is None:
            owned_reader = ProductionHandReader()
            reader = owned_reader
        loop = asyncio.get_running_loop()
        holds = hold_windows(self.phases)
        frames: Dict[str, List[Optional[HandFrameReading]]] = {"left": [], "right": []}
        sequence = 0
        status: Optional[str] = None
        next_emit_ms = 0
        last_ts = -1
        elapsed = 0

        def observe(ms: int, quality: Quality, metrics: List[Metric], signal: str, phase: str = "capturing") -> None:
            nonlocal sequence
            emit_observation(TaskObservation(
                "obs_%s_%d" % (context.attempt_id, sequence), context.call_id, context.attempt_id, "arms",
                sequence, max(0, min(int(ms), 15000)), phase, quality, metrics, signal))
            sequence += 1

        try:
            async for sample in samples:
                if context.cancel_event.is_set():
                    return TaskResult("arms", context.attempt_id, "cancelled", Quality("unusable", ["cancelled"]),
                                      "uncertain", stream_base.baseline_info(context.baseline), None, None)
                ms = int(sample["captured_ms"])
                if ms > self.window_ms:
                    break  # outside the window: not measured
                phase = phase_at(ms, self.phases)
                side = phase[3] if phase is not None else None
                reading: Optional[HandFrameReading] = None
                if side is not None:  # a hold phase: measure. Settle and swap frames are ignored.
                    ts_ms = ms if ms > last_ts else last_ts + 1  # the extractor needs strictly increasing times
                    last_ts = ts_ms
                    if self.use_executor:
                        reading = await loop.run_in_executor(None, self._measure, reader, decode, sample, ts_ms, side)
                    else:  # tests and the scripted demo: cheap fake reader, deterministic with a fake clock
                        reading = self._measure(reader, decode, sample, ts_ms, side)
                    frames[side].append(reading)
                elapsed = ms

                if reading is None:
                    new_status = "pending"  # settle or swap: nothing is judged, framing is never flagged
                else:
                    new_status = "usable" if reading.usable else "unusable"
                if new_status != status or ms >= next_emit_ms:
                    status = new_status
                    next_emit_ms = (ms // self.period_ms + 1) * self.period_ms
                    if new_status == "pending":
                        observe(ms, Quality("pending", []), [], "none")
                    elif new_status == "usable":
                        metrics: List[Metric] = []
                        sink = provisional_sink(frames[side], holds[side][0])
                        if sink is not None:
                            metrics = [Metric(METRIC_ID, UNIT, sink)]
                        observe(ms, Quality("usable", []), metrics, "none")
                    else:
                        why = stream_base.slug(reading.reason or "hand not found")
                        observe(ms, Quality("unusable", [why]), [], "framing_unusable")
                if ms >= self.window_ms:
                    break
            observe(elapsed, Quality("pending", []), [], "none", phase="finalizing")
            result, values = final_result(context, frames, self.phases)
            if values:
                self._values[context.attempt_id] = values
            return result
        finally:
            if owned_reader is not None:
                owned_reader.close()
