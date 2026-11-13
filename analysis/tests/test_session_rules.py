"""Pure rules of the call session: interrupt decisions, budget, confirmation, status, baseline eligibility.

Written, not run.
"""
from __future__ import annotations

from analysis.session import limits, rules
from analysis.session.rules import FinalRecord, ResourceInfo, WatchView


def reading(value, low=-0.02, high=0.03, outside=None, side=None, metric="m_v1"):
    if outside is None:
        outside = value > high or value < low
    return {"metric_id": metric, "unit": "ratio", "value": value, "usual_low": low, "usual_high": high,
            "outside": outside, "side": side}


def rec(value=0.01, usable=True, outcome="completed", snapshot="b1", ready=True, task="smile", attempt="A1",
        metric="m_v1", side=None):
    r = reading(value, metric=metric, side=side) if usable else None
    return FinalRecord(task, attempt, outcome, usable, r, snapshot, ready)


def view(**kw):
    base = dict(state="capturing", task="smile", now=0.0, created_at=0.0, capture_command_at=0.0,
                capture_started_at=0.0, first_sample_at=None, last_sample_at=None, samples_received=0,
                last_analyzer_at=0.0, framing_bad_since=None, framing_bad_event_id=None,
                unperformable_event_id=None, input_closed=False, window_s=5.0)
    base.update(kw)
    return WatchView(**base)


AVAILABLE = {"camera": ResourceInfo("available"), "microphone": ResourceInfo("available")}


# ---- interrupt decisions ---------------------------------------------------------------------

def test_camera_loss_interrupts_at_once_and_permission_denied_is_not_recoverable():
    res = {"camera": ResourceInfo("unavailable", "track_ended", "e1"), "microphone": ResourceInfo("available")}
    d = rules.decide_interrupt(view(now=0.1), res)
    assert d.code == "camera_unavailable" and d.source_event_id == "e1" and d.recoverable is True
    res["camera"] = ResourceInfo("unavailable", "permission_denied", "e2")
    assert rules.decide_interrupt(view(now=0.1), res).recoverable is False


def test_loss_of_a_resource_the_task_does_not_need_does_not_interrupt():
    res = {"camera": ResourceInfo("available"), "microphone": ResourceInfo("unavailable", "track_ended", "e1")}
    assert rules.decide_interrupt(view(now=0.1, samples_received=1, last_sample_at=0.1), res) is None


def test_framing_unusable_needs_two_continuous_seconds():
    v = view(framing_bad_since=0.0, framing_bad_event_id="obs_1", samples_received=3, last_sample_at=1.4,
             last_analyzer_at=0.0, now=1.5)
    assert rules.decide_interrupt(v, AVAILABLE) is None
    v = view(framing_bad_since=0.0, framing_bad_event_id="obs_1", samples_received=3, last_sample_at=1.9,
             last_analyzer_at=0.0, now=2.0)
    d = rules.decide_interrupt(v, AVAILABLE)
    assert d.code == "framing_unusable" and d.source_event_id == "obs_1"


def test_no_first_sample_within_two_seconds():
    assert rules.decide_interrupt(view(now=1.9), AVAILABLE) is None
    d = rules.decide_interrupt(view(now=2.0), AVAILABLE)
    assert d.code == "no_samples" and d.source_event_id is None


def test_sample_gap_of_two_seconds():
    v = view(now=2.9, samples_received=4, first_sample_at=0.1, last_sample_at=1.0, last_analyzer_at=2.8)
    assert rules.decide_interrupt(v, AVAILABLE) is None
    v.now = 3.0
    assert rules.decide_interrupt(v, AVAILABLE).code == "no_samples"


def test_no_acknowledgment_and_no_sample_within_five_seconds():
    v = view(state="preparing", capture_started_at=None, capture_command_at=0.0, now=4.9)
    assert rules.decide_interrupt(v, AVAILABLE) is None
    v.now = 5.0
    assert rules.decide_interrupt(v, AVAILABLE).code == "no_samples"


