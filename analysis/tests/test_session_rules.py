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

def test_budget_allows_three_attempts_and_refuses_the_fourth():
    assert rules.attempt_budget([]) is None
    assert rules.attempt_budget([rec(usable=False, outcome="interrupted"), rec(usable=False, outcome="failed")]) is None
    three = [rec(usable=False, outcome="interrupted"), rec(usable=False, outcome="failed"),
             rec(usable=False, outcome="interrupted")]
    assert rules.attempt_budget(three) == "attempt_budget_exhausted"


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
