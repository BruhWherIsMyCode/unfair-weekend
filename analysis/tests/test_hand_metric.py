"""Hand arithmetic on made-up points. No mediapipe, no camera."""
import math

import pytest

from analysis import config
from analysis.face_metric import FrameObservation
from analysis.hand_metric import (
    Hand, HandFrameReading, hand_clip_reading, hand_frame_reading, palm_centre, palm_width_ratio,
)


def face(eye_l=(400.0, 200.0), eye_r=(300.0, 200.0), ts=0, found=True):
    if not found:
        return FrameObservation(face_found=False, image_w=640, image_h=480, timestamp_ms=ts)
    return FrameObservation(
        face_found=True, image_w=640, image_h=480, timestamp_ms=ts,
        points={"her_left_eye": eye_l, "her_right_eye": eye_r,
                "her_left_mouth": (380.0, 280.0), "her_right_mouth": (320.0, 280.0)})


def hand_at(cx, cy, width=40.0, length=60.0, label="Left"):
    """21 points; the palm centre ends up exactly at (cx, cy)."""
    pts = [(cx, cy)] * 21
    # wrist (0) and finger bases (5, 9, 13, 17) arranged so their mean is (cx, cy)
    pts = list(pts)
    pts[0] = (cx, cy + length / 2.0 * 1.0)
    pts[9] = (cx, cy - length / 2.0 * 1.0)
    pts[5] = (cx - width / 2.0, cy - length / 2.0 * 0.0)
    pts[17] = (cx + width / 2.0, cy - length / 2.0 * 0.0)
    pts[13] = (cx, cy)
    return Hand(points=pts, label=label, score=0.9)


def test_palm_centre_is_mean_of_five_points():
    h = hand_at(500.0, 250.0)
    cx, cy = palm_centre(h.points)
    assert cx == pytest.approx(500.0)
    assert cy == pytest.approx(250.0 + (30.0 - 30.0 + 0 + 0 + 0) / 5.0)


def test_her_left_hand_is_on_larger_x():
    f = face()  # midline x = 350
    r = hand_at(450.0, 300.0)
    ok = hand_frame_reading(f, [r], "left")
    assert ok.usable
    bad = hand_frame_reading(f, [r], "right")
    assert not bad.usable
    assert bad.reason == "wrong hand: use your RIGHT hand"
    assert bad.wrong_side


def test_her_right_hand_is_on_smaller_x():
    f = face()
    h = hand_at(250.0, 300.0)
    assert hand_frame_reading(f, [h], "right").usable
    bad = hand_frame_reading(f, [h], "left")
    assert bad.reason == "wrong hand: use your LEFT hand"


def test_farthest_hand_on_expected_side_is_chosen():
    f = face()
    near = hand_at(380.0, 300.0, label="Near")
    far = hand_at(520.0, 300.0, label="Far")
    r = hand_frame_reading(f, [near, far], "left")
    assert r.usable and r.hand_label == "Far"


def test_other_side_hand_does_not_count_when_expected_present():
    f = face()
    mine = hand_at(450.0, 300.0)
    other = hand_at(200.0, 300.0)
    assert hand_frame_reading(f, [other, mine], "left").usable


def test_no_hand_and_no_face_reasons():
    assert hand_frame_reading(face(), [], "left").reason == "no hand on your LEFT side"
    assert hand_frame_reading(face(found=False), [hand_at(450, 300)], "left").reason == "face not found"
    tiny = face(eye_l=(330.0, 200.0), eye_r=(310.0, 200.0))
    assert not hand_frame_reading(tiny, [hand_at(450, 300)], "left").usable
    assert not hand_frame_reading(tiny, [hand_at(450, 300)], "left").face_ok


def test_height_lower_is_positive_unit_is_eye_distance():
    f = face()  # eye distance 100, eye line at y=200
    low = hand_frame_reading(f, [hand_at(450.0, 250.0)], "left")
    high = hand_frame_reading(f, [hand_at(450.0, 150.0)], "left")
    assert low.height == pytest.approx(0.5, abs=1e-6)
    assert high.height == pytest.approx(-0.5, abs=1e-6)


def test_height_with_tilted_eye_line_is_perpendicular_to_the_line():
    # Eye line tilted 45 degrees (her left eye lower in the image).
    f = face(eye_l=(400.0, 300.0), eye_r=(300.0, 200.0))
    unit = math.hypot(100.0, 100.0)
    # Point exactly on the line extended: height 0.
    on_line = hand_frame_reading(f, [hand_at(450.0, 350.0)], "left")
    assert on_line.height == pytest.approx(0.0, abs=1e-6)
    # Move it straight down the image by 100 px: perpendicular part = 100 * cos(45) / unit.
    lower = hand_frame_reading(f, [hand_at(450.0, 450.0)], "left")
    assert lower.height == pytest.approx(100.0 * math.cos(math.pi / 4) / unit, abs=1e-6)
    assert lower.height > 0


