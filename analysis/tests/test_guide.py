"""Tests for the guided step machine. Fake clock, no camera, no cv2. Written, not run yet."""
from analysis import config
from analysis.guide import (
    DONE, INTRO, LOWER, MEASURE, RESULT, START, WAIT, Guide, Signal, face_plan, hand_plan,
    plain_reason, verdict_lines)

STEP = 0.1


class Clock:
    def __init__(self, guide):
        self.g = guide
        self.t = 100.0
        self.view = None

    def tick(self, seconds, **sig):
        """Advance `seconds` in STEP frames with a constant signal; return the last view."""
        sig.setdefault("ready", False)
        n = int(round(seconds / STEP))
        for _ in range(max(1, n)):
            self.view = self.g.update(self.t, Signal(**sig))
            self.t += STEP
        return self.view

    def until(self, phase, limit=60.0, **sig):
        steps = int(limit / STEP)
        for _ in range(steps):
            sig.setdefault("ready", False)
            self.view = self.g.update(self.t, Signal(**sig))
            self.t += STEP
            if self.view.phase == phase:
                return self.view
        raise AssertionError("never reached %s, stuck in %s" % (phase, self.view.phase))


def start_face(per_label=2):
    c = Clock(Guide(face_plan(per_label), "face"))
    c.tick(config.GUIDE_START_MIN_S + 2.0, ready=True)  # start card, then first intro
    assert c.view.phase == INTRO
    return c


def measure_one(c):
    """From an intro or wait phase, get ready and run the measured phases to the end."""
    c.tick(config.GUIDE_INTRO_S + 0.2, ready=True) if c.view.phase == INTRO else None
    c.tick(config.GUIDE_READY_S + 0.3, ready=True)
    assert c.g.state == MEASURE
    for _ in range(int((c.g.settle_s + c.g.hold_s) / STEP) + 5):
        c.view = c.g.update(c.t, Signal(ready=True))
        c.t += STEP
        if c.view.finished_measuring:
            return c.view
    raise AssertionError("measuring never finished")


# ---- plans ----------------------------------------------------------------------------

def test_face_plan_order_and_texts():
    plan = face_plan(5)
    assert len(plan) == 15
    assert [t.label for t in plan] == ["normal"] * 5 + ["acted_left"] * 5 + ["acted_right"] * 5
    assert plan[0].instruction.startswith("Look at the camera. When it says SMILE")
    assert "LEFT mouth corner down" in plan[5].instruction
    assert "RIGHT mouth corner down" in plan[10].instruction
    assert plan[5].block == "Part 2 of 3: droop on your LEFT side"


def test_hand_plan_order_and_texts():
    plan = hand_plan(5)
    assert len(plan) == 20
    assert [(t.side, t.label) for t in plan[::5]] == [
        ("left", "normal"), ("left", "weak"), ("right", "normal"), ("right", "weak")]
    assert plan[0].instruction.startswith("Hold your LEFT hand up next to your face, palm to the camera")
    assert "ACT WEAK" in plan[5].instruction
    assert plan[10].block == "Part 3 of 4: RIGHT hand, normal"


def test_plan_counts_follow_arguments():
    assert len(face_plan(3)) == 9
    assert len(hand_plan(3)) == 12


# ---- start card, intro, waiting -------------------------------------------------------------

def test_start_card_needs_three_seconds_and_a_seen_face():
    c = Clock(Guide(face_plan(1), "face"))
    v = c.tick(config.GUIDE_START_MIN_S - 0.5, ready=True)
    assert v.phase == START
    assert v.instruction.startswith("This test runs by itself")
    v = c.tick(1.0, ready=True)
    assert v.phase == INTRO  # 3 s have passed and the face was seen for more than 1 s


def test_start_card_stays_while_no_face():
    c = Clock(Guide(face_plan(1), "face"))
    v = c.tick(config.GUIDE_START_MIN_S + 5.0, ready=False)
    assert v.phase == START


def test_intro_card_then_wait_ready():
    c = start_face()
    assert c.view.instruction == "Part 1 of 3: normal smiles"
    v = c.tick(config.GUIDE_INTRO_S + 0.3, ready=False)
    assert v.phase == WAIT
    assert v.instruction.startswith("Look at the camera")


