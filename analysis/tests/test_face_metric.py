from __future__ import annotations

import math

from analysis import config
from analysis.baseline import compare, make_baseline
from analysis.face_metric import (
    FrameObservation,
    clip_reading,
    frame_reading,
    unmirror_points,
)

W = H = 200


def make_obs(below_left=0.8, below_right=0.8, smile=0.5, eye_dx=40.0, yaw=None,
             blend=True, left_smile=None, right_smile=None):
    """Eyes at y=100 (her right x=80, her left x=120). Mouth y = 100 + below*unit."""
    unit = eye_dx
    cx = 100.0
    pts = {
        "her_right_eye": (cx - eye_dx / 2, 100.0),
        "her_left_eye": (cx + eye_dx / 2, 100.0),
        "her_right_mouth": (cx - 25.0, 100.0 + below_right * unit),
        "her_left_mouth": (cx + 25.0, 100.0 + below_left * unit),
    }
    bs = {}
    if blend:
        ls = smile if left_smile is None else left_smile
        rs = smile if right_smile is None else right_smile
        bs = {"mouthSmileLeft": ls, "mouthSmileRight": rs}
    return FrameObservation(face_found=True, image_w=W, image_h=H, points=pts,
                            blendshapes=bs, yaw_deg=yaw, pitch_deg=0.0, roll_deg=0.0)


def rotate(obs, degrees):
    a = math.radians(degrees)
    cx, cy = W / 2, H / 2
    out = {}
    for k, (x, y) in obs.points.items():
        dx, dy = x - cx, y - cy
        out[k] = (cx + dx * math.cos(a) - dy * math.sin(a), cy + dx * math.sin(a) + dy * math.cos(a))
    return FrameObservation(face_found=True, image_w=W, image_h=H, points=out,
                            blendshapes=obs.blendshapes, yaw_deg=obs.yaw_deg,
                            pitch_deg=obs.pitch_deg, roll_deg=obs.roll_deg)


def build_clip(relaxed, peak, n_relaxed=10, n_peak=10, relaxed_smile=0.05, peak_smile=0.8):
    frames = []
    for _ in range(n_relaxed):
        frames.append(frame_reading(make_obs(relaxed[0], relaxed[1], smile=relaxed_smile)))
    for _ in range(n_peak):
        frames.append(frame_reading(make_obs(peak[0], peak[1], smile=peak_smile)))
    return frames


def test_level_face_is_zero():
    fr = frame_reading(make_obs())
    assert fr.usable
    assert abs(fr.corner_height) < 1e-9


def test_left_corner_lowered_is_positive_and_side_left():
    fr = frame_reading(make_obs(below_left=0.9, below_right=0.8))
    assert fr.corner_height > 0.09
    base = make_baseline([0.0, 0.01, -0.01, 0.0, 0.005], metric="corner_height")
    assert compare(fr.corner_height, base)["side"] == "left"


def test_rotation_15_degrees_still_about_zero():
    fr = frame_reading(rotate(make_obs(), 15))
    assert fr.usable
    assert abs(fr.corner_height) < 1e-6


def test_rotation_keeps_lowered_left_value():
    plain = frame_reading(make_obs(below_left=0.9, below_right=0.8)).corner_height
    tilted = frame_reading(rotate(make_obs(below_left=0.9, below_right=0.8), 15)).corner_height
    assert abs(plain - tilted) < 1e-6


def test_mirrored_input_unmirrored_gives_same_reading():
    obs = make_obs(below_left=0.9, below_right=0.8)
    original = frame_reading(obs).corner_height
    mirrored_points = {k: (W - x, y) for k, (x, y) in obs.points.items()}
    # What the camera delivers if it mirrors: x flipped. Undo it, assign sides by geometry.
    fixed = unmirror_points(mirrored_points, W)
    fixed_obs = FrameObservation(face_found=True, image_w=W, image_h=H, points=fixed,
                                 blendshapes=obs.blendshapes)
    assert abs(frame_reading(fixed_obs).corner_height - original) < 1e-9


def test_mirroring_without_correction_flips_the_sign():
    # Documents the trap: a mirrored image read as if it were not mirrored swaps the sides.
    obs = make_obs(below_left=0.9, below_right=0.8)
    original = frame_reading(obs).corner_height
    mirrored_points = {k: (W - x, y) for k, (x, y) in obs.points.items()}
    # Sides re-assigned by geometry (larger x = her left) with NO un-mirroring.
    sw = {
        "her_left_eye": mirrored_points["her_right_eye"],
        "her_right_eye": mirrored_points["her_left_eye"],
        "her_left_mouth": mirrored_points["her_right_mouth"],
        "her_right_mouth": mirrored_points["her_left_mouth"],
    }
    wrong = frame_reading(FrameObservation(face_found=True, image_w=W, image_h=H, points=sw))
    assert wrong.corner_height < 0 < original


def test_small_face_unusable():
    fr = frame_reading(make_obs(eye_dx=config.MIN_EYE_DISTANCE_PX - 5))
    assert not fr.usable
    assert fr.reason


def test_yaw_beyond_limit_unusable():
    fr = frame_reading(make_obs(yaw=config.MAX_YAW_DEG + 5))
    assert not fr.usable
    assert "turned" in fr.reason


def test_face_not_found_unusable():
    fr = frame_reading(FrameObservation(face_found=False))
    assert not fr.usable
    assert fr.reason == "face not found"


def test_clip_without_smile_is_unusable():
    frames = [frame_reading(make_obs(smile=0.3)) for _ in range(20)]
    clip = clip_reading(frames)
    assert clip.quality == "unusable"
    assert clip.reasons
    assert clip.corner_height is None


def test_clip_with_too_few_usable_frames_is_unusable():
    frames = [frame_reading(make_obs(smile=0.1 + 0.1 * i)) for i in range(config.MIN_USABLE_FRAMES - 1)]
    clip = clip_reading(frames)
    assert clip.quality == "unusable"
    assert clip.corner_height is None


def test_good_clip_is_usable_and_level():
    clip = clip_reading(build_clip((0.8, 0.8), (0.75, 0.75)))
    assert clip.quality == "usable"
    assert abs(clip.corner_height) < 1e-9
    assert abs(clip.corner_lift) < 1e-9


def test_corner_lift_positive_when_left_rises_less():
    # Right corner rises 0.10, left rises 0.02.
    clip = clip_reading(build_clip((0.8, 0.8), (0.78, 0.70)))
    assert clip.quality == "usable"
    assert clip.corner_lift > 0.07
    # Left corner ends lower than right too.
    assert clip.corner_height > 0


def test_smile_score_sign():
    # Her left smiles less -> positive.
    frames = [frame_reading(make_obs(smile=0.05)) for _ in range(10)]
    frames += [frame_reading(make_obs(left_smile=0.5, right_smile=0.9)) for _ in range(10)]
    clip = clip_reading(frames)
    assert clip.quality == "usable"
    assert clip.smile_score > 0.3