def test_analysis_stalled_only_while_samples_arrive():
    arriving = view(now=3.0, samples_received=6, first_sample_at=0.0, last_sample_at=2.9, last_analyzer_at=0.0)
    assert rules.decide_interrupt(arriving, AVAILABLE).code == "analysis_stalled"
    fresh = view(now=2.9, samples_received=6, first_sample_at=0.0, last_sample_at=2.8, last_analyzer_at=0.0)
    assert rules.decide_interrupt(fresh, AVAILABLE) is None


def test_task_unperformable_signal_interrupts():
    v = view(now=0.5, samples_received=1, last_sample_at=0.4, unperformable_event_id="obs_9")
    d = rules.decide_interrupt(v, AVAILABLE)
    assert d.code == "task_unperformable" and d.source_event_id == "obs_9"


def test_preparation_limit_and_finalize_deadline():
    prep = view(state="preparing", capture_command_at=None, capture_started_at=None, now=29.9)
    assert rules.decide_interrupt(prep, AVAILABLE) is None
    prep.now = 30.0
    assert rules.decide_interrupt(prep, AVAILABLE).code == "capture_failed"
    late = view(state="finalizing", input_closed=True, now=5.0 + 20.0, samples_received=5, last_sample_at=4.9)
    assert rules.decide_interrupt(late, AVAILABLE).code == "analysis_stalled"


def test_no_interrupt_after_input_closed_even_without_new_samples():
    v = view(state="finalizing", input_closed=True, now=8.0, samples_received=25, first_sample_at=0.0,
             last_sample_at=5.0, last_analyzer_at=5.0)
    assert rules.decide_interrupt(v, AVAILABLE) is None


# ---- budget ------------------------------------------------------------------------------------

def test_a_not_measured_attempt_is_repeated_once_and_the_second_skips_the_task():
    """v11: one repeat for a not-measured attempt (was: three technical retries)."""
    assert rules.attempt_budget([]) is None
    one = [rec(usable=False, outcome="interrupted")]
    assert rules.attempt_budget(one) is None and rules.attempts_left(one) == 1
    two = [rec(usable=False, outcome="interrupted"), rec(usable=False, outcome="failed")]
    assert rules.attempt_budget(two) == "attempt_budget_exhausted" and rules.attempts_left(two) == 0
    for outcome in ("could_not_perform", "completed"):  # unusable and could_not_perform count the same way
        assert rules.attempt_budget([rec(usable=False, outcome=outcome)] * 2) == "attempt_budget_exhausted"


def test_the_confirmation_repeat_after_a_usable_reading_is_unchanged_and_three_is_the_cap():
    assert rules.attempt_budget([rec(0.2)]) is None and rules.attempts_left([rec(0.2)]) == 2
    mixed = [rec(0.2), rec(usable=False, outcome="interrupted")]  # usable + one not measured: one more allowed
    assert rules.attempt_budget(mixed) is None and rules.attempts_left(mixed) == 1
    assert rules.attempt_budget(mixed + [rec(0.2)]) == "attempt_budget_exhausted"  # three in total is the cap
    assert rules.attempt_budget(mixed + [rec(usable=False, outcome="failed")]) == "attempt_budget_exhausted"
    assert rules.attempt_budget([rec(0.2), rec(0.2)]) == "usable_readings_complete"


def test_budget_stops_after_two_usable_readings():
    assert rules.attempt_budget([rec(), rec()]) == "usable_readings_complete"
    assert limits.MAX_ATTEMPTS_PER_TASK == 3 and limits.MAX_USABLE_PER_TASK == 2


# ---- confirmation --------------------------------------------------------------------------------

def test_two_outside_readings_on_the_same_side_confirm():
    ev = rules.evaluate_sign([rec(0.11, attempt="A1", side="left"), rec(0.09, attempt="A2", side="left")])
    assert ev.confirmed is True and len(ev.attempts) == 2 and ev.side == "left"