def test_wait_ready_needs_continuous_ready():
    c = start_face()
    c.until(WAIT)
    c.tick(config.GUIDE_READY_S - 0.3, ready=True)
    c.tick(0.1, ready=False)  # a gap resets the timer
    c.tick(config.GUIDE_READY_S - 0.3, ready=True)
    assert c.g.state == WAIT
    c.tick(0.6, ready=True)
    assert c.g.state == MEASURE


def test_hint_after_waiting_face():
    c = start_face()
    c.until(WAIT)
    v = c.tick(config.GUIDE_HINT_AFTER_S - 1.0, ready=False)
    assert "can't see your face" not in v.detail
    v = c.tick(1.5, ready=False)
    assert v.detail.startswith("I can't see your face clearly")


def test_hint_hand_and_wrong_side():
    c = hand_ready_clock()
    v = c.tick(config.GUIDE_HINT_AFTER_S + 0.5, ready=False)
    assert v.detail.startswith("I can't see your LEFT hand")

    c2 = hand_ready_clock()
    v =c2.tick(config.GUIDE_HINT_AFTER_S + 0.5, ready=False, wrong_side=True, hand_seen_any=True)
    assert v.detail.startswith("I see your other hand")
    assert "--mirrored" in v.detail


# ---- measuring, result, retries -------------------------------------------------------------

def test_measured_phases_and_countdown():
    c = start_face()
    c.until(WAIT)
    v = c.tick(config.GUIDE_READY_S + 0.2, ready=True)
    assert v.collect and v.phase == "RELAX"
    assert v.countdown.startswith("RELAX")
    assert v.color == "green"
    v = c.tick(config.RELAX_SECONDS + 0.2, ready=True)
    assert v.phase == "SMILE" and v.countdown.startswith("SMILE")


def test_usable_trial_shows_got_it_then_next_trial():
    c = start_face()
    measure_one(c)
    c.g.trial_finished(True, "")
    v = c.tick(0.1)
    assert v.phase == RESULT and v.instruction == "Got it."
    assert v.color == "white"
    v = c.tick(config.GUIDE_RESULT_S + 0.3)
    assert v.phase == WAIT
    assert v.progress == "Trial 2 of 6"


def test_unusable_trial_repeats_then_skips():
    c = start_face(per_label=1)
    measure_one(c)
    for attempt in range(config.GUIDE_MAX_RETRIES):
        c.g.trial_finished(False, "no clear smile (smile strength rose 0.10, need 0.25)")
        v = c.tick(0.1)
        assert v.instruction.startswith("That one did not count: I did not see a clear smile")
        assert v.instruction.endswith("Once more.")
        c.tick(config.GUIDE_RESULT_S + 0.3)
        assert c.g.idx == 0  # same trial again
        assert c.view.progress == "Trial 1 of 3"
        measure_one(c)
    c.g.trial_finished(False, "no clear smile")
    v = c.tick(0.1)
    assert "Skipping it" in v.instruction
    assert c.g.notes and "Trial 1 skipped" in c.g.notes[0]
    c.tick(config.GUIDE_RESULT_S + 0.3)
    assert c.g.idx == 1
    # the repeat did not show the intro card again, and the next block's first trial waits
    assert c.view.phase in (WAIT, INTRO)


def test_trial_finished_ignored_when_not_measuring():
    c = start_face()
    c.g.trial_finished(True, "")
    assert c.g.state == INTRO


# ---- lower your hand -------------------------------------------------------------------------

def hand_ready_clock():
    """A hand guide that has just entered WAIT for its first trial."""
    c = Clock(Guide(hand_plan(1), "hand"))
    c.tick(config.GUIDE_START_MIN_S + 2.0, ready=False, face_ok=True)
    c.until(WAIT)
    return c


def finish_hand_trial(c, usable=True):
    c.tick(config.GUIDE_READY_S + 0.3, ready=True, hand_seen_any=True)
    for _ in range(int((c.g.settle_s + c.g.hold_s) / STEP) + 5):
        c.view = c.g.update(c.t, Signal(ready=True, hand_seen_any=True))
        c.t += STEP
        if c.view.finished_measuring:
            break
    assert c.view.finished_measuring
    c.g.trial_finished(usable, "" if usable else "hand not in the picture")
    c.tick(config.GUIDE_RESULT_S + 0.3, hand_seen_any=True)


