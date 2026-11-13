"""The smile task on streamed JPEG samples (5 s window, about 5 frames per second).

Per frame: decode (analysis/tasks/jpeg.py) -> frame_reader -> a FrameReading. The production reader wraps
FacePointsExtractor + face_metric.frame_reading, the same per-frame arithmetic as tasks/smile.py. The final
reading is face_metric.clip_reading over the frames (the same function tasks/smile.py uses for a clip), its
primary value `corner_height`, compared with the frozen baseline through baseline.compare.

While running it emits a TaskObservation about once per second of sample time and at once when the input
quality changes (pending / usable / unusable). Every number is a first guess; nothing here is validated.
"""
from __future__ import annotations

import asyncio
import math
from statistics import median
from typing import Any, AsyncIterator, Callable, Dict, List, Optional, Tuple

from analysis import baseline as baseline_mod
from analysis import config
from analysis.face_metric import FrameReading, clip_reading, frame_reading
from analysis.session import limits
from analysis.session.models import (Metric, Quality, Reading, TaskContext, TaskObservation, TaskResult)
from analysis.tasks import stream_base

METRIC_ID = "corner_height_v1"  # versioned name of the primary reading (config PRIMARY corner_height)
UNIT = "eye_distance"  # distance below the eye line, in units of the eye-to-eye distance
PRIMARY = "corner_height"
WINDOW_MS = 6000  # v11: 0-1.5 s relax, the session plays `smile_now` at 1.5 s, 1.5-6 s smile (limits.SMILE_PHASES)
OBSERVATION_PERIOD_MS = 1000

FrameReader = Callable[[Any, int], Optional[FrameReading]]
Decoder = Callable[[Dict[str, Any]], Any]


class ProductionFrameReader:
    """image -> FrameReading with the real face extractor. Created lazily; close() when the attempt ends."""

    def __init__(self, model_path: Any = None, mirrored: bool = False) -> None:
        self.model_path = model_path or config.MODEL_PATH
        self.mirrored = mirrored
        self.on_frame: Optional[Callable[..., Any]] = None  # debug mode only: (image, ts, face_obs, reading, mirrored)
        self._ctx: Any = None
        self._extractor: Any = None

    def __call__(self, image: Any, timestamp_ms: int) -> Optional[FrameReading]:
        if self._extractor is None:
            from analysis.face_points import FacePointsExtractor  # imports mediapipe

            if not self.model_path.exists():
                raise FileNotFoundError("Model file missing. Run:  python -m analysis.get_models")
            self._ctx = FacePointsExtractor(self.model_path)
            self._extractor = self._ctx.__enter__()
        obs = self._extractor.process(image, timestamp_ms, mirrored=self.mirrored)
        reading = frame_reading(obs)
        if self.on_frame is not None:  # debug mode: only reports what was seen
            try:
                self.on_frame(image, timestamp_ms, obs, reading, self.mirrored)
            except Exception:  # never into the measuring
                pass
        return reading

    def close(self) -> None:
        if self._ctx is not None:
            self._ctx.__exit__(None, None, None)
            self._ctx = None
            self._extractor = None


def _default_decoder() -> Decoder:
    from analysis.tasks.jpeg import decode_jpeg

    return decode_jpeg