def test_outside_then_within_is_retained_not_confirmed():
    ev = rules.evaluate_sign([rec(0.11), rec(0.01)])
    assert ev.confirmed is False and ev.latest_outside is False and len(ev.attempts) == 2


def test_opposite_directions_do_not_confirm():
    assert rules.evaluate_sign([rec(0.11), rec(-0.09)]).confirmed is False


def test_other_metric_version_or_other_frozen_baseline_does_not_confirm():
    assert rules.evaluate_sign([rec(0.11), rec(0.09, metric="m_v2")]).confirmed is False
    assert rules.evaluate_sign([rec(0.11, snapshot="b1"), rec(0.09, snapshot="b2")]).confirmed is False


def test_interrupted_or_unusable_attempts_never_count():
    records = [rec(0.11), rec(usable=False, outcome="interrupted"), rec(usable=False, outcome="completed")]
    ev = rules.evaluate_sign(records)
    assert ev.confirmed is False and len(ev.attempts) == 1


def test_a_reading_without_a_ready_baseline_does_not_confirm():
    a = rec(0.11, ready=False)
    b = rec(0.09, ready=False)
    assert rules.evaluate_sign([a, b]).confirmed is False


# ---- status ----------------------------------------------------------------------------------------

def ev(task_records):
    return rules.evaluate_sign(task_records)


def test_status_priority():
    confirmed = {"smile": ev([rec(0.11), rec(0.09)]), "arms": ev([]), "sentence": ev([])}
    assert rules.session_status(confirmed, ("smile", "arms", "sentence")) == "change_noticed"  # beats incomplete
    unfinished = {"smile": ev([rec(0.01)]), "arms": ev([rec(usable=False, outcome="interrupted")])}
    assert rules.session_status(unfinished, ("smile", "arms")) == "incomplete"
    learning = {"smile": ev([rec(0.01, ready=False)])}
    assert rules.session_status(learning, ("smile",)) == "learning"
    usual = {"smile": ev([rec(0.01)])}
    assert rules.session_status(usual, ("smile",)) == "as_usual"


def test_incomplete_beats_learning():
    evals = {"smile": ev([rec(0.01, ready=False)]), "arms": ev([])}
    assert rules.session_status(evals, ("smile", "arms")) == "incomplete"


def test_first_outside_then_within_is_as_usual_but_single_outside_is_incomplete():
    assert rules.session_status({"smile": ev([rec(0.11), rec(0.01)])}, ("smile",)) == "as_usual"
    assert rules.session_status({"smile": ev([rec(0.11)])}, ("smile",)) == "incomplete"


# ---- baseline eligibility ------------------------------------------------------------------------

def test_acted_and_confirmed_sessions_never_enter_the_baseline():
    records = [rec(0.01)]
    assert rules.baseline_values_to_store("acted", records, False) == {}
    assert rules.baseline_values_to_store("live", records, True) == {}
    assert rules.baseline_values_to_store("live", records, False) == {"m_v1": 0.01}
    assert rules.baseline_values_to_store("normal", records, False) == {"m_v1": 0.01}


def test_a_concern_call_stores_nothing():
    assert rules.baseline_values_to_store("live", [rec(0.01)], False, True) == {}
    assert rules.baseline_values_to_store("live", [rec(0.01)], False, False) == {"m_v1": 0.01}


def test_any_usable_outside_reading_stores_nothing_even_when_not_confirmed():
    outside_then_inside = [rec(0.11, attempt="A1"), rec(0.01, attempt="A2")]  # as_usual call, but one reading outside
    assert rules.baseline_values_to_store("live", outside_then_inside, False) == {}
    other_metric = [rec(0.01, metric="a_v1"), rec(0.11, metric="b_v1", attempt="A2")]
    assert rules.baseline_values_to_store("live", other_metric, False) == {}  # one metric outside blocks all


