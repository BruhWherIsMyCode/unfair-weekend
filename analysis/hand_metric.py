"""Pure arithmetic for the one-hand arm task: points in, readings out.

No mediapipe, no cv2. Only numpy. Testable on any machine.

Why relative to the face: the older person holds the phone in one hand, so
the camera moves with it. Everything is measured against HER FACE (eye line
and eye distance), never against the image.

Conventions:
- "left" and "right" mean HER left and HER right.
- In the un-mirrored image her left is on the image's RIGHT (larger x). The
  library's own handedness label is kept for display only and never decides a
  side.
- Every clip reading is oriented so that a LARGER value = more of a problem.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from analysis import config
from analysis.face_metric import FrameObservation

Point = Tuple[float, float]

PALM_IDS = (0, 5, 9, 13, 17)  # wrist and the four finger bases
SIDE_WORD = {"left": "LEFT", "right": "RIGHT"}


@dataclass
class Hand:
    """One hand found in a frame. Points are 21 (x, y) pairs in PIXELS."""

    points: List[Point]
    label: str = ""  # the library's handedness label, display only
    score: float = 0.0  # the library's handedness score, display only


@dataclass
class HandFrameReading:
    usable: bool
    reason: Optional[str] = None
    face_ok: bool = False  # face found and big enough (so a missing hand means a missing hand)
    wrong_side: bool = False  # a hand was seen, but only on the other side of the face
    unit: Optional[float] = None  # eye distance in pixels
    height: Optional[float] = None  # palm centre below the eye line, in eye distances (positive = lower)
    palm_width_ratio: Optional[float] = None  # dist(5, 17) / dist(0, 9)
    eye_line: Optional[Tuple[Point, Point]] = None  # (her_right_eye, her_left_eye), for drawing
    palm_centre: Optional[Point] = None  # for drawing
    hand_points: List[Point] = field(default_factory=list)  # the chosen hand, for drawing
    hand_label: str = ""  # library label of the chosen hand, display only
    timestamp_ms: int = 0


@dataclass
class HandClipReading:
    quality: str  # "usable" or "unusable"
    reasons: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    hand_sink: Optional[float] = None
    hand_height: Optional[float] = None
    palm_turn: Optional[float] = None
    hand_shake: Optional[float] = None
    dropped: bool = False
    frames_total: int = 0
    frames_usable: int = 0

    def readings(self) -> Dict[str, Optional[float]]:
        return {
            "hand_sink": self.hand_sink,
            "hand_height": self.hand_height,
            "palm_turn": self.palm_turn,
            "hand_shake": self.hand_shake,
        }


# ---- One frame ---------------------------------------------------------------------

def _dist(a: Point, b: Point) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def palm_centre(points: Sequence[Point]) -> Point:
    xs = [points[i][0] for i in PALM_IDS]
    ys = [points[i][1] for i in PALM_IDS]
    return (sum(xs) / len(xs), sum(ys) / len(ys))


def palm_width_ratio(points: Sequence[Point]) -> Optional[float]:
    length = _dist(points[0], points[9])
    if length < 1e-9:
        return None
    return _dist(points[5], points[17]) / length


def hand_frame_reading(
    face_obs: Optional[FrameObservation],
    hands: Optional[Sequence[Hand]],
    expected_side: str,
) -> HandFrameReading:
    """One frame: face observation + hands found -> one reading for the expected side."""
    if expected_side not in SIDE_WORD:
        raise ValueError("expected_side must be 'left' or 'right'")
    ts = face_obs.timestamp_ms if face_obs is not None else 0
    if face_obs is None or not face_obs.face_found:
        return HandFrameReading(usable=False, reason="face not found", timestamp_ms=ts)
    eye_l = face_obs.points.get("her_left_eye")
    eye_r = face_obs.points.get("her_right_eye")
    if eye_l is None or eye_r is None:
        return HandFrameReading(usable=False, reason="face not found", timestamp_ms=ts)
    ex, ey = eye_l[0] - eye_r[0], eye_l[1] - eye_r[1]
    unit = math.hypot(ex, ey)
    if unit < config.MIN_EYE_DISTANCE_PX:
        return HandFrameReading(usable=False, reason="face too small or far away", unit=unit,
                                timestamp_ms=ts)

    mid_x = (eye_l[0] + eye_r[0]) / 2.0
    normal = (-ey / unit, ex / unit)  # towards the chin when the face is upright
    sign = 1.0 if expected_side == "left" else -1.0  # her left = larger x

    cands = []  # (distance from midline on the expected side, hand, centre)
    other_side = False
    for hand in hands or []:
        if len(hand.points) < 21:
            continue
        c = palm_centre(hand.points)
        off = (c[0] - mid_x) * sign
        if off > 0:
            cands.append((off, hand, c))
        else:
            other_side = True

    word = SIDE_WORD[expected_side]
    if not cands:
        if other_side:
            return HandFrameReading(
                usable=False, reason="wrong hand: use your %s hand" % word, face_ok=True,
                wrong_side=True, unit=unit, timestamp_ms=ts)
        return HandFrameReading(
            usable=False, reason="no hand on your %s side" % word, face_ok=True, unit=unit,
            timestamp_ms=ts)

    _, hand, c = max(cands, key=lambda t: t[0])
    height = ((c[0] - eye_r[0]) * normal[0] + (c[1] - eye_r[1]) * normal[1]) / unit
    return HandFrameReading(
        usable=True,
        face_ok=True,
        unit=unit,
        height=height,
        palm_width_ratio=palm_width_ratio(hand.points),
        eye_line=(eye_r, eye_l),
        palm_centre=c,
        hand_points=list(hand.points),
        hand_label=hand.label,
        timestamp_ms=ts,
    )


# ---- One clip -------------------------------------------------------------------------

def _median(values: Sequence[float]) -> float:
    return float(np.median(np.asarray(values, dtype=float)))


def _detrended_std(frames: Sequence[HandFrameReading]) -> Optional[float]:
    """Spread of height around a straight line fitted through time."""
    if len(frames) < 3:
        return None
    t = np.asarray([f.timestamp_ms / 1000.0 for f in frames], dtype=float)
    y = np.asarray([f.height for f in frames], dtype=float)
    if float(t.max() - t.min()) < 1e-9:
        return float(np.std(y))
    slope, intercept = np.polyfit(t, y, 1)
    resid = y - (slope * t + intercept)
    return float(np.std(resid))


def hand_clip_reading(
    frame_readings: Sequence[Optional[HandFrameReading]],
    hold_start_ms: float,
    hold_end_ms: float,
) -> HandClipReading:
    """Frame readings -> one clip reading, using only frames inside the hold."""
    window_ms = config.HAND_WINDOW_S * 1000.0
    frames = [f for f in frame_readings
              if f is not None and hold_start_ms <= f.timestamp_ms <= hold_end_ms]
    total = len(frames)
    usable = [f for f in frames if f.usable]
    n = len(usable)

    if n == 0 and not any(f.wrong_side for f in frames):
        return HandClipReading(
            quality="unusable",
            reasons=["hand not in the picture: hold it up next to your face"],
            frames_total=total, frames_usable=0)

    start_all = [f for f in frames if f.timestamp_ms < hold_start_ms + window_ms]
    end_all = [f for f in frames if f.timestamp_ms > hold_end_ms - window_ms]
    start_win = [f for f in start_all if f.usable]
    end_win = [f for f in end_all if f.usable]

    # Dropped hand: seen at the start, missing for most of the end while the face is still there.
    end_face = [f for f in end_all if f.face_ok]
    dropped = (
        len(start_win) >= 3
        and len(end_face) >= 3
        and sum(1 for f in end_face if not f.usable) > len(end_face) / 2.0
    )

    if not dropped:
        reasons: List[str] = []
        if n < config.HAND_MIN_USABLE_FRAMES:
            reasons.append("too few usable frames (%d of %d, need %d)"
                           % (n, total, config.HAND_MIN_USABLE_FRAMES))
            counts: Dict[str, int] = {}
            for f in frames:
                if not f.usable and f.reason:
                    counts[f.reason] = counts.get(f.reason, 0) + 1
            if counts:
                reasons.append("most common problem: %s" % max(counts, key=lambda k: counts[k]))
        if len(start_win) < 3:
            reasons.append("hand not seen steadily at the start of the hold")
        if len(end_win) < 3:
            reasons.append("hand not seen steadily at the end of the hold")
        if reasons:
            return HandClipReading(quality="unusable", reasons=reasons,
                                   frames_total=total, frames_usable=n)
    elif not config.DROP_COUNTS_AS_SINK:
        return HandClipReading(quality="unusable", reasons=["hand dropped out of view"],
                               dropped=True, frames_total=total, frames_usable=n)

    start_h = _median([f.height for f in start_win])
    notes: List[str] = []
    if dropped:
        sink: Optional[float] = config.DROP_SINK_VALUE
        turn: Optional[float] = None
        notes.append("hand dropped out of view")
    else:
        end_h = _median([f.height for f in end_win])
        sink = end_h - start_h
        r_start = [f.palm_width_ratio for f in start_win if f.palm_width_ratio is not None]
        r_end = [f.palm_width_ratio for f in end_win if f.palm_width_ratio is not None]
        turn = None
        if r_start and r_end:
            rs = _median(r_start)
            if rs > 1e-9:
                turn = (rs - _median(r_end)) / rs

    return HandClipReading(
        quality="usable",
        reasons=[],
        notes=notes,
        hand_sink=sink,
        hand_height=start_h,
        palm_turn=turn,
        hand_shake=_detrended_std(usable),
        dropped=dropped,
        frames_total=total,
        frames_usable=n,
    )
