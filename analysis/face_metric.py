"""Pure arithmetic for the face tool: points in, readings out.

No mediapipe, no cv2. Only numpy. Testable on any machine.

Conventions:
- "left" and "right" mean HER left and HER right (the person in the video).
- Every reading is oriented so that a POSITIVE value means her LEFT side is
  lower or weaker, negative means her right side, zero means level.
- Measuring happens on the true, un-mirrored face. If the camera image was
  mirrored, face_points.py flips it back before detection.
- In the un-mirrored image her left side appears on the image's RIGHT
  (larger x). Sides are assigned by that geometry, never by landmark index.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from analysis import config

Point = Tuple[float, float]

POINT_NAMES = ("her_left_eye", "her_right_eye", "her_left_mouth", "her_right_mouth")


@dataclass
class FrameObservation:
    """What the extractor saw in one frame. Points are in PIXELS."""

    face_found: bool
    image_w: int = 0
    image_h: int = 0
    points: Dict[str, Point] = field(default_factory=dict)  # the four POINT_NAMES
    blendshapes: Dict[str, float] = field(default_factory=dict)
    yaw_deg: Optional[float] = None
    pitch_deg: Optional[float] = None
    roll_deg: Optional[float] = None
    timestamp_ms: int = 0


@dataclass
class FrameReading:
    usable: bool
    reason: Optional[str] = None
    unit: Optional[float] = None  # eye distance in pixels
    below_left: Optional[float] = None  # her left mouth corner below the eye line, in units
    below_right: Optional[float] = None
    corner_height: Optional[float] = None  # below_left - below_right
    smile_strength: Optional[float] = None
    smile_score: Optional[float] = None  # mouthSmileRight - mouthSmileLeft
    yaw_deg: Optional[float] = None
    pitch_deg: Optional[float] = None
    roll_deg: Optional[float] = None
    timestamp_ms: int = 0


@dataclass
class ClipReading:
    quality: str  # "usable" or "unusable"
    reasons: List[str] = field(default_factory=list)
    corner_height: Optional[float] = None
    corner_lift: Optional[float] = None
    smile_score: Optional[float] = None
    frames_total: int = 0
    frames_usable: int = 0
    peak_strength: Optional[float] = None
    relaxed_strength: Optional[float] = None

    def readings(self) -> Dict[str, Optional[float]]:
        return {
            "corner_height": self.corner_height,
            "corner_lift": self.corner_lift,
            "smile_score": self.smile_score,
        }


# ---- Sides and mirroring ------------------------------------------------------

def assign_sides(a: Point, b: Point) -> Tuple[Point, Point]:
    """Return (her_left, her_right) from two points of a pair.

    In the un-mirrored image her left is on the image's right, so the point
    with the larger x is her left.
    """
    if a[0] >= b[0]:
        return a, b
    return b, a


def unmirror_points(points: Dict[str, Point], image_w: float) -> Dict[str, Point]:
    """Undo a horizontal mirror on named points, then re-assign sides by geometry."""
    flipped = {k: (image_w - v[0], v[1]) for k, v in points.items()}
    eye_l, eye_r = assign_sides(flipped["her_left_eye"], flipped["her_right_eye"])
    mouth_l, mouth_r = assign_sides(flipped["her_left_mouth"], flipped["her_right_mouth"])
    return {
        "her_left_eye": eye_l,
        "her_right_eye": eye_r,
        "her_left_mouth": mouth_l,
        "her_right_mouth": mouth_r,
    }


# ---- Head angles ---------------------------------------------------------------

def angles_from_matrix(matrix) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """Yaw, pitch, roll in degrees from a 4x4 (or 3x3) transformation matrix.

    [unverified] The angle convention is a guess: R = Ry(yaw) Rx(pitch) Rz(roll),
    and the sign of each angle is not checked against a real head. The live
    window shows the angles so they can be checked by turning the head.
    Returns (None, None, None) when the matrix is missing or unusable.
    """
    if matrix is None:
        return None, None, None
    try:
        m = np.asarray(matrix, dtype=float)
        rot = m[:3, :3].copy()
        norms = np.linalg.norm(rot, axis=0)  # the matrix may contain scale
        if not np.all(norms > 1e-9):
            return None, None, None
        rot = rot / norms
    except Exception:
        return None, None, None
    yaw = math.degrees(math.atan2(rot[0, 2], rot[2, 2]))
    pitch = math.degrees(math.asin(max(-1.0, min(1.0, -rot[1, 2]))))
    roll = math.degrees(math.atan2(rot[1, 0], rot[1, 1]))
    return yaw, pitch, roll


# ---- One frame -------------------------------------------------------------------

def _below(point: Point, origin: Point, normal: Tuple[float, float], unit: float) -> float:
    return ((point[0] - origin[0]) * normal[0] + (point[1] - origin[1]) * normal[1]) / unit


def frame_reading(obs: Optional[FrameObservation]) -> Optional[FrameReading]:
    """One observation -> one frame reading. None only when obs is None."""
    if obs is None:
        return None
    base = dict(
        yaw_deg=obs.yaw_deg,
        pitch_deg=obs.pitch_deg,
        roll_deg=obs.roll_deg,
        timestamp_ms=obs.timestamp_ms,
    )
    if not obs.face_found:
        return FrameReading(usable=False, reason="face not found", **base)
    if any(name not in obs.points for name in POINT_NAMES):
        return FrameReading(usable=False, reason="face points missing", **base)

    eye_l = obs.points["her_left_eye"]
    eye_r = obs.points["her_right_eye"]
    ex, ey = eye_l[0] - eye_r[0], eye_l[1] - eye_r[1]
    unit = math.hypot(ex, ey)
    if unit < 1e-9:
        return FrameReading(usable=False, reason="eyes at the same spot", unit=unit, **base)

    # Unit normal of the eye line pointing towards the chin (down in the image
    # when the face is upright). Eye vector points to larger x, so (-ey, ex)
    # points down.
    normal = (-ey / unit, ex / unit)
    mouth_l = obs.points["her_left_mouth"]
    mouth_r = obs.points["her_right_mouth"]
    below_left = _below(mouth_l, eye_r, normal, unit)
    below_right = _below(mouth_r, eye_r, normal, unit)
    corner_height = below_left - below_right

    bs = obs.blendshapes or {}
    left_smile = bs.get("mouthSmileLeft")
    right_smile = bs.get("mouthSmileRight")
    if left_smile is not None and right_smile is not None:
        smile_strength = (left_smile + right_smile) / 2.0
        smile_score = right_smile - left_smile  # positive = her left smiles less
    else:
        mouth_width = math.hypot(mouth_l[0] - mouth_r[0], mouth_l[1] - mouth_r[1])
        smile_strength = mouth_width / unit
        smile_score = None

    reason = None
    if unit < config.MIN_EYE_DISTANCE_PX:
        reason = "face too small or far away"
    elif config.CHECK_HEAD_ANGLES and obs.yaw_deg is not None and abs(obs.yaw_deg) > config.MAX_YAW_DEG:
        reason = "head turned too far"
    elif config.CHECK_HEAD_ANGLES and obs.pitch_deg is not None and abs(obs.pitch_deg) > config.MAX_PITCH_DEG:
        reason = "head tipped too far"

    return FrameReading(
        usable=reason is None,
        reason=reason,
        unit=unit,
        below_left=below_left,
        below_right=below_right,
        corner_height=corner_height,
        smile_strength=smile_strength,
        smile_score=smile_score,
        **base,
    )


# ---- One clip ----------------------------------------------------------------------

def _median(values: Sequence[float]) -> float:
    return float(np.median(np.asarray(values, dtype=float)))


def clip_reading(frame_readings: Sequence[Optional[FrameReading]]) -> ClipReading:
    """Many frame readings -> one clip reading. Never returns a value when unusable."""
    frames = [f for f in frame_readings if f is not None]
    total = len(frames)
    usable = [f for f in frames if f.usable]
    n = len(usable)

    if n < config.MIN_USABLE_FRAMES:
        reasons = ["too few usable frames (%d of %d, need %d)" % (n, total, config.MIN_USABLE_FRAMES)]
        # Add the most common frame-level reason to help the person fix the framing.
        counts: Dict[str, int] = {}
        for f in frames:
            if not f.usable and f.reason:
                counts[f.reason] = counts.get(f.reason, 0) + 1
        if counts:
            top = max(counts, key=lambda k: counts[k])
            reasons.append("most common problem: %s" % top)
        return ClipReading(quality="unusable", reasons=reasons, frames_total=total, frames_usable=n)

    ordered = sorted(usable, key=lambda f: f.smile_strength)
    n_relaxed = max(1, int(round(n * config.RELAXED_FRACTION)))
    n_peak = min(n, max(config.MIN_PEAK_FRAMES, int(round(n * config.PEAK_FRACTION))))
    relaxed = ordered[:n_relaxed]
    peak = ordered[n - n_peak:]

    relaxed_strength = _median([f.smile_strength for f in relaxed])
    peak_strength = _median([f.smile_strength for f in peak])
    if peak_strength - relaxed_strength < config.MIN_SMILE_RISE:
        return ClipReading(
            quality="unusable",
            reasons=["no clear smile (smile strength rose %.2f, need %.2f)"
                     % (peak_strength - relaxed_strength, config.MIN_SMILE_RISE)],
            frames_total=total,
            frames_usable=n,
            peak_strength=peak_strength,
            relaxed_strength=relaxed_strength,
        )

    corner_height = _median([f.corner_height for f in peak])
    lift_left = _median([f.below_left for f in relaxed]) - _median([f.below_left for f in peak])
    lift_right = _median([f.below_right for f in relaxed]) - _median([f.below_right for f in peak])
    corner_lift = lift_right - lift_left  # positive = her left lifted less

    scores = [f.smile_score for f in peak if f.smile_score is not None]
    smile_score = _median(scores) if scores else None

    return ClipReading(
        quality="usable",
        reasons=[],
        corner_height=corner_height,
        corner_lift=corner_lift,
        smile_score=smile_score,
        frames_total=total,
        frames_usable=n,
        peak_strength=peak_strength,
        relaxed_strength=relaxed_strength,
    )
