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
from typing import Any, AsyncIterator, Callable, Dict, List, Optional

from analysis import baseline as baseline_mod
from analysis import config
from analysis.face_metric import FrameReading, clip_reading, frame_reading
from analysis.session.models import (Metric, Quality, Reading, TaskContext, TaskObservation, TaskResult)
from analysis.tasks import stream_base

METRIC_ID = "corner_height_v1"  # versioned name of the primary reading (config PRIMARY corner_height)
UNIT = "eye_distance"  # distance below the eye line, in units of the eye-to-eye distance
PRIMARY = "corner_height"
WINDOW_MS = 5000
OBSERVATION_PERIOD_MS = 1000

FrameReader = Callable[[Any, int], Optional[FrameReading]]
Decoder = Callable[[Dict[str, Any]], Any]


class ProductionFrameReader:
    """image -> FrameReading with the real face extractor. Created lazily; close() when the attempt ends."""

    def __init__(self, model_path: Any = None, mirrored: bool = False) -> None:
        self.model_path = model_path or config.MODEL_PATH
        self.mirrored = mirrored
        self._ctx: Any = None
        self._extractor: Any = None

    def __call__(self, image: Any, timestamp_ms: int) -> Optional[FrameReading]:
        if self._extractor is None:
            from analysis.face_points import FacePointsExtractor  # imports mediapipe

            if not self.model_path.exists():
                raise FileNotFoundError("Model file missing. Run:  python -m analysis.get_models")
            self._ctx = FacePointsExtractor(self.model_path)
            self._extractor = self._ctx.__enter__()
        return frame_reading(self._extractor.process(image, timestamp_ms, mirrored=self.mirrored))

    def close(self) -> None:
        if self._ctx is not None:
            self._ctx.__exit__(None, None, None)
            self._ctx = None
            self._extractor = None


def _default_decoder() -> Decoder:
    from analysis.tasks.jpeg import decode_jpeg

    return decode_jpeg


def final_result(context: TaskContext, frame_readings: List[Optional[FrameReading]]) -> TaskResult:
    """The pure final step: frame readings -> one schema-valid TaskResult against the frozen baseline."""
    binfo = stream_base.baseline_info(context.baseline)
    clip = clip_reading(frame_readings)

    if clip.quality != "usable":
        if clip.peak_strength is not None:  # frames were fine but no smile happened
            return TaskResult("smile", context.attempt_id, "could_not_perform", Quality("usable", []),
                              "not_observed", binfo, None, None)
        reasons = ["too_few_usable_frames"]
        for text in clip.reasons[1:]:
            reasons.append(stream_base.slug(text.split(":", 1)[-1]))
        return TaskResult("smile", context.attempt_id, "completed", Quality("unusable", list(dict.fromkeys(reasons))),
                          "uncertain", binfo, None, None)

    value = clip.readings().get(PRIMARY)
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
                 use_executor: bool = True, mirrored: bool = False) -> None:
        self._frame_reader = frame_reader
        self.mirrored = mirrored  # for the production reader a run creates itself (a mirrored front camera)
        self._decode = decode
        self.use_executor = use_executor  # False runs the frame work inline (tests, demo)
        self.window_ms = window_ms
        self.period_ms = period_ms

    def definition(self) -> Dict[str, Any]:
        return stream_base.build_definition("smile", METRIC_ID, UNIT)

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