def test_an_unusable_or_interrupted_outside_reading_does_not_block_the_baseline():
    # only usable final readings count: this record has no reading at all
    records = [rec(usable=False, outcome="interrupted"), rec(0.01, attempt="A2")]
    assert rules.baseline_values_to_store("live", records, False) == {"m_v1": 0.01}


def test_interrupted_and_unusable_attempts_do_not_enter_the_baseline():
    records = [rec(usable=False, outcome="interrupted"), rec(usable=False, outcome="completed")]
    assert rules.baseline_values_to_store("live", records, False) == {}


# ---- v6: same-side confirmation ------------------------------------------------------------------------

def test_left_hand_outside_then_right_hand_outside_is_not_confirmed():
    ev_ = rules.evaluate_sign([rec(0.11, attempt="A1", side="left"), rec(0.09, attempt="A2", side="right")])
    assert ev_.confirmed is False and ev_.side is None and len(ev_.attempts) == 2


def test_the_same_side_twice_confirms_and_names_the_side():
    for side in ("left", "right"):
        ev_ = rules.evaluate_sign([rec(0.11, attempt="A1", side=side), rec(0.09, attempt="A2", side=side)])
        assert ev_.confirmed is True and ev_.side == side


def test_a_pair_is_found_even_when_a_different_side_came_in_between():
    records = [rec(0.11, attempt="A1", side="left"), rec(0.09, attempt="A2", side="right"),
               rec(0.10, attempt="A3", side="left")]
    ev_ = rules.evaluate_sign(records)
    assert ev_.confirmed is True and ev_.side == "left"


def test_a_missing_side_does_not_disagree_with_a_named_one():
    assert rules.sides_match({"side": None}, {"side": "left"}) is True
    assert rules.sides_match({"side": "left"}, {"side": "right"}) is False
    assert rules.sides_match({"side": "left"}, {"side": "left"}) is True
    ev_ = rules.evaluate_sign([rec(0.11, attempt="A1", side=None), rec(0.09, attempt="A2", side="left")])
    assert ev_.confirmed is True and ev_.side == "left"


def test_smile_sides_follow_the_same_rule():
    # smile readings outside on opposite sides cannot both be "above" and "below" the same range, but if a
    # caller hands over mismatching sides with the same direction they are not one finding
    a = rec(0.11, attempt="A1", side="left")
    b = rec(0.09, attempt="A2", side="right")
    assert rules.evaluate_sign([a, b]).confirmed is False


# ---- v12b: one reading confirms (limits.CONFIRM_FROM_ONE_READING, the demo default) ----------------------------

def test_one_reading_confirms_a_lopsided_smile_on_the_side_of_the_lower_corner():
    ev = rules.evaluate_all({"smile": [rec(0.11)], "arms": [rec(0.01, task="arms")]}, one_reading=True)
    assert ev["smile"].confirmed and ev["smile"].side == "left" and len(ev["smile"].attempts) == 1
    assert not ev["arms"].confirmed
    ev = rules.evaluate_all({"smile": [rec(-0.09)]}, one_reading=True)
    assert ev["smile"].confirmed and ev["smile"].side == "right"


def test_one_reading_confirms_a_sinking_hand():
    ev = rules.evaluate_all({"arms": [rec(0.4, task="arms", side="left")]}, one_reading=True)
    assert ev["arms"].confirmed and ev["arms"].side == "left"


def test_a_hand_held_higher_than_usual_is_settled_and_not_a_sign():
    ev = rules.evaluate_all({"smile": [rec(0.01)], "arms": [rec(-0.3, task="arms", side="left")]}, one_reading=True)
    assert not ev["arms"].confirmed and ev["arms"].latest_outside is False
    assert rules.session_status(ev, ("smile", "arms")) == "as_usual"


def test_without_one_reading_a_single_outside_reading_still_waits_for_its_repeat():
    ev = rules.evaluate_all({"smile": [rec(0.11)]}, one_reading=False)
    assert not ev["smile"].confirmed and ev["smile"].latest_outside is True
