"""The attempt state machine and its input bookkeeping. Written, not run."""
from __future__ import annotations

import pytest

from analysis.session import attempt as at
from analysis.session.models import Metric, Quality, TaskObservation


def make(now=0.0):
    a = at.Attempt("A1", "call_t", "smile", now, "task_smile", 1)
    return a


def obs(seq=0, event_id=None, attempt_id="A1", signal="none", status="usable", call_id="call_t"):
    reasons = ["face_not_visible"] if status == "unusable" else []
    return TaskObservation(event_id or "obs_%d" % seq, call_id, attempt_id, "smile", seq, 1000, "capturing",
                           Quality(status, reasons), [], signal)


def test_happy_path_states():
    a = make()
    assert a.state == at.PREPARING
    a.capture_command_at = 0.0
    assert a.mark_capture_started(0.1) is True and a.state == at.CAPTURING
    assert a.close_input(5.0) is True and a.state == at.FINALIZING
    assert a.finalize({"x": 1}, at.COMPLETED, 5.1) is True
    assert a.state == at.COMPLETED and a.is_terminal


def test_illegal_transitions_are_refused():
    a = make()
    with pytest.raises(at.IllegalTransition):
        a.transition(at.COMPLETED, 0.0)
    a.begin_stop(0.1, "interrupted", "x")
    with pytest.raises(at.IllegalTransition):
        a.transition(at.CAPTURING, 0.2)


def test_any_active_state_can_stop_and_then_finish_terminal():
    for prepare in (lambda a: None, lambda a: a.mark_capture_started(0.0),
                    lambda a: (a.mark_capture_started(0.0), a.close_input(0.1))):
        a = make()
        prepare(a)
        assert a.begin_stop(1.0, "interrupted", "camera") is True
        assert a.state == at.STOPPING
        assert a.finalize({"r": 1}, at.INTERRUPTED, 1.1) is True and a.state == at.INTERRUPTED


def test_finalize_exactly_once():
    a = make()
    a.begin_stop(0.1, "cancelled", "call_ended")
    assert a.finalize({"first": True}, at.CANCELLED, 0.2) is True
    assert a.finalize({"second": True}, at.FAILED, 0.3) is False
    assert a.result == {"first": True} and a.state == at.CANCELLED
    assert a.begin_stop(0.4, "interrupted", "late") is False  # a terminal attempt cannot be stopped again


def test_stop_is_idempotent_and_keeps_the_first_cause():
    a = make()
    assert a.begin_stop(0.1, "interrupted", "first") is True
    assert a.begin_stop(0.2, "failed", "second") is False
    assert a.stop_reason == "first" and a.stop_kind == "interrupted"


def test_samples_before_the_capture_command_are_early():
    a = make()
    assert a.accept_sample(0, 0, "x", 0.1) == at.EARLY


def test_first_sample_starts_capture_and_sequences_must_increase():
    a = make()
    a.capture_command_at = 0.0
    assert a.accept_sample(0, 0, "x", 0.1) == at.ACCEPTED
    assert a.state == at.CAPTURING and a.first_sample_at == 0.1
    assert a.accept_sample(1, 200, "y", 0.3) == at.ACCEPTED
    assert a.accept_sample(1, 200, "y", 0.4) == at.DUPLICATE  # identical retry: ignored
    assert a.accept_sample(1, 200, "DIFFERENT", 0.5) == at.CONFLICT  # conflicting reuse: rejected
    assert a.accept_sample(0, 0, "x", 0.6) == at.DUPLICATE
    assert a.accept_sample(5, 1000, "z", 0.7) == at.ACCEPTED
    assert a.accept_sample(3, 600, "w", 0.8) == at.CONFLICT  # an unseen older sequence
    assert a.samples_received == 3


def test_late_samples_never_revive_a_stopped_or_finished_attempt():
    a = make()
    a.capture_command_at = 0.0
    a.accept_sample(0, 0, "x", 0.1)
    a.begin_stop(0.2, "interrupted", "camera")
    assert a.accept_sample(1, 200, "y", 0.3) == at.LATE
    a.finalize({"r": 1}, at.INTERRUPTED, 0.4)
    assert a.accept_sample(2, 400, "z", 0.5) == at.LATE
    assert a.state == at.INTERRUPTED


def test_samples_after_input_closed_are_late():
    a = make()
    a.capture_command_at = 0.0
    a.accept_sample(0, 0, "x", 0.1)
    a.close_input(1.0)
    assert a.accept_sample(1, 200, "y", 1.1) == at.LATE


def test_observation_sequences_duplicates_and_conflicts():
    a = make()
    a.capture_command_at = 0.0
    a.mark_capture_started(0.0)
    assert a.accept_observation(obs(0), 1.0) == at.ACCEPTED
    assert a.accept_observation(obs(0), 1.1) == at.DUPLICATE
    other = obs(0, signal="framing_unusable", status="unusable")
    assert a.accept_observation(other, 1.2) == at.CONFLICT  # same event id, different content
    assert a.accept_observation(obs(1), 1.3) == at.ACCEPTED
    assert a.accept_observation(obs(1, event_id="obs_other"), 1.4) == at.CONFLICT  # sequence reused
    assert len(a.observations) == 2 and a.latest_observation.sequence == 1


def test_observation_for_another_attempt_is_obsolete_and_late_ones_are_ignored():
    a = make()
    a.mark_capture_started(0.0)
    assert a.accept_observation(obs(0, attempt_id="A0"), 1.0) == at.OBSOLETE
    assert a.accept_observation(obs(0, call_id="other_call"), 1.0) == at.OBSOLETE
    a.begin_stop(1.5, "interrupted", "camera")
    assert a.accept_observation(obs(0), 2.0) == at.LATE
    a.finalize({"r": 1}, at.INTERRUPTED, 2.1)
    assert a.accept_observation(obs(1), 2.2) == at.LATE
    assert a.observations == []


def test_framing_clock_starts_at_first_bad_observation_and_a_good_one_clears_it():
    a = make()
    a.mark_capture_started(0.0)
    a.accept_observation(obs(0, signal="framing_unusable", status="unusable"), 1.0)
    a.accept_observation(obs(1, signal="framing_unusable", status="unusable"), 2.0)
    assert a.framing_bad_since == 1.0 and a.framing_bad_event_id == "obs_0"
    a.accept_observation(obs(2), 2.5)
    assert a.framing_bad_since is None and a.framing_bad_event_id is None
    assert a.last_analyzer_at == 2.5


def test_unperformable_signal_is_remembered():
    a = make()
    a.mark_capture_started(0.0)
    a.accept_observation(obs(0, signal="task_unperformable"), 1.0)
    assert a.unperformable_event_id == "obs_0"
