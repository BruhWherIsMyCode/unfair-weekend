"""Remote measuring: the client measures every frame, the server runs the SAME stream task modules on the readings.

The wire form of one frame reading is a plain dict (`*_to_dict`). The server side wraps the existing StreamSmile and
StreamArms with an injected decoder and an injected frame reader:

    decoder      pass-through for format "reading" (the sample itself)
    frame reader the sample's `data` -> FrameReading / HandFrameReading (`*_from_dict`); `data` null = a frame that
                 could not be measured, the same as the production reader returning None

so observations, framing checks, watchdogs and final results run on the server with the same code as in-process.
`remote_modules()` is the factory; the in-process modules (stream_registry) are not touched.

The types live in files nobody edits here (face_metric.py, hand_metric.py), so the codecs live in this file. Drawing
fields of a hand reading (eye line, palm centre, hand points) are not sent: the client keeps them for its window.
"""
from __future__ import annotations

import dataclasses
import math
from typing import Any, Dict, Optional

from analysis.face_metric import FrameReading
from analysis.hand_metric import HandFrameReading
from analysis.tasks.stream_arms import StreamArms
from analysis.tasks.stream_sentence import StreamSentence
from analysis.tasks.stream_smile import StreamSmile

READING_FORMAT = "reading"
_SMILE_NUMBERS = ("unit", "below_left", "below_right", "corner_height", "corner_height_3d", "smile_strength",
                  "smile_score", "yaw_deg", "pitch_deg", "roll_deg")
_HAND_NUMBERS = ("unit", "height", "palm_width_ratio")


def _number(value: Any, name: str) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("%s must be a finite number or null" % name)
    return float(value)


def _common(d: Any) -> Dict[str, Any]:
    if not isinstance(d, dict):
        raise ValueError("a frame reading must be an object")
    if not isinstance(d.get("usable"), bool):
        raise ValueError("usable must be a boolean")
    reason = d.get("reason")
    if reason is not None and not isinstance(reason, str):
        raise ValueError("reason must be a string or null")
    ts = d.get("timestamp_ms", 0)
    if isinstance(ts, bool) or not isinstance(ts, int):
        raise ValueError("timestamp_ms must be an integer")
    return {"usable": d["usable"], "reason": reason[:200] if reason else None, "timestamp_ms": ts}


def smile_to_dict(reading: FrameReading) -> Dict[str, Any]:
    out: Dict[str, Any] = {"usable": bool(reading.usable), "reason": reading.reason,
                           "timestamp_ms": int(reading.timestamp_ms)}
    for name in _SMILE_NUMBERS:
        value = getattr(reading, name)
        out[name] = None if value is None or not math.isfinite(value) else float(value)
    return out


def smile_from_dict(d: Any) -> FrameReading:
    fields = _common(d)
    for name in _SMILE_NUMBERS:
        fields[name] = _number(d.get(name), name)
    return FrameReading(**fields)


def hand_to_dict(reading: HandFrameReading) -> Dict[str, Any]:
    out: Dict[str, Any] = {"usable": bool(reading.usable), "reason": reading.reason,
                           "face_ok": bool(reading.face_ok), "wrong_side": bool(reading.wrong_side),
                           "timestamp_ms": int(reading.timestamp_ms)}
    for name in _HAND_NUMBERS:
        value = getattr(reading, name)
        out[name] = None if value is None or not math.isfinite(value) else float(value)
    return out


def hand_from_dict(d: Any) -> HandFrameReading:
    fields = _common(d)
    fields["face_ok"] = bool(d.get("face_ok", False))
    fields["wrong_side"] = bool(d.get("wrong_side", False))
    for name in _HAND_NUMBERS:
        fields[name] = _number(d.get(name), name)
    return HandFrameReading(**fields)


def reading_to_dict(task: str, reading: Any) -> Optional[Dict[str, Any]]:
    """Client side: a frame reading (or None) -> the `data` of a `reading` message."""
    if reading is None:
        return None
    return smile_to_dict(reading) if task == "smile" else hand_to_dict(reading)


def passthrough_decode(sample: Dict[str, Any]) -> Any:
    """The decoder for format "reading": the sample already carries its measurement."""
    return sample


class RemoteSmileReader:
    """sample -> FrameReading, from the client's reading. Null data = not measurable (None, like the production
    reader); a malformed reading becomes an unusable frame instead of crashing the task."""

    def __call__(self, sample: Dict[str, Any], timestamp_ms: int) -> Optional[FrameReading]:
        data = sample.get("data")
        if data is None:
            return None
        try:
            reading = smile_from_dict(data)
        except ValueError:
            return FrameReading(usable=False, reason="reading could not be read", timestamp_ms=timestamp_ms)
        reading.timestamp_ms = timestamp_ms
        return reading


class RemoteHandReader:
    """sample -> HandFrameReading. The expected side was already applied by the client (from the phases)."""

    def __call__(self, sample: Dict[str, Any], timestamp_ms: int, expected_side: str) -> Optional[HandFrameReading]:
        data = sample.get("data")
        if data is None:
            return None
        try:
            reading = hand_from_dict(data)
        except ValueError:
            return HandFrameReading(usable=False, reason="reading could not be read", timestamp_ms=timestamp_ms)
        reading.timestamp_ms = timestamp_ms
        return reading


def remote_modules() -> Dict[str, Any]:
    """The task modules for a call whose client measures. Fresh instances per call."""
    return {"smile": StreamSmile(RemoteSmileReader(), passthrough_decode, use_executor=False),
            "arms": StreamArms(RemoteHandReader(), passthrough_decode, use_executor=False),
            "sentence": StreamSentence()}


def field_names(cls: Any) -> tuple:
    return tuple(f.name for f in dataclasses.fields(cls))