def test_lower_waits_for_the_hand_to_disappear():
    c = hand_ready_clock()
    finish_hand_trial(c)
    assert c.g.state == LOWER
    assert c.view.instruction == "Lower your hand"
    c.tick(1.0, hand_seen_any=True)
    assert c.g.state == LOWER
    c.tick(config.GUIDE_LOWER_NO_HAND_S + 0.2, hand_seen_any=False)
    assert c.g.state != LOWER


def test_lower_ends_after_three_seconds():
    c = hand_ready_clock()
    finish_hand_trial(c)
    c.tick(config.GUIDE_LOWER_MAX_S - 0.5, hand_seen_any=True)
    assert c.g.state == LOWER
    c.tick(1.0, hand_seen_any=True)
    assert c.g.state != LOWER


def test_no_lower_after_the_last_trial_and_done():
    c = Clock(Guide(hand_plan(1)[:1], "hand"))
    c.tick(config.GUIDE_START_MIN_S + 2.0, ready=False, face_ok=True)
    c.until(WAIT)
    finish_hand_trial(c)
    assert c.g.state == DONE
    assert c.view.done and c.view.instruction == "Finished. Results below."


# ---- done and progress -------------------------------------------------------------------------

def test_done_after_last_face_trial_with_summary_lines():
    c = start_face(per_label=1)
    steps = 0
    while c.g.state != DONE and steps < 40:
        steps += 1
        if c.g.state == MEASURE:
            for _ in range(int((c.g.settle_s + c.g.hold_s) / STEP) + 5):
                c.view = c.g.update(c.t, Signal(ready=True))
                c.t += STEP
                if c.view.finished_measuring:
                    break
            c.g.trial_finished(True, "")
        else:
            c.tick(1.0, ready=True)
    assert c.g.state == DONE
    c.g.summary_lines = ("line one", "line two")
    v = c.tick(0.1)
    assert v.done and v.summary_lines == ("line one", "line two")
    assert v.progress == "Done"


def test_progress_numbers_across_blocks():
    c = start_face(per_label=2)
    seen = []
    for _ in range(6):
        if c.g.state != MEASURE:
            c.tick(config.GUIDE_INTRO_S + 0.3, ready=True) if c.g.state == INTRO else None
            c.tick(config.GUIDE_READY_S + 0.3, ready=True)
        seen.append(c.view.progress)
        for _ in range(int((c.g.settle_s + c.g.hold_s) / STEP) + 5):
            c.view = c.g.update(c.t, Signal(ready=True))
            c.t += STEP
            if c.view.finished_measuring:
                break
        c.g.trial_finished(True, "")
        c.tick(config.GUIDE_RESULT_S + 0.3, ready=True)
    assert seen == ["Trial %d of 6" % n for n in range(1, 7)]


def test_empty_plan_goes_straight_to_done():
    c = Clock(Guide([], "face"))
    v = c.tick(config.GUIDE_START_MIN_S + 2.0, ready=True)
    assert v.done


# ---- helpers ---------------------------------------------------------------------------------------

def test_plain_reason_maps_known_phrases():
    assert plain_reason("too few usable frames (3 of 90, need 8); most common problem: x") == \
        "I could not see you clearly enough"
    assert plain_reason("") == "not enough clear frames"
    assert plain_reason("something odd (12)") == "something odd"


def test_verdict_lines_face_and_hand():
    face_text = "\n".join([
        "== corner_height (positive = her LEFT lower/weaker) ==",
        "label            n   median",
        "acted_left   separates (0 of 5 trials not beyond normal on the correct side)",
        "acted_right  overlaps",
        "",
        "best metric: corner_height (smallest acted-vs-normal gap = 2.00 x normal spread)"])
    lines = verdict_lines(face_text, "face")
    assert lines[0].startswith("corner_height: acted_left separates")
    assert lines[1].startswith("corner_height: acted_right")
    assert lines[2].startswith("best metric")
    hand_text = "== her LEFT hand (larger = more of a problem) ==\nhand_sink x\nher LEFT hand: not enough data\n"
    assert verdict_lines(hand_text, "hand") == ["her LEFT hand: not enough data"]