def phase_reading(frame_readings: List[Optional[FrameReading]],
                  relax_end_ms: int = limits.SMILE_CUE_MS) -> Tuple[str, Optional[float], Optional[str]]:
    """Frame readings -> (status, corner_height, reason). A pure function on made-up or real frame readings.

    status is "usable" (corner_height is the value), "not_measured" (reason is already_smiling or no_smile_seen)
    or "unusable" (reason is face_not_in_view).

    With at least SMILE_MIN_PHASE_FRAMES usable frames in the relax phase (before `relax_end_ms`) and in the smile
    phase, the relaxed reference is the median smile strength of the relax frames and the peak is the median of the
    strongest 20 % (at least 3) of the smile-phase frames. Otherwise the old rule applies (weakest 20 % against
    strongest 20 % of all usable frames, face_metric.clip_reading). The mouth-corner height is the median over the
    peak frames in both cases. Not enough rise: relaxed at or above SMILE_ALREADY_SMILING = already_smiling, else
    no_smile_seen."""
    frames = [f for f in frame_readings if f is not None]
    usable = [f for f in frames if f.usable and f.smile_strength is not None and f.corner_height is not None]
    if len(usable) < config.MIN_USABLE_FRAMES:
        return "unusable", None, "face_not_in_view"
    relax = [f for f in usable if f.timestamp_ms < relax_end_ms]
    smile = [f for f in usable if f.timestamp_ms >= relax_end_ms]
    if len(relax) >= limits.SMILE_MIN_PHASE_FRAMES and len(smile) >= limits.SMILE_MIN_PHASE_FRAMES:
        relaxed = float(median([f.smile_strength for f in relax]))
        n_peak = min(len(smile), max(config.MIN_PEAK_FRAMES, int(round(len(smile) * config.PEAK_FRACTION))))
        peak_frames = sorted(smile, key=lambda f: f.smile_strength)[len(smile) - n_peak:]
        peak = float(median([f.smile_strength for f in peak_frames]))
        height = float(median([f.corner_height for f in peak_frames]))
    else:
        clip = clip_reading(frames)
        if clip.quality == "usable":
            return "usable", clip.readings().get(PRIMARY), None
        if clip.peak_strength is None or clip.relaxed_strength is None:
            return "unusable", None, "face_not_in_view"
        relaxed, peak, height = clip.relaxed_strength, clip.peak_strength, None
    if peak - relaxed < config.MIN_SMILE_RISE:
        return "not_measured", None, ("already_smiling" if relaxed >= limits.SMILE_ALREADY_SMILING
                                       else "no_smile_seen")
    return "usable", height, None


def final_result(context: TaskContext, frame_readings: List[Optional[FrameReading]]) -> TaskResult:
    """The pure final step: frame readings -> one schema-valid TaskResult against the frozen baseline."""
    binfo = stream_base.baseline_info(context.baseline)
    status, value, why = phase_reading(frame_readings)

    if status == "not_measured":  # frames were fine but no clear rise from the relaxed face happened
        return TaskResult("smile", context.attempt_id, "could_not_perform", Quality("usable", [why]),
                          "not_observed", binfo, None, None)
    if status == "unusable":
        reasons = [why]
        counts: Dict[str, int] = {}
        for f in frame_readings:
            if f is not None and not f.usable and f.reason:
                counts[f.reason] = counts.get(f.reason, 0) + 1
        if counts:  # the most common frame problem, as a detail for the log
            reasons.append(stream_base.slug(max(counts, key=lambda k: counts[k])))
        return TaskResult("smile", context.attempt_id, "completed", Quality("unusable", list(dict.fromkeys(reasons))),
                          "uncertain", binfo, None, None)

    if value is None or not math.isfinite(value):
        return TaskResult("smile", context.attempt_id, "completed", Quality("unusable", ["reading_unavailable"]),
                          "uncertain", binfo, None, None)

    if binfo.ready:
        cmp = baseline_mod.compare(value, context.baseline)
        reading = Reading(METRIC_ID, UNIT, float(value), cmp["usual_low"], cmp["usual_high"], bool(cmp["outside"]),
                          cmp["side"] if cmp["outside"] else None)
    else:  # learning: no comparison at all
        reading = Reading(METRIC_ID, UNIT, float(value), None, None, None, None)
    return TaskResult("smile", context.attempt_id, "completed", Quality("usable", []), "observed", binfo, reading, None)


