"""Where camera frames come from. This is the ONLY module of the PC version that opens a camera (cv2.VideoCapture).

    FrameSource.read() -> (ok, frame_bgr, t_ms)     t_ms = milliseconds on a monotonic clock, from this source's start
    WebcamSource                                     a local webcam (cv2 imported lazily)

A later phone stream plugs in here as another FrameSource. `CameraWatch` is the pure part: it looks at the frames
(and at failed reads) and says whether the camera counts as available:

    read failures for 1 s                         -> unavailable, reason "camera_lost"
    dark or near-uniform picture for 1 s          -> unavailable, reason "camera_blocked" (a hand over the lens)

A good frame makes it available again at once. The thresholds are first guesses, not measured.
"""
from __future__ import annotations

import time
from typing import Any, Optional, Tuple

import numpy as np

CAMERA_MIN_BRIGHTNESS = 15.0  # mean pixel value (0-255) below this counts as covered; first guess
CAMERA_MIN_STD = 3.0  # standard deviation of pixel values below this counts as a uniform picture; first guess
CAMERA_BAD_SECONDS = 1.0  # how long a covered picture or failed reads last before the status changes; first guess


def frame_is_blocked(frame: Any, min_brightness: float = CAMERA_MIN_BRIGHTNESS, min_std: float = CAMERA_MIN_STD) -> bool:
    """True for a dark or near-uniform frame. Pure."""
    if frame is None:
        return False
    arr = np.asarray(frame)
    if arr.size == 0:
        return True
    return bool(arr.mean() < min_brightness or arr.std() < min_std)


class CameraWatch:
    """Turns frames and read failures into (state, reason). Time is passed in, so tests use plain numbers."""

    def __init__(self, bad_seconds: float = CAMERA_BAD_SECONDS) -> None:
        self.bad_seconds = bad_seconds
        self.state = "unavailable"  # until the first good frame
        self.reason: Optional[str] = "camera_lost"
        self._fail_since: Optional[float] = None
        self._blocked_since: Optional[float] = None

    def observe(self, now: float, ok: bool, frame: Any = None) -> Tuple[str, Optional[str]]:
        if not ok or frame is None:
            self._blocked_since = None
            if self._fail_since is None:
                self._fail_since = now
            if now - self._fail_since >= self.bad_seconds:
                self.state, self.reason = "unavailable", "camera_lost"
            return self.state, self.reason
        self._fail_since = None
        if frame_is_blocked(frame):
            if self._blocked_since is None:
                self._blocked_since = now
            if now - self._blocked_since >= self.bad_seconds:
                self.state, self.reason = "unavailable", "camera_blocked"
            return self.state, self.reason
        self._blocked_since = None
        self.state, self.reason = "available", None
        return self.state, self.reason


class FrameSource:
    def read(self) -> Tuple[bool, Any, int]:
        raise NotImplementedError

    def close(self) -> None:
        pass


class WebcamSource(FrameSource):
    def __init__(self, index: int = 0) -> None:
        import cv2  # imported here: the only camera code of the PC version

        self._cv2 = cv2
        self._cap = cv2.VideoCapture(index)
        self._t0 = time.monotonic()

    @property
    def opened(self) -> bool:
        return bool(self._cap is not None and self._cap.isOpened())

    def read(self) -> Tuple[bool, Any, int]:
        t_ms = int((time.monotonic() - self._t0) * 1000)
        if not self.opened:
            return False, None, t_ms
        ok, frame = self._cap.read()
        return bool(ok), (frame if ok else None), t_ms

    def close(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None
