"""Tests for the angle plan and the "I saw" result card. Fake clock, no camera. Written, not run yet."""
from analysis import config
from analysis.guide import (
    ACTED_LEFT_TEXT, ACTED_RIGHT_TEXT, RESULT, Guide, Signal, angle_plan, face_plan)
from analysis.hand_metric import seen_text_hand

STEP = 0.1


def test_angle_plan_order_poses_and_texts():
    plan = angle_plan(3)
    assert len(plan) == 18
    assert [(t.label, t.pose) for t in plan[::3]] == [
        ("normal", "straight"), ("normal", "low"), ("normal", "left"), ("normal", "right"),
        ("acted_left", "straight"), ("acted_right", "straight")]
    assert plan[0].instruction == "Look straight at the camera. Give a normal smile when it says SMILE."
    assert plan[3].instruction == ("Lift your chin a little, as if the phone were held low. "
                                   "Normal smile.")
    assert plan[6].instruction == ("Turn your head a little to your LEFT, keep your eyes on the screen. "
                                   "Normal smile.")
    assert plan[9].instruction == ("Turn your head a little to your RIGHT, keep your eyes on the screen. "
                                   "Normal smile.")
    assert plan[12].instruction == ACTED_LEFT_TEXT == face_plan(1)[1].instruction
    assert plan[15].instruction == ACTED_RIGHT_TEXT == face_plan(1)[2].instruction
    assert all(t.test == "face" for t in plan)


def test_angle_plan_has_one_intro_block_per_pose():
    plan = angle_plan(3)
    blocks = []
    for t in plan:
        if not blocks or blocks[-1] != t.block:
            blocks.append(t.block)
    assert len(blocks) == 6
    assert all(b.startswith("Part %d of 6" % (i + 1)) for i, b in enumerate(blocks))


def test_angle_plan_default_count_comes_from_config():
    assert len(angle_plan()) == 6 * config.GUIDE_ANGLE_PER_POSE
    assert len(angle_plan(1)) == 6


def test_face_plan_trials_have_no_pose():
    assert all(t.pose == "" for t in face_plan(2))


def _to_result(seen_text, warning, usable=True):
    g = Guide(face_plan(1), "face")
    t = 100.0
    for _ in range(int((config.GUIDE_START_MIN_S + 2.0) / STEP)):
        g.update(t, Signal(ready=True))
        t += STEP
    view = g.update(t, Signal(ready=True))
    for _ in range(int(60.0 / STEP)):  # intro, wait, then the measured phases
        t += STEP
        view = g.update(t, Signal(ready=True))
        if view.finished_measuring:
            break
    assert view.finished_measuring
    g.trial_finished(usable, "" if usable else "no clear smile", seen_text=seen_text, warning=warning)
    return g.update(t + STEP, Signal(ready=True))


def test_result_card_shows_what_was_seen_and_the_warning():
    view = _to_result("I saw: level",
                      "That did not look like a LEFT droop. Saved as you did it, not as asked.")
    assert view.phase == RESULT
    assert view.instruction == "Got it."
    assert view.detail == "I saw: level"
    assert view.warning.startswith("That did not look like a LEFT droop")


def test_result_card_without_seen_text_still_works():
    view = _to_result("", "")
    assert view.instruction == "Got it."
    assert view.detail == "" and view.warning == ""


def test_a_mismatch_warning_does_not_repeat_the_trial():
    view = _to_result("I saw: level",
                      "That did not look like a LEFT droop. Saved as you did it, not as asked.")
    assert "Once more" not in view.instruction


def test_hand_seen_texts():
    assert seen_text_hand(0.01, 0.0) == "I saw: hand steady"
    assert seen_text_hand(0.15, 0.0) == "I saw: hand sank 0.15"
    assert seen_text_hand(0.15, 0.18) == "I saw: hand sank 0.15, palm turned in 0.18"
    assert seen_text_hand(0.01, 0.18) == "I saw: hand steady, palm turned in 0.18"
    assert seen_text_hand(1.0, None, dropped=True) == "I saw: hand dropped out of view"
    assert seen_text_hand(0.15, 0.05) == "I saw: hand sank 0.15"
