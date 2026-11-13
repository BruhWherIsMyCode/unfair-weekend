"""The ONLY module that imports mediapipe for hands. Frame in, hands out.

Conventions (same as face_points.py):
- "Left" and "right" mean HER left and HER right; sides are decided in
  hand_metric by geometry, never by the library's handedness label (kept here
  for display only).
- With mirrored=True the frame is flipped horizontally BEFORE detection, so
  hand and face points share the same true, un-mirrored pixel space.
- Landmark x, y are converted to PIXELS.
"""
from __future__ import annotations

from typing import List, Tuple

import cv2
import mediapipe as mp
import numpy as np

from analysis import config
from analysis.face_metric import FrameObservation
from analysis.face_points import FacePointsExtractor
from analysis.hand_metric import Hand


class HandPointsExtractor:
    """Context manager around the VIDEO-mode Hand Landmarker (two hands)."""

    def __init__(self, model_path) -> None:
        self._model_path = str(model_path)
        self._landmarker = None
        self._last_ts = -1

    def __enter__(self) -> "HandPointsExtractor":
        options = mp.tasks.vision.HandLandmarkerOptions(
            base_options=mp.tasks.BaseOptions(model_asset_path=self._model_path),
            running_mode=mp.tasks.vision.RunningMode.VIDEO,
            num_hands=2,
        )
        self._landmarker = mp.tasks.vision.HandLandmarker.create_from_options(options)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._landmarker is not None:
            self._landmarker.close()
            self._landmarker = None

    def process(self, frame_bgr, timestamp_ms: int, mirrored: bool = False) -> List[Hand]:
        if self._landmarker is None:
            raise RuntimeError("Use HandPointsExtractor as a context manager.")
        if mirrored:
            frame_bgr = cv2.flip(frame_bgr, 1)
        h, w = frame_bgr.shape[:2]
        rgb = np.ascontiguousarray(cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB))
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)

        ts = int(timestamp_ms)
        if ts <= self._last_ts:
            ts = self._last_ts + 1
        self._last_ts = ts

        result = self._landmarker.detect_for_video(mp_image, ts)
        hands: List[Hand] = []
        for i, lms in enumerate(result.hand_landmarks or []):
            label, score = "", 0.0
            if result.handedness and i < len(result.handedness) and result.handedness[i]:
                cat = result.handedness[i][0]
                label, score = cat.category_name, float(cat.score)
            hands.append(Hand(points=[(lm.x * w, lm.y * h) for lm in lms], label=label, score=score))
        return hands


class HandFrameReader:
    """Runs the existing face extractor and the hand landmarker on the same frame."""

    def __init__(self, face_model_path=None, hand_model_path=None) -> None:
        self._face = FacePointsExtractor(face_model_path or config.MODEL_PATH)
        self._hand = HandPointsExtractor(hand_model_path or config.HAND_MODEL_PATH)

    def __enter__(self) -> "HandFrameReader":
        self._face.__enter__()
        try:
            self._hand.__enter__()
        except Exception:
            self._face.__exit__(None, None, None)
            raise
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self._hand.__exit__(exc_type, exc, tb)
        self._face.__exit__(exc_type, exc, tb)

    def process(self, frame_bgr, timestamp_ms: int, mirrored: bool = False) -> Tuple[FrameObservation, List[Hand]]:
        face_obs = self._face.process(frame_bgr, timestamp_ms, mirrored=mirrored)
        hands = self._hand.process(frame_bgr, timestamp_ms, mirrored=mirrored)
        return face_obs, hands
