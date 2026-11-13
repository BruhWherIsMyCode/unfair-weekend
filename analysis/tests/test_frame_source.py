"""The camera blocked / lost detector, with numpy arrays and plain numbers for time."""
from __future__ import annotations

import numpy as np

from analysis.pc.frame_source import CameraWatch, frame_is_blocked

RNG = np.random.default_rng(0)


def picture():
    return RNG.integers(40, 220, size=(48, 64, 3), dtype=np.uint8)


def test_blocked_frames():
    assert frame_is_blocked(np.zeros((48, 64, 3), dtype=np.uint8))  # covered lens: dark
    assert frame_is_blocked(np.full((48, 64, 3), 200, dtype=np.uint8))  # uniform
    assert not frame_is_blocked(picture())
    assert not frame_is_blocked(None)


def test_unavailable_until_the_first_good_frame_then_available_at_once():
    w = CameraWatch()
    assert w.state == "unavailable"
    assert w.observe(0.0, True, picture()) == ("available", None)


def test_blocked_for_one_second_means_camera_blocked_and_a_good_frame_recovers():
    w = CameraWatch()
    w.observe(0.0, True, picture())
    dark = np.zeros((48, 64, 3), dtype=np.uint8)
    assert w.observe(1.0, True, dark)[0] == "available"
    assert w.observe(1.9, True, dark)[0] == "available"
    assert w.observe(2.0, True, dark) == ("unavailable", "camera_blocked")
    assert w.observe(2.5, True, picture()) == ("available", None)


def test_a_short_dark_moment_does_not_count():
    w = CameraWatch()
    w.observe(0.0, True, picture())
    w.observe(1.0, True, np.zeros((48, 64, 3), dtype=np.uint8))
    w.observe(1.5, True, picture())
    assert w.observe(2.2, True, np.zeros((48, 64, 3), dtype=np.uint8))[0] == "available"  # the clock restarted


def test_read_failures_for_one_second_mean_camera_lost():
    w = CameraWatch()
    w.observe(0.0, True, picture())
    assert w.observe(1.0, False, None)[0] == "available"
    assert w.observe(1.9, False, None)[0] == "available"
    assert w.observe(2.0, False, None) == ("unavailable", "camera_lost")
    assert w.observe(2.1, True, picture()) == ("available", None)