def test_palm_width_ratio_shrinks_when_turned():
    wide = hand_at(450, 300, width=40.0)
    narrow = hand_at(450, 300, width=20.0)
    assert palm_width_ratio(narrow.points) < palm_width_ratio(wide.points)


# ---- clips -------------------------------------------------------------------------

def reading(ts, height=0.5, ratio=0.6, usable=True, face_ok=True, wrong=False):
    return HandFrameReading(usable=usable, face_ok=face_ok, wrong_side=wrong, height=height if usable else None,
                            palm_width_ratio=ratio if usable else None, timestamp_ms=ts,
                            reason=None if usable else "no hand on your LEFT side")


def series(start_ms, end_ms, step, fn):
    return [fn(t) for t in range(int(start_ms), int(end_ms) + 1, step)]


def test_steady_hand_has_zero_sink_and_turn():
    fr = series(1000, 6000, 100, lambda t: reading(t))
    clip = hand_clip_reading(fr, 1000, 6000)
    assert clip.quality == "usable"
    assert clip.hand_sink == pytest.approx(0.0)
    assert clip.palm_turn == pytest.approx(0.0)
    assert clip.hand_height == pytest.approx(0.5)
    assert clip.hand_shake == pytest.approx(0.0, abs=1e-9)
    assert not clip.dropped


def test_sink_uses_start_and_end_windows():
    # Height 0.5 in the first second, 0.8 in the last second.
    def fn(t):
        return reading(t, height=0.5 if t < 2000 else (0.8 if t > 5000 else 0.6))
    clip = hand_clip_reading(series(1000, 6000, 100, fn), 1000, 6000)
    assert clip.hand_sink == pytest.approx(0.3)
    assert clip.hand_height == pytest.approx(0.5)


def test_palm_turn_positive_when_palm_narrows():
    def fn(t):
        return reading(t, ratio=0.6 if t < 2000 else (0.3 if t > 5000 else 0.45))
    clip = hand_clip_reading(series(1000, 6000, 100, fn), 1000, 6000)
    assert clip.palm_turn == pytest.approx(0.5)


def test_shake_ignores_a_straight_drift():
    def fn(t):
        return reading(t, height=0.5 + 0.0001 * (t - 1000))
    clip = hand_clip_reading(series(1000, 6000, 100, fn), 1000, 6000)
    assert clip.hand_shake == pytest.approx(0.0, abs=1e-9)
    assert clip.hand_sink > 0.0


def test_frames_outside_the_hold_are_ignored():
    fr = [reading(t, height=5.0) for t in range(0, 900, 100)]  # before the hold: wild values
    fr += series(1000, 6000, 100, lambda t: reading(t))
    clip = hand_clip_reading(fr, 1000, 6000)
    assert clip.hand_height == pytest.approx(0.5)


def test_dropped_hand_counts_as_big_sink_with_note():
    def fn(t):
        if t < 3500:
            return reading(t)
        return reading(t, usable=False)
    clip = hand_clip_reading(series(1000, 6000, 100, fn), 1000, 6000)
    assert clip.quality == "usable"
    assert clip.dropped
    assert clip.hand_sink == config.DROP_SINK_VALUE
    assert "hand dropped out of view" in clip.notes


def test_dropped_hand_unusable_when_drop_does_not_count(monkeypatch):
    monkeypatch.setattr(config, "DROP_COUNTS_AS_SINK", False)
    fr = series(1000, 6000, 100, lambda t: reading(t) if t < 3500 else reading(t, usable=False))
    clip = hand_clip_reading(fr, 1000, 6000)
    assert clip.quality == "unusable"


def test_never_seen_is_unusable_with_plain_reason():
    fr = series(1000, 6000, 100, lambda t: reading(t, usable=False))
    clip = hand_clip_reading(fr, 1000, 6000)
    assert clip.quality == "unusable"
    assert clip.reasons == ["hand not in the picture: hold it up next to your face"]
    assert clip.hand_sink is None


def test_too_few_usable_frames_is_unusable():
    fr = [reading(1000 + 100 * i) for i in range(10)]  # 10 < 15
    clip = hand_clip_reading(fr, 1000, 6000)
    assert clip.quality == "unusable"
    assert any("too few usable frames" in r for r in clip.reasons)


def test_wrong_hand_only_gives_wrong_hand_problem():
    fr = series(1000, 6000, 100, lambda t: reading(t, usable=False, wrong=True))
    clip = hand_clip_reading(fr, 1000, 6000)
    assert clip.quality == "unusable"
    assert any("most common problem" in r for r in clip.reasons)


def test_thin_start_window_is_unusable():
    # Plenty of frames overall, but the start window (first second) has only 2 usable ones.
    fr = [reading(1000), reading(1100)] + [reading(t, usable=False) for t in range(1200, 2000, 100)]
    fr += series(2000, 6000, 100, lambda t: reading(t))
    clip = hand_clip_reading(fr, 1000, 6000)
    assert clip.quality == "unusable"
    assert any("start" in r for r in clip.reasons)
