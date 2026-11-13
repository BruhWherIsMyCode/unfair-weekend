"""Per-frame measuring on the client, with the SAME production readers the stream modules use.

    smile  stream_smile.ProductionFrameReader (FacePointsExtractor + face_metric.frame_reading)
    arms   stream_arms.ProductionHandReader   (HandFrameReader + hand_metric.hand_frame_reading)

Both import mediapipe lazily on their first frame. `Measurer.measure(...)` returns a frame reading or None (on any
failure; the first failure is printed once). `hint_for(...)` is pure: the plain words shown in "WHAT I SEE".

During a capture the client measures one frame per sample period (the state machine decides which frames; the ones in
between are dropped). Outside captures it measures at about 2 frames per second, only for the on-screen hint.
"""
from __future__ import annotations

from typing import Any, Callable, Optional

from analysis.pc.remote_tasks import reading_to_dict

HINT_PERIOD_S = 0.5  # outside captures: about 2 frames per second
SIDE_WORD = {"left": "LEFT", "right": "RIGHT"}


def hint_for(task: str, reading: Any, expected_side: Optional[str], phase: Optional[str] = None) -> str:
    """Plain words for what the camera sees right now."""
    if task == "smile":
        if reading is None or not reading.usable:
            why = (getattr(reading, "reason", None) or "") if reading is not None else ""
            if "small" in why or "far" in why:
                return "come closer to the camera"
            if "turn" in why or "tilt" in why or "head" in why or "angle" in why:
                return "look straight at the camera"
            return "face not visible"
        return "going fine"
    # arms
    if expected_side is None:
        return "get ready" if phase in ("settle", None) else "now swap hands" if phase == "swap" else "going fine"
    word = SIDE_WORD.get(expected_side, "")
    if reading is None:
        return "face not visible"
    if reading.usable:
        return "going fine"
    if getattr(reading, "wrong_side", False):
        return "Raise your %s hand" % word
    if not getattr(reading, "face_ok", False):
        return "face not visible"
    return "Raise your %s hand" % word


class Measurer:
    def __init__(self, mirrored: bool = False, log: Callable[[str], Any] = print) -> None:
        self.mirrored = mirrored
        self._log = log
        self._smile: Any = None
        self._hands: Any = None
        self._failed = False

    def _smile_reader(self) -> Any:
        if self._smile is None:
            from analysis.tasks.stream_smile import ProductionFrameReader

            self._smile = ProductionFrameReader(mirrored=self.mirrored)
        return self._smile

    def _hand_reader(self) -> Any:
        if self._hands is None:
            from analysis.tasks.stream_arms import ProductionHandReader

            self._hands = ProductionHandReader(mirrored=self.mirrored)
        return self._hands

    def measure(self, task: str, frame: Any, t_ms: int, expected_side: Optional[str]) -> Any:
        """A FrameReading (smile) or HandFrameReading (arms), or None when the frame could not be measured.
        `t_ms` must increase from call to call (the readers need strictly increasing times)."""
        if frame is None:
            return None
        try:
            if task == "arms" and expected_side is not None:
                return self._hand_reader()(frame, t_ms, expected_side)
            return self._smile_reader()(frame, t_ms)
        except FileNotFoundError:
            raise  # a missing model file is not a bad frame: the program says so and stops
        except Exception as exc:
            if not self._failed:
                self._failed = True
                self._log("measuring failed (%s); further failures are not printed" % type(exc).__name__)
            return None

    def data_for(self, task: str, reading: Any) -> Any:
        return reading_to_dict(task, reading)

    def close(self) -> None:
        for reader in (self._smile, self._hands):
            if reader is not None:
                reader.close()
