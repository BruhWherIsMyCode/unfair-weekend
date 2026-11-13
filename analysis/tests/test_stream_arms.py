"""The one-hand arms task on streamed samples, with a fake hand reader and a fake JPEG decoder. Written, not run.

No mediapipe, no cv2, no camera: the numbers are made up. The window is 15 s of samples every 200 ms:
0-1 settle, 1-6.5 hold LEFT, 6.5-8.5 swap, 8.5-15 hold RIGHT (v11 timing).
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from analysis import config
from analysis.session import limits
from analysis.session.models import TaskContext
from analysis.tasks.stream_arms import METRIC_ID, StreamArms, cue_at_ms, hold_windows, phase_at
from analysis.tests.agent_helpers import ARMS_BASELINE, FakeHands, identity_decode

FIXTURES = json.loads((Path(__file__).resolve().parents[2] / "contract" / "fixtures.json").read_text("utf-8"))
NO_BASELINE = {"snapshot_id": "baseline_none", "ready": False, "sessions": 0, "needed": 5}


async def sample_stream(last_ms=limits.CAPTURE_MAX_MS, step=200):
    for i, ms in enumerate(range(0, last_ms + 1, step)):
        yield {"sequence": i, "captured_ms": ms, "format": "jpeg", "data_base64": "AAAA"}


def run_arms(reader, baseline=ARMS_BASELINE, decode=identity_decode, cancel=False, stream=None):
    async def scenario():
        context = TaskContext("call_t", "A1", "arms", baseline, asyncio.Event())
        if cancel:
            context.cancel_event.set()
        observations = []
        task = StreamArms(reader, decode, use_executor=False)
        result = await task.run(context, stream or sample_stream(), observations.append)
        return result, observations, task

    return asyncio.run(scenario())


def in_hold(ms):
    p = phase_at(ms)
    return p is not None and p[3] is not None


# ---- readings ---------------------------------------------------------------------------------------

def test_both_hands_within_their_usual_complete_with_no_side():
    result, _, task = run_arms(FakeHands())
    d = result.to_dict()  # also validates the schema rules
    assert d["outcome"] == "completed" and d["performance"] == "observed"
    assert d["quality"] == {"status": "usable", "reasons": []}
    r = d["reading"]
    assert r["metric_id"] == METRIC_ID == "hand_sink_v1" and r["unit"] == "eye_distance"
    assert abs(r["value"]) < 1e-9
    assert (r["usual_low"], r["usual_high"], r["outside"], r["side"]) == (-0.10, 0.10, False, None)
    assert d["baseline"] == {"snapshot_id": "baseline_a", "sessions": 12, "needed": 5, "ready": True}
    assert d["interruption"] is None
    assert set(task.attempt_values("A1")) == {"left", "right"}


def test_the_hand_closest_to_its_usual_high_is_reported_when_neither_is_outside():
    reader = FakeHands(sink={"left": 0.02, "right": 0.07})
    r = run_arms(reader)[0].to_dict()["reading"]
    assert abs(r["value"] - 0.07) < 1e-9 and r["outside"] is False and r["side"] is None


def test_left_hand_sinking_is_outside_on_the_left():
    r = run_arms(FakeHands(sink={"left": 0.4}))[0].to_dict()["reading"]
    assert abs(r["value"] - 0.4) < 1e-9
    assert (r["usual_low"], r["usual_high"], r["outside"], r["side"]) == (-0.10, 0.10, True, "left")


def test_the_hand_that_is_further_outside_is_reported():
    r = run_arms(FakeHands(sink={"left": 0.2, "right": 0.5}))[0].to_dict()["reading"]
    assert abs(r["value"] - 0.5) < 1e-9 and r["outside"] is True and r["side"] == "right"
    r = run_arms(FakeHands(sink={"left": 0.5, "right": 0.2}))[0].to_dict()["reading"]
    assert abs(r["value"] - 0.5) < 1e-9 and r["side"] == "left"


def test_a_hand_that_rises_higher_is_not_outside_only_sinking_counts():
    r = run_arms(FakeHands(sink={"left": -0.5}))[0].to_dict()["reading"]
    assert r["outside"] is False and r["side"] is None


def test_each_hand_uses_its_own_baseline():
    baseline = dict(ARMS_BASELINE)
    baseline["hand_right"] = dict(baseline["hand_right"], usual_low=0.0, usual_high=0.02)
    r = run_arms(FakeHands(sink={"left": 0.05, "right": 0.05}), baseline=baseline)[0].to_dict()["reading"]
    assert r["outside"] is True and r["side"] == "right" and r["usual_high"] == 0.02


def test_a_hand_that_drops_out_of_view_counts_as_a_big_sink():
    reader = FakeHands(usable=lambda side, ts: not (side == "left" and ts >= 4000))
    r = run_arms(reader)[0].to_dict()["reading"]
    assert r["value"] == config.DROP_SINK_VALUE and r["outside"] is True and r["side"] == "left"


# ---- unusable ---------------------------------------------------------------------------------------

def test_right_hand_missing_in_its_hold_is_unusable_and_names_the_hand():
    result, _, task = run_arms(FakeHands(usable=lambda side, ts: side != "right"))
    d = result.to_dict()
    assert d["outcome"] == "completed" and d["performance"] == "not_observed" and d["reading"] is None
    assert d["quality"] == {"status": "unusable", "reasons": ["right_hand_not_seen"]}
    assert task.attempt_values("A1") == {}  # a half-usable attempt never feeds a baseline


def test_left_hand_missing_and_both_missing():
    d = run_arms(FakeHands(usable=lambda side, ts: side != "left"))[0].to_dict()
    assert d["quality"]["reasons"] == ["left_hand_not_seen"]
    d = run_arms(FakeHands(usable=lambda side, ts: False))[0].to_dict()
    assert d["quality"]["reasons"] == ["left_hand_not_seen", "right_hand_not_seen"] and d["reading"] is None


def test_unreadable_jpeg_counts_as_a_hand_not_seen():
    d = run_arms(FakeHands(), decode=lambda sample: None)[0].to_dict()
    assert d["quality"]["status"] == "unusable" and d["reading"] is None


# ---- phases ------------------------------------------------------------------------------------------

def test_phase_boundaries():
    assert phase_at(0)[0] == "settle" and phase_at(999)[0] == "settle"
    assert phase_at(1000)[3] == "left" and phase_at(6999)[3] == "left"
    assert phase_at(7000)[0] == "swap" and phase_at(11499)[0] == "swap"
    assert phase_at(11500)[3] == "right" and phase_at(19999)[3] == "right"
    assert phase_at(20000) is None
    assert hold_windows() == {"left": (1000, 7000), "right": (11500, 20000)}
    assert cue_at_ms() == 7000
    assert limits.ARMS_PHASES[0][1] == 0 and limits.ARMS_PHASES[-1][2] == limits.CAPTURE_MAX_MS == 20000


def test_frames_in_settle_and_swap_are_not_read_at_all():
    reader = FakeHands()
    run_arms(reader)
    assert reader.calls, "hold frames must be read"
    for ts, side in reader.calls:
        assert in_hold(ts) and phase_at(ts)[3] == side  # the reader got the side of the phase it was called in
    assert not [c for c in reader.calls if c[0] < 1000 or 7000 <= c[0] < 11500]


def test_observations_flag_framing_only_in_hold_phases():
    result, obs, _ = run_arms(FakeHands(usable=lambda side, ts: False))
    capturing = [o for o in obs if o.phase == "capturing"]
    assert capturing
    for o in capturing:
        p = phase_at(o.elapsed_ms)
        if in_hold(o.elapsed_ms) and o.elapsed_ms - p[1] >= limits.ARMS_RAISE_GRACE_MS:
            assert o.signal == "framing_unusable" and o.quality.status == "unusable"
        elif in_hold(o.elapsed_ms):
            assert o.signal == "none" and o.quality.status == "pending"  # still raising the hand
        else:
            assert o.signal == "none" and o.quality.status == "pending"  # settle and swap are never flagged
    assert any(not in_hold(o.elapsed_ms) for o in capturing)
    for o in obs:
        o.to_dict()  # every observation is schema-valid
    assert obs[-1].phase == "finalizing" and obs[-1].quality.status == "pending"
    assert [o.sequence for o in obs] == list(range(len(obs)))
    assert result.quality.reasons == ["left_hand_not_seen", "right_hand_not_seen"]


def test_observations_come_about_every_second_and_carry_the_provisional_sink():
    _, obs, _ = run_arms(FakeHands(sink={"left": 0.4}))
    capturing = [o for o in obs if o.phase == "capturing"]
    seconds = [o.elapsed_ms for o in capturing]
    assert seconds[0] == 0 and all(b - a <= 1000 for a, b in zip(seconds, seconds[1:]))
    at_4s = next(o for o in capturing if o.elapsed_ms == 6000)  # v11: the hold is 1-6.5 s, the sink starts mid-hold
    assert at_4s.quality.status == "usable" and at_4s.signal == "none"
    assert at_4s.metrics and at_4s.metrics[0].metric_id == METRIC_ID and abs(at_4s.metrics[0].value - 0.4) < 1e-9
    assert all(o.event_id == "obs_A1_%d" % o.sequence for o in obs)


def test_a_hold_that_loses_the_hand_then_recovers_emits_on_the_transition():
    reader = FakeHands(usable=lambda side, ts: not (1400 <= ts < 2000))
    _, obs, _ = run_arms(reader)
    bad = [o for o in obs if o.signal == "framing_unusable"]
    assert [o.elapsed_ms for o in bad] == [1400]  # emitted at once, not on the next whole second
    assert [o for o in obs if o.elapsed_ms == 2000 and o.quality.status == "usable"]


# ---- baseline ----------------------------------------------------------------------------------------

def test_baseline_missing_gives_null_comparison_and_ready_false():
    d = run_arms(FakeHands(sink={"left": 0.4}), baseline=NO_BASELINE)[0].to_dict()
    r = d["reading"]
    assert abs(r["value"] - 0.4) < 1e-9 and r["usual_low"] is None and r["usual_high"] is None
    assert r["outside"] is None and r["side"] is None and d["baseline"]["ready"] is False


def test_baseline_ready_only_when_both_hands_are_ready():
    one = dict(ARMS_BASELINE)
    one["hand_right"] = {"ready": False, "sessions": 2, "needed": 5, "usual_low": None, "usual_high": None}
    d = run_arms(FakeHands(sink={"left": 0.4}), baseline=one)[0].to_dict()
    assert d["baseline"]["ready"] is False and d["reading"]["outside"] is None


# ---- misc --------------------------------------------------------------------------------------------

def test_samples_after_the_window_are_not_measured():
    reader = FakeHands()
    run_arms(reader, stream=sample_stream(last_ms=20600))
    assert max(ts for ts, _ in reader.calls) <= 20000


def test_cancel_signal_ends_the_run_without_a_reading():
    result, obs, _ = run_arms(FakeHands(), cancel=True)
    assert result.outcome == "cancelled" and result.reading is None and obs == []
    result.to_dict()


def test_definition_has_the_published_capture_settings_and_the_swap_cue():  # noqa: E302
    d = StreamArms().definition()
    fixture = next(f for f in FIXTURES if f["name"] == "capture_arms")["data"]["settings"]
    # v12: the arms window grew to 20 s (the raised contract maximum); fixtures.json keeps draft 0.2's 12000
    assert d["capture"] == dict(fixture, duration_ms=20000)
    assert d["cues"] == [{"at_ms": 7000, "line_id": "swap_hands"}]
    assert d["metric_id"] == "hand_sink_v1" and d["required_resource"] == "camera" and d["sign"] == "arm"
    assert d["instruction_line"] == "task_arms" and d["repeat_line"] == "repeat_arms"


# ---- raise grace (owner's real PC run: every arms attempt was interrupted ~3 s in) ---------------------

def test_a_hand_raised_late_within_the_grace_is_not_flagged_and_still_measured():
    # the left hand appears 2.4 s into its hold, the right hand 2.8 s into its hold (still within the grace)
    reader = FakeHands(usable=lambda side, ts: (side == "left" and ts >= 3400) or (side == "right" and ts >= 14300))
    result, obs, _ = run_arms(reader)
    assert not [o for o in obs if o.signal == "framing_unusable"]
    d = result.to_dict()
    assert d["outcome"] == "completed" and d["quality"]["status"] == "usable" and d["reading"] is not None


def test_a_hand_never_raised_is_flagged_once_the_grace_is_over():
    reader = FakeHands(usable=lambda side, ts: side == "right")
    _, obs, _ = run_arms(reader)
    bad = [o.elapsed_ms for o in obs if o.signal == "framing_unusable"]
    first = 1000 + limits.ARMS_RAISE_GRACE_MS
    assert bad and first <= min(bad) < first + 200  # the first sample at or after the grace
    assert all(first <= ms < 7000 for ms in bad)


def test_losing_the_hand_after_it_was_seen_is_flagged_at_once_even_inside_the_grace():
    reader = FakeHands(usable=lambda side, ts: not (1400 <= ts < 2000))
    _, obs, _ = run_arms(reader)
    assert [o.elapsed_ms for o in obs if o.signal == "framing_unusable"] == [1400]


def test_the_grace_is_a_constructor_setting():
    reader = FakeHands(usable=lambda side, ts: False)

    async def scenario():
        context = TaskContext("call_t", "A1", "arms", ARMS_BASELINE, asyncio.Event())
        observations = []
        task = StreamArms(reader, identity_decode, use_executor=False, raise_grace_ms=0)
        await task.run(context, sample_stream(), observations.append)
        return observations

    obs = asyncio.run(scenario())
    assert min(o.elapsed_ms for o in obs if o.signal == "framing_unusable") == 1000

