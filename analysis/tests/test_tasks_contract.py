"""Task result shape, status values and one-sided comparison. Made-up numbers only."""
import pytest

from analysis import config
from analysis.contract_shape import tool_result
from analysis.face_metric import ClipReading
from analysis.hand_metric import HandClipReading
from analysis.tasks import hand as hand_task
from analysis.tasks.common import STATUSES, add_status, status_of


def hand_clip(sink=0.0, quality="usable", notes=None):
    return HandClipReading(quality=quality, hand_sink=sink if quality == "usable" else None,
                           hand_height=0.5, palm_turn=0.0, hand_shake=0.01,
                           reasons=[] if quality == "usable" else ["hand not in the picture: hold it up next to your face"],
                           notes=notes or [])


def person(task, values, label="normal"):
    return {"sessions": [{"label": label, "values": {"%s.hand_sink" % task: v}} for v in values]}


NORMAL = [0.00, 0.01, 0.02, 0.01, 0.00, 0.02]  # six values: baseline ready (needed 5)


def test_hand_below_range_is_as_usual():
    r = hand_task.build_result("hand_left", 1, hand_clip(-0.5), person("hand_left", NORMAL))
    assert r["reading"]["outside"] is False
    assert r["reading"]["side"] is None
    assert r["reading"]["sign"] == "arm"
    assert r["status"] == "as_usual"


def test_hand_above_range_is_outside_with_the_hands_side():
    r = hand_task.build_result("hand_left", 1, hand_clip(0.6), person("hand_left", NORMAL))
    assert r["reading"]["outside"] is True
    assert r["reading"]["side"] == "left"
    assert r["reading"]["outside_by"] > 0
    assert r["status"] == "different_from_usual"
    r2 = hand_task.build_result("hand_right", 1, hand_clip(0.6), person("hand_right", NORMAL))
    assert r2["reading"]["side"] == "right"


def test_each_hand_uses_its_own_baseline():
    p = person("hand_left", NORMAL)  # nothing stored for the right hand
    r = hand_task.build_result("hand_right", 1, hand_clip(0.6), p)
    assert r["status"] == "learning"
    assert r["reading"]["outside"] is False


def test_acted_sessions_never_enter_the_baseline():
    p = person("hand_left", NORMAL)
    p["sessions"] += person("hand_left", [0.9, 0.9], label="acted")["sessions"]
    r = hand_task.build_result("hand_left", 1, hand_clip(0.6), p)
    assert r["status"] == "different_from_usual"


def test_minimum_half_width_keeps_range_from_collapsing():
    p = person("hand_left", [0.0] * 6)
    r = hand_task.build_result("hand_left", 1, hand_clip(0.04), p)
    assert r["reading"]["usual_high"] == pytest.approx(config.MIN_HALF_WIDTH["hand_sink"])
    assert r["status"] == "as_usual"


def test_unusable_has_reason_no_reading_and_status():
    r = hand_task.build_result("hand_left", 1, hand_clip(quality="unusable"), person("hand_left", NORMAL))
    assert r["quality"] == "unusable"
    assert r["reading"] is None
    assert r["reason"]
    assert r["status"] == "unusable"


def test_notes_are_passed_on():
    r = hand_task.build_result("hand_left", 1, hand_clip(1.0, notes=["hand dropped out of view"]),
                               person("hand_left", NORMAL))
    assert r["notes"] == ["hand dropped out of view"]


def test_status_values():
    assert set(STATUSES) == {"as_usual", "different_from_usual", "unusable", "learning"}
    assert status_of({"quality": "unusable", "reading": None}) == "unusable"
    assert status_of({"quality": "usable", "reading": {"outside": False}, "baseline": {"ready": False}}) == "learning"
    assert status_of({"quality": "usable", "reading": {"outside": True}, "baseline": {"ready": True}}) == "different_from_usual"
    assert status_of({"quality": "usable", "reading": {"outside": False}, "baseline": {"ready": True}}) == "as_usual"
    assert "stroke" not in " ".join(STATUSES)


def test_stored_values_are_per_task():
    v = hand_task.stored_values("hand_right", hand_clip(0.1))
    assert set(v) == {"hand_right.hand_sink", "hand_right.hand_height",
                      "hand_right.palm_turn", "hand_right.hand_shake"}


def smile_clip():
    return ClipReading(quality="usable", corner_height=0.2, corner_lift=0.1, smile_score=0.0,
                       frames_total=30, frames_usable=30)


def test_smile_result_shape_is_unchanged():
    base = {"ready": True, "sessions": 6, "needed": 5, "usual_low": -0.02, "usual_high": 0.03}
    r = tool_result("smile", 1, smile_clip(), "corner_height", base)
    assert set(r) == {"task", "attempt", "quality", "reading", "baseline", "all_readings"}
    assert r["reading"]["sign"] == "face"
    # two-sided: above the range is her left, below is her right
    assert r["reading"]["outside"] and r["reading"]["side"] == "left"
    below = ClipReading(quality="usable", corner_height=-0.3, corner_lift=0.0, smile_score=0.0)
    r2 = tool_result("smile", 1, below, "corner_height", base)
    assert r2["reading"]["outside"] and r2["reading"]["side"] == "right"
    # adding a status leaves everything else alone
    assert add_status(r)["status"] == "different_from_usual"
