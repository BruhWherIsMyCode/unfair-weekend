"""Tests for the angle-corrected reading and the "I saw" texts. Made-up points only.
Written, not run yet."""
from __future__ import annotations

import math

from analysis import config
from analysis.face_metric import (
    FrameObservation,
    clip_reading,
    corner_height_3d,
    face_frame,
    frame_reading,
    matches_asked,
    mismatch_warning,
    seen_side,
    seen_text_face,
)

CENTRE = (300.0, 300.0)


def own_frame_points(left_drop=0.0):
    """A made-up face in its own 3D frame (pixels): x to her left, y down, z towards the camera
    is negative. The forehead and the mouth sit at different depths than the eyes."""
    return {
        "her_left_eye": (40.0, 0.0, 0.0),
        "her_right_eye": (-40.0, 0.0, 0.0),
        "forehead": (0.0, -80.0, -20.0),
        "nose_bridge": (0.0, -10.0, -20.0),
        "her_left_mouth": (50.0, 80.0 + left_drop, -30.0),
        "her_right_mouth": (-50.0, 80.0, -30.0),
    }


def rotate(p, yaw_deg, pitch_deg):
    """Yaw about the y axis first, then pitch about the x axis."""
    x, y, z = p
    a = math.radians(yaw_deg)
    x, z = x * math.cos(a) + z * math.sin(a), -x * math.sin(a) + z * math.cos(a)
    b = math.radians(pitch_deg)
    y, z = y * math.cos(b) - z * math.sin(b), y * math.sin(b) + z * math.cos(b)
    return (x, y, z)


def observe(points3d, translate=CENTRE):
    """Project to pixels with z kept: 2D points are (x, y), 3D points keep z."""
    p3 = {k: (v[0] + translate[0], v[1] + translate[1], v[2]) for k, v in points3d.items()}
    p2 = {k: (v[0], v[1]) for k, v in p3.items() if k in (
        "her_left_eye", "her_right_eye", "her_left_mouth", "her_right_mouth")}
    return FrameObservation(face_found=True, image_w=600, image_h=600, points=p2, points3d=p3)


def rotated_obs(left_drop, yaw, pitch):
    pts = {k: rotate(v, yaw, pitch) for k, v in own_frame_points(left_drop).items()}
    return observe(pts)


# ---- the 3D reading --------------------------------------------------------------------------

def test_3d_reading_unchanged_by_rotation():
    straight = observe(own_frame_points(left_drop=8.0))
    turned = rotated_obs(8.0, yaw=15.0, pitch=15.0)
    h3_straight = frame_reading(straight).corner_height_3d
    h3_turned = frame_reading(turned).corner_height_3d
    assert h3_straight is not None and h3_turned is not None
    assert abs(h3_straight - h3_turned) < 1e-9
    # No claim about the 2D reading here: with this fake face a 15 deg turn moves it
    # only ~0.0005 (eye-line normalisation absorbs most of it), as in the owner's
    # real angle test, where 3D was no better than 2D.


def test_3d_level_face_stays_level_when_rotated():
    value = frame_reading(rotated_obs(0.0, yaw=15.0, pitch=15.0)).corner_height_3d
    assert abs(value) < 1e-9


def test_3d_lowered_left_corner_has_right_sign_and_size():
    value = corner_height_3d(observe(own_frame_points(left_drop=8.0)).points3d)
    assert 0.08 < value < 0.11  # about 8 px / 80 px eye distance, a little less (tilted axis)
    lowered_right = own_frame_points()
    lowered_right["her_right_mouth"] = (-50.0, 88.0, -30.0)
    assert corner_height_3d(observe(lowered_right).points3d) < -0.08


