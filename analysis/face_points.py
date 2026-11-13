"""The ONLY module that imports mediapipe. Frame in, FrameObservation out.

Conventions:
- "Left" and "right" mean HER left and HER right.
- Frames may be mirrored (front cameras often mirror). With mirrored=True the
  frame is flipped horizontally BEFORE detection, so all measuring happens on
  the true, un-mirrored face.
- In the un-mirrored image her left is on the image's right (larger x), so
  sides are assigned by geometry, not by landmark index.
- Landmark x, y are converted to PIXELS before any distance is computed.
"""
from __future__ import annotations

from typing import Dict, Optional

import cv2
import mediapipe as mp
import numpy as np

from analysis import config
from analysis.face_metric import FrameObservation, angles_from_matrix, assign_sides


class FacePointsExtractor:
    """Context manager around the VIDEO-mode Face Landmarker."""

    def __init__(self, model_path) -> None:
        self._model_path = str(model_path)
        self._landmarker = None
        self._last_ts = -1

    def __enter__(self) -> "FacePointsExtractor":
        base_options = mp.tasks.BaseOptions
        options_cls = mp.tasks.vision.FaceLandmarkerOptions
        options = options_cls(
            base_options=base_options(model_asset_path=self._model_path),
            running_mode=mp.tasks.vision.RunningMode.VIDEO,
            num_faces=1,
            output_face_blendshapes=True,
            output_facial_transformation_matrixes=True,
        )
        self._landmarker = mp.tasks.vision.FaceLandmarker.create_from_options(options)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._landmarker is not None:
            self._landmarker.close()
            self._landmarker = None

    def process(self, frame_bgr, timestamp_ms: int, mirrored: bool = False) -> FrameObservation:
        if self._landmarker is None:
            raise RuntimeError("Use FacePointsExtractor as a context manager.")
        if mirrored:
            frame_bgr = cv2.flip(frame_bgr, 1)
        h, w = frame_bgr.shape[:2]
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        rgb = np.ascontiguousarray(rgb)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)

        # Timestamps must increase for VIDEO mode.
        ts = int(timestamp_ms)
        if ts <= self._last_ts:
            ts = self._last_ts + 1
        self._last_ts = ts

        result = self._landmarker.detect_for_video(mp_image, ts)
        if not result.face_landmarks:
            return FrameObservation(face_found=False, image_w=w, image_h=h, timestamp_ms=ts)

        lms = result.face_landmarks[0]

        def px(idx: int):
            lm = lms[idx]
            return (lm.x * w, lm.y * h)

        def px3(idx: int):
            # MediaPipe's z uses roughly the same scale as x, so z * w.
            lm = lms[idx]
            return (lm.x * w, lm.y * h, lm.z * w)

        eye_a, eye_b = (px(i) for i in config.EYE_OUTER_CORNER_IDS)
        mouth_a, mouth_b = (px(i) for i in config.MOUTH_CORNER_IDS)
        eye_l, eye_r = assign_sides(eye_a, eye_b)
        mouth_l, mouth_r = assign_sides(mouth_a, mouth_b)

        # The same pairs in 3D, sides assigned the same way (by the picture x).
        eye3_a, eye3_b = (px3(i) for i in config.EYE_OUTER_CORNER_IDS)
        mouth3_a, mouth3_b = (px3(i) for i in config.MOUTH_CORNER_IDS)
        eye3_l, eye3_r = (eye3_a, eye3_b) if eye_a[0] >= eye_b[0] else (eye3_b, eye3_a)
        mouth3_l, mouth3_r = (mouth3_a, mouth3_b) if mouth_a[0] >= mouth_b[0] else (mouth3_b, mouth3_a)
        points3d = {
            "her_left_eye": eye3_l,
            "her_right_eye": eye3_r,
            "her_left_mouth": mouth3_l,
            "her_right_mouth": mouth3_r,
            "forehead": px3(config.FOREHEAD_ID),
            "nose_bridge": px3(config.NOSE_BRIDGE_ID),
        }

        blendshapes: Dict[str, float] = {}
        if result.face_blendshapes:
            for cat in result.face_blendshapes[0]:
                blendshapes[cat.category_name] = float(cat.score)

        yaw = pitch = roll = None  # type: Optional[float]
        if result.facial_transformation_matrixes:
            yaw, pitch, roll = angles_from_matrix(result.facial_transformation_matrixes[0])

        return FrameObservation(
            face_found=True,
            image_w=w,
            image_h=h,
            points={
                "her_left_eye": eye_l,
                "her_right_eye": eye_r,
                "her_left_mouth": mouth_l,
                "her_right_mouth": mouth_r,
            },
            blendshapes=blendshapes,
            yaw_deg=yaw,
            pitch_deg=pitch,
            roll_deg=roll,
            timestamp_ms=ts,
            mesh=[(lm.x * w, lm.y * h) for lm in lms],
            points3d=points3d,
        )
