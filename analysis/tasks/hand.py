"""The one-hand arm tasks: hand_left (her left hand) and hand_right (her right hand).

She holds the phone in one hand, so only the free hand is tested, one at a
time, measured against her own face. Each hand has its own baseline, stored
as "<task>.<reading>" (for example "hand_left.hand_sink"). Comparisons are
one-sided: only ABOVE her usual range counts as outside.
"""
from __future__ import annotations

from typing import Dict, Optional, Tuple

from analysis import baseline as baseline_mod
from analysis import config
from analysis.contract_shape import tool_result
from analysis.hand_metric import HandClipReading, hand_clip_reading, hand_frame_reading
from analysis.tasks import common

SIDES = {"hand_left": "left", "hand_right": "right"}
NAMES = tuple(SIDES)


def stored_values(task: str, clip: HandClipReading) -> Dict[str, Optional[float]]:
    """Per-hand baseline keys, e.g. {"hand_left.hand_sink": 0.02, ...}."""
    return {"%s.%s" % (task, k): v for k, v in clip.readings().items()}


def build_result(task: str, attempt: int, clip: HandClipReading, person: dict,
                 primary: str = config.HAND_PRIMARY) -> dict:
    """Pure: a clip reading + the person's stored sessions -> result dict with status."""
    side = SIDES[task]
    base = baseline_mod.make_baseline(
        baseline_mod.baseline_values(person, "%s.%s" % (task, primary)),
        min_half_width=config.MIN_HALF_WIDTH.get(primary, config.DEFAULT_MIN_HALF_WIDTH),
    )
    result = tool_result(task, attempt, clip, primary, base, sign="arm", one_sided_side=side)
    if clip.notes:
        result["notes"] = list(clip.notes)
    return common.add_status(result)


def run(task: str, clip_path: str, person: Optional[str], attempt: int,
        mirrored: bool = False, hold=None) -> Tuple[dict, Dict[str, Optional[float]]]:
    from analysis.hand_points import HandFrameReader

    side = SIDES[task]
    for path in (config.MODEL_PATH, config.HAND_MODEL_PATH):
        if not path.exists():
            raise FileNotFoundError("Model file missing: %s. Run:  python -m analysis.get_models" % path)
    readings = []
    last_ts = 0
    with HandFrameReader() as reader:
        for frame, ts in common.iter_frames(clip_path):
            face_obs, hands = reader.process(frame, ts, mirrored=mirrored)
            readings.append(hand_frame_reading(face_obs, hands, side))
            last_ts = ts
    if hold is not None:
        start_ms, end_ms = hold
    else:
        start_ms, end_ms = config.HAND_SETTLE_S * 1000.0, float(last_ts)
    clip = hand_clip_reading(readings, start_ms, end_ms)
    return build_result(task, attempt, clip, common.load_person(person)), stored_values(task, clip)