def test_3d_none_when_points_missing_or_degenerate():
    assert corner_height_3d({}) is None
    assert corner_height_3d(None) is None
    pts = own_frame_points(8.0)
    del pts["forehead"]
    assert corner_height_3d(pts) is None
    assert frame_reading(FrameObservation(face_found=True, image_w=600, image_h=600,
                                          points=observe(own_frame_points()).points)
                         ).corner_height_3d is None
    same_eyes = own_frame_points(8.0)
    same_eyes["her_left_eye"] = same_eyes["her_right_eye"]
    assert corner_height_3d(same_eyes) is None
    forehead_on_axis = own_frame_points(8.0)
    forehead_on_axis["forehead"] = (0.0, 0.0, 0.0)  # forehead at the eye mid-point
    assert face_frame(forehead_on_axis) is None


def test_face_frame_axes_are_perpendicular_and_unit():
    origin, x_axis, down, unit = face_frame(own_frame_points())
    assert abs(sum(a * b for a, b in zip(x_axis, down))) < 1e-9
    assert abs(sum(a * a for a in down) - 1.0) < 1e-9
    assert abs(unit - 80.0) < 1e-9


def test_clip_has_3d_reading_and_median_angles():
    def frame(smile, yaw, pitch):
        obs = observe(own_frame_points(left_drop=8.0))
        obs.blendshapes = {"mouthSmileLeft": smile, "mouthSmileRight": smile}
        obs.yaw_deg, obs.pitch_deg = yaw, pitch
        return frame_reading(obs)

    frames = [frame(0.05, 10.0, -4.0) for _ in range(10)] + [frame(0.8, 14.0, -6.0) for _ in range(10)]
    clip = clip_reading(frames)
    assert clip.quality == "usable"
    assert 0.08 < clip.corner_height_3d < 0.11
    assert clip.readings()["corner_height_3d"] == clip.corner_height_3d
    assert clip.yaw_deg is not None and 10.0 <= clip.yaw_deg <= 14.0
    assert clip.pitch_deg is not None and -6.0 <= clip.pitch_deg <= -4.0


def test_clip_angles_none_when_unknown():
    def frame(smile):
        obs = observe(own_frame_points())
        obs.blendshapes = {"mouthSmileLeft": smile, "mouthSmileRight": smile}
        return frame_reading(obs)

    clip = clip_reading([frame(0.05) for _ in range(10)] + [frame(0.8) for _ in range(10)])
    assert clip.quality == "usable"
    assert clip.yaw_deg is None and clip.pitch_deg is None


def test_metrics_and_min_half_width_include_the_3d_reading():
    assert "corner_height_3d" in config.METRICS
    assert config.MIN_HALF_WIDTH["corner_height_3d"] == 0.02
    assert config.FOREHEAD_ID == 10 and config.NOSE_BRIDGE_ID == 168


# ---- "I saw" text and the mismatch flag -------------------------------------------------------

def test_seen_side_and_text():
    assert seen_side(0.0) == "level"
    assert seen_side(config.SEEN_LEVEL - 0.001) == "level"
    assert seen_side(0.08) == "left"
    assert seen_side(-0.08) == "right"
    assert seen_side(None) is None
    assert seen_text_face(0.005) == "I saw: level"
    assert seen_text_face(0.08) == "I saw: your LEFT corner lower by 0.08"
    assert seen_text_face(-0.0449) == "I saw: your RIGHT corner lower by 0.04"


def test_matches_asked():
    assert matches_asked("acted_left", 0.08) is True
    assert matches_asked("acted_left", -0.08) is False  # the wrong side was lifted
    assert matches_asked("acted_left", 0.0) is False  # level
    assert matches_asked("acted_right", -0.08) is True
    assert matches_asked("acted_right", 0.08) is False
    assert matches_asked("normal", 0.08) is None
    assert matches_asked("acted_left", None) is None


def test_mismatch_warning_text():
    assert mismatch_warning("acted_left", -0.08) == \
        "That did not look like a LEFT droop. Saved as you did it, not as asked."
    assert mismatch_warning("acted_right", 0.0) == \
        "That did not look like a RIGHT droop. Saved as you did it, not as asked."
    assert mismatch_warning("acted_left", 0.08) is None
    assert mismatch_warning("normal", 0.5) is None
