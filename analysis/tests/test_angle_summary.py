"""Tests for the angle-test summary. Made-up trials. Written, not run yet."""
from __future__ import annotations

from analysis import config
from analysis.angle_summary import format_lines, summarise, verdict_for


def trial(label, pose, quality="usable", yaw=None, pitch=None, **readings):
    t = {"label": label, "pose": pose, "quality": quality, "yaw_deg": yaw, "pitch_deg": pitch}
    for m in config.METRICS:
        t[m] = readings.get(m, 0.0)
    return t


def trials(low=0.0, left=0.0, right=0.0, acted_left=0.10, acted_right=-0.10, metric="corner_height"):
    """Straight normal at 0.0; the poses shifted by the given amounts; droops as given."""
    out = []
    for _ in range(3):
        out.append(trial("normal", "straight", yaw=0.0, pitch=0.0, **{metric: 0.0}))
        out.append(trial("normal", "low", yaw=0.0, pitch=-15.0, **{metric: low}))
        out.append(trial("normal", "left", yaw=-20.0, pitch=0.0, **{metric: left}))
        out.append(trial("normal", "right", yaw=20.0, pitch=0.0, **{metric: right}))
        out.append(trial("acted_left", "straight", **{metric: acted_left}))
        out.append(trial("acted_right", "straight", **{metric: acted_right}))
    return out


def test_ratio_and_verdict_ok():
    # biggest angle shift 0.02 (left), droop size 0.10 -> ratio 0.2
    m = summarise(trials(low=0.01, left=0.02, right=-0.015))["metrics"]["corner_height"]
    assert abs(m["angle_shift"] - 0.02) < 1e-9
    assert abs(m["droop_size"] - 0.10) < 1e-9
    assert abs(m["ratio"] - 0.2) < 1e-9
    assert m["verdict"] == "angle OK"


def test_droop_size_is_the_smaller_of_the_two_sides():
    m = summarise(trials(left=0.02, acted_left=0.10, acted_right=-0.04))["metrics"]["corner_height"]
    assert abs(m["droop_size"] - 0.04) < 1e-9
    assert abs(m["ratio"] - 0.5) < 1e-9
    assert m["verdict"] == "angle RISKY"  # exactly 0.5 is in the 0.5-1.0 band


def test_verdict_bands():
    assert verdict_for(0.49) == "angle OK"
    assert verdict_for(0.5) == "angle RISKY"
    assert verdict_for(1.0) == "angle RISKY"
    assert verdict_for(1.01) == "angle BREAKS IT"
    assert verdict_for(None) == "no data"


def test_angle_breaks_it():
    m = summarise(trials(right=-0.15))["metrics"]["corner_height"]  # shift 0.15 > droop 0.10
    assert abs(m["ratio"] - 1.5) < 1e-9
    assert m["verdict"] == "angle BREAKS IT"


def test_unusable_trials_are_ignored():
    t = trials(left=0.02) + [trial("normal", "left", quality="unusable", corner_height=5.0)]
    m = summarise(t)["metrics"]["corner_height"]
    assert abs(m["angle_shift"] - 0.02) < 1e-9


def test_missing_data_gives_no_verdict_and_never_crashes():
    s = summarise([])
    assert all(m["verdict"] == "no data" for m in s["metrics"].values())
    assert s["best_metric"] is None
    assert format_lines(s)[-1] == "Best against angle: not enough data."
    # no acted trials at all
    only_normal = [t for t in trials() if t["label"] == "normal"]
    assert summarise(only_normal)["metrics"]["corner_height"]["ratio"] is None


def test_pose_medians_of_yaw_and_pitch():
    poses = summarise(trials())["poses"]
    assert poses["left"]["yaw"] == -20.0
    assert poses["right"]["yaw"] == 20.0
    assert poses["low"]["pitch"] == -15.0
    assert poses["straight"]["count"] == 9  # 3 normal + 3 acted left + 3 acted right


def test_best_reading_line_in_plain_words():
    t = []
    for tr in trials(left=0.02, metric="corner_height_3d"):
        t.append(tr)
    # the other readings: a crooked phone moves corner_height as much as the droop
    for tr in t:
        if tr["label"] == "normal" and tr["pose"] == "left":
            tr["corner_height"] = 0.10
        if tr["label"] == "acted_left":
            tr["corner_height"] = 0.10
        if tr["label"] == "acted_right":
            tr["corner_height"] = -0.10
    s = summarise(t)
    assert s["best_metric"] == "corner_height_3d"
    lines = format_lines(s)
    assert lines[-1] == ("Best against angle: corner_height_3d "
                         "(a crooked phone moves it 20% as much as a droop).")
    assert any(ln.startswith("corner_height: angle BREAKS IT") or ln.startswith("corner_height: angle RISKY")
               for ln in lines)
    assert any(ln.startswith("pose left: head turn -20 deg") for ln in lines)
