"""The smile task: the existing clip measuring of measure_clip.py, wrapped as a task."""
from __future__ import annotations

from typing import Dict, Optional, Tuple

from analysis import baseline as baseline_mod
from analysis import config
from analysis.contract_shape import tool_result
from analysis.face_metric import clip_reading, frame_reading
from analysis.tasks import common

NAMES = ("smile",)
PRIMARY = "corner_height"


def run(task: str, clip_path: str, person: Optional[str], attempt: int,
        mirrored: bool = False, hold=None) -> Tuple[dict, Dict[str, Optional[float]]]:
    """Returns (result, values to store). `hold` is not used by the smile task."""
    from analysis.face_points import FacePointsExtractor

    if not config.MODEL_PATH.exists():
        raise FileNotFoundError("Model file missing. Run:  python -m analysis.get_models")
    readings = []
    with FacePointsExtractor(config.MODEL_PATH) as extractor:
        for frame, ts in common.iter_frames(clip_path):
            readings.append(frame_reading(extractor.process(frame, ts, mirrored=mirrored)))
    clip = clip_reading(readings)
    base = baseline_mod.baseline_for(common.load_person(person), PRIMARY)
    result = tool_result("smile", attempt, clip, PRIMARY, base)
    return common.add_status(result), clip.readings()