class StreamSmile:
    name = "smile"

    def __init__(self, frame_reader: Optional[FrameReader] = None, decode: Optional[Decoder] = None,
                 window_ms: int = WINDOW_MS, period_ms: int = OBSERVATION_PERIOD_MS,
                 use_executor: bool = True, mirrored: bool = False, debug: Any = None) -> None:
        self._frame_reader = frame_reader
        self.debug = debug  # a debug_view.DebugView (UW_DEBUG=on) or None: reports only, never changes a reading
        self.mirrored = mirrored  # for the production reader a run creates itself (a mirrored front camera)
        self._decode = decode
        self.use_executor = use_executor  # False runs the frame work inline (tests, demo)
        self.window_ms = window_ms
        self.period_ms = period_ms

    def definition(self) -> Dict[str, Any]:
        d = stream_base.build_definition("smile", METRIC_ID, UNIT)
        d["cues"] = [{"at_ms": limits.SMILE_CUE_MS, "line_id": limits.SMILE_CUE_LINE}]  # same mechanism as arms
        return d

    def _measure(self, reader: FrameReader, decode: Decoder, sample: Dict[str, Any], ts_ms: int
                 ) -> Optional[FrameReading]:
        """Synchronous and CPU-heavy: runs in an executor so the event loop keeps handling events."""
        image = decode(sample)
        if image is None:
            return FrameReading(usable=False, reason="image could not be decoded", timestamp_ms=ts_ms)
        return reader(image, ts_ms)

    async def run(self, context: TaskContext, samples: AsyncIterator[Dict[str, Any]],
                  emit_observation: Callable[[Any], Any]) -> TaskResult:
        decode = self._decode or _default_decoder()
        owned_reader: Optional[ProductionFrameReader] = None
        reader = self._frame_reader
        if reader is None:
            owned_reader = ProductionFrameReader(mirrored=self.mirrored)
            reader = owned_reader
        if self.debug is not None and hasattr(reader, "on_frame"):
            debug, attempt_id = self.debug, context.attempt_id
            reader.on_frame = lambda image, ts, obs, reading, mirrored: debug.smile_frame(
                attempt_id, ts, image, obs, reading, mirrored)
        loop = asyncio.get_running_loop()
        readings: List[Optional[FrameReading]] = []
        sequence = 0
        status = "pending"
        next_emit_ms = self.period_ms
        last_ts = -1
        elapsed = 0

        def observe(ms: int, quality: Quality, metrics: List[Metric], signal: str, phase: str = "capturing") -> None:
            nonlocal sequence
            emit_observation(TaskObservation(
                "obs_%s_%d" % (context.attempt_id, sequence), context.call_id, context.attempt_id, "smile",
                sequence, max(0, min(int(ms), 15000)), phase, quality, metrics, signal))
            sequence += 1

        try:
            async for sample in samples:
                if context.cancel_event.is_set():
                    return TaskResult("smile", context.attempt_id, "cancelled", Quality("unusable", ["cancelled"]),
                                      "uncertain", stream_base.baseline_info(context.baseline), None, None)
                ms = int(sample["captured_ms"])
                if ms > self.window_ms:
                    break  # outside the window: not measured
                ts_ms = ms if ms > last_ts else last_ts + 1  # the extractor needs strictly increasing times
                last_ts = ts_ms
                if self.use_executor:
                    reading = await loop.run_in_executor(None, self._measure, reader, decode, sample, ts_ms)
                else:  # tests and the scripted demo: cheap fake reader, deterministic with a fake clock
                    reading = self._measure(reader, decode, sample, ts_ms)
                readings.append(reading)
                elapsed = ms

                usable = reading is not None and reading.usable
                new_status = "usable" if usable else "unusable"
                if new_status != status or ms >= next_emit_ms:
                    status = new_status
                    next_emit_ms = (ms // self.period_ms + 1) * self.period_ms
                    if usable:
                        metrics = []
                        if reading.corner_height is not None and math.isfinite(reading.corner_height):
                            metrics = [Metric(METRIC_ID, UNIT, float(reading.corner_height))]
                        observe(ms, Quality("usable", []), metrics, "none")
                    else:
                        why = stream_base.slug(reading.reason if reading is not None and reading.reason
                                               else "face not found")
                        observe(ms, Quality("unusable", [why]), [], "framing_unusable")
                if ms >= self.window_ms:
                    break
            observe(elapsed, Quality("pending", []), [], "none", phase="finalizing")
            return final_result(context, readings)
        finally:
            if owned_reader is not None:
                owned_reader.close()
