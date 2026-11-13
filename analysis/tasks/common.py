"""Shared helpers for the task modules. Pure Python; cv2 is imported only when a video is read."""
from __future__ import annotations

from typing import Iterator, Optional, Tuple

from analysis import baseline as baseline_mod
from analysis import config

STATUSES = ("as_usual", "different_from_usual", "unusable", "learning")


def status_of(result: dict) -> str:
    """as_usual / different_from_usual / unusable / learning, from a section-6 result."""
    if result.get("quality") != "usable" or result.get("reading") is None:
        return "unusable"
    if not result.get("baseline", {}).get("ready"):
        return "learning"
    return "different_from_usual" if result["reading"].get("outside") else "as_usual"


def add_status(result: dict) -> dict:
    result["status"] = status_of(result)
    return result


def load_person(person: Optional[str]) -> dict:
    if not person:
        return {"sessions": []}
    return baseline_mod.load_person(baseline_mod.person_path(person))


def iter_frames(clip_path: str) -> Iterator[Tuple[object, int]]:
    """Yield (frame, timestamp_ms) for every frame of a video file. Raises OSError if unreadable."""
    import cv2  # imported here so the pure parts work without opencv

    cap = cv2.VideoCapture(str(clip_path))
    if not cap.isOpened():
        raise OSError("Cannot open clip: %s" % clip_path)
    fps = cap.get(cv2.CAP_PROP_FPS)
    if not fps or fps != fps or fps <= 0:
        fps = config.DEFAULT_FPS
    try:
        i = 0
        last_ts = -1
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            ts = max(int(i * 1000.0 / fps), last_ts + 1)
            last_ts = ts
            yield frame, ts
            i += 1
    finally:
        cap.release()
