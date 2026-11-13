from __future__ import annotations

from analysis.baseline import make_baseline
from analysis.contract_shape import tool_result
from analysis.face_metric import ClipReading


def usable_clip(ch=0.1):
    return ClipReading(quality="usable", corner_height=ch, corner_lift=0.05, smile_score=0.2,
                       frames_total=30, frames_usable=28, peak_strength=0.8, relaxed_strength=0.1)


def test_shape_and_keys_when_ready():
    base = make_baseline([0.0] * 6, min_half_width=0.02)
    r = tool_result("smile", 1, usable_clip(0.1), "corner_height", base)
    assert r["task"] == "smile"
    assert r["attempt"] == 1
    assert r["quality"] == "usable"
    assert set(r["reading"]) == {"sign", "value", "usual_low", "usual_high", "outside", "side", "outside_by"}
    assert r["reading"]["sign"] == "face"
    assert r["reading"]["outside"] is True
    assert r["reading"]["side"] == "left"
    assert set(r["baseline"]) == {"sessions", "needed", "ready"}
    assert r["baseline"]["ready"] is True
    assert set(r["all_readings"]) == {"corner_height", "corner_lift", "smile_score", "corner_height_3d"}


def test_learning_baseline_never_outside():
    base = make_baseline([0.0, 0.0])
    r = tool_result("smile", 1, usable_clip(0.9), "corner_height", base)
    assert r["reading"]["outside"] is False
    assert r["reading"]["side"] is None
    assert r["baseline"]["ready"] is False


def test_unusable_clip_has_reason_and_no_reading():
    base = make_baseline([0.0] * 6, min_half_width=0.02)
    clip = ClipReading(quality="unusable", reasons=["no clear smile"], frames_total=20, frames_usable=20)
    r = tool_result("smile", 2, clip, "corner_height", base)
    assert r["quality"] == "unusable"
    assert "no clear smile" in r["reason"]
    assert r["reading"] is None
    assert all(v is None for v in r["all_readings"].values())
