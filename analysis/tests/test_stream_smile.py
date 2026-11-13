"""The streamed smile task with a fake frame reader and a fake JPEG decoder. Written, not run.

No mediapipe, no cv2, no camera: the numbers are made up.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from analysis.face_metric import FrameReading
from analysis.session.models import TaskContext
from analysis.tasks.stream_registry import STREAM_TASKS
from analysis.tasks.stream_smile import METRIC_ID, StreamSmile, final_result, phase_reading

FIXTURES = json.loads((Path(__file__).resolve().parents[2] / "contract" / "fixtures.json").read_text("utf-8"))

READY = {"snapshot_id": "baseline_t", "ready": True, "sessions": 12, "needed": 5, "usual_low": -0.02,
         "usual_high": 0.03}
LEARNING = {"snapshot_id": "baseline_l", "ready": False, "sessions": 2, "needed": 5, "usual_low": None,
            "usual_high": None}


class FakeReader:
    """A made-up face: relaxed before 1.5 s, then smiling with the given mouth-corner height."""

    def __init__(self, corner=0.0, usable=lambda ts: True, smiling=lambda ts: ts >= 1500, reason="face not found"):
        self.corner, self.usable, self.smiling, self.reason = corner, usable, smiling, reason

    def __call__(self, image, ts):
        if not self.usable(ts):
            return FrameReading(usable=False, reason=self.reason, timestamp_ms=ts)
        smiling = self.smiling(ts)
        h = self.corner if smiling else 0.0
        return FrameReading(usable=True, unit=100.0, below_left=1.0 + h / 2, below_right=1.0 - h / 2,
                            corner_height=h, smile_strength=0.9 if smiling else 0.1, timestamp_ms=ts)


def decode_ok(sample):
    return sample


def decode_fail(sample):
    return None


async def sample_stream(last_ms=5000, step=200, extra=()):
    for i, ms in enumerate(range(0, last_ms + 1, step)):
        yield {"sequence": i, "captured_ms": ms, "format": "jpeg", "data_base64": "AAAA"}
    for j, ms in enumerate(extra):
        yield {"sequence": 1000 + j, "captured_ms": ms, "format": "jpeg", "data_base64": "AAAA"}


def run_task(reader, baseline=READY, decode=decode_ok, cancel=False, stream=None):
    async def scenario():
        context = TaskContext("call_t", "A1", "smile", baseline, asyncio.Event())
        if cancel:
            context.cancel_event.set()
        observations = []
        task = StreamSmile(reader, decode, use_executor=False)
        result = await task.run(context, stream or sample_stream(), observations.append)
        return result, observations

    return asyncio.run(scenario())


def test_outside_reading_is_compared_with_the_frozen_baseline():
    result, _ = run_task(FakeReader(corner=0.11))
    d = result.to_dict()  # also validates the schema rules
    assert d["outcome"] == "completed" and d["performance"] == "observed"
    assert d["quality"] == {"status": "usable", "reasons": []}
    r = d["reading"]
    assert r["metric_id"] == METRIC_ID and abs(r["value"] - 0.11) < 1e-9
    assert (r["usual_low"], r["usual_high"], r["outside"], r["side"]) == (-0.02, 0.03, True, "left")
    assert d["baseline"] == {"snapshot_id": "baseline_t", "sessions": 12, "needed": 5, "ready": True}
    assert d["interruption"] is None


def test_reading_within_usual_has_no_side():
    d = run_task(FakeReader(corner=0.01))[0].to_dict()
    assert d["reading"]["outside"] is False and d["reading"]["side"] is None


def test_below_the_usual_range_is_her_right_side():
    d = run_task(FakeReader(corner=-0.08))[0].to_dict()
    assert d["reading"]["outside"] is True and d["reading"]["side"] == "right"


def test_missing_baseline_gives_null_comparison():
    d = run_task(FakeReader(corner=0.11), baseline=LEARNING)[0].to_dict()
    r = d["reading"]
    assert r["value"] > 0 and r["usual_low"] is None and r["usual_high"] is None and r["outside"] is None
    assert d["baseline"]["ready"] is False


def test_observations_about_once_per_second_and_one_finalizing():
    _, obs = run_task(FakeReader(corner=0.01))
    dicts = [o.to_dict() for o in obs]  # every observation is schema-valid
    capturing = [o for o in obs if o.phase == "capturing"]
    assert [o.elapsed_ms for o in capturing] == [0, 1000, 2000, 3000, 4000, 5000]
    assert [o.sequence for o in obs] == list(range(len(obs)))
    assert obs[-1].phase == "finalizing" and obs[-1].quality.status == "pending"
    assert all(o.event_id == "obs_A1_%d" % o.sequence for o in obs)
    assert capturing[1].metrics and capturing[1].metrics[0].metric_id == METRIC_ID
    assert all(d["call_id"] == "call_t" and d["attempt_id"] == "A1" and d["task"] == "smile" for d in dicts)


def test_quality_transition_emits_at_once_and_flags_framing():
    reader = FakeReader(corner=0.01, usable=lambda ts: not (1200 <= ts < 2000))
    result, obs = run_task(reader)
    unusable = [o for o in obs if o.quality.status == "unusable"]
    assert [o.elapsed_ms for o in unusable] == [1200]  # emitted on the transition, not on the next whole second
    assert unusable[0].signal == "framing_unusable" and unusable[0].quality.reasons == ["face_not_found"]
    recovered = [o for o in obs if o.elapsed_ms == 2000]
    assert recovered and recovered[0].quality.status == "usable" and recovered[0].signal == "none"
    assert result.outcome == "completed"  # enough usable frames remain


def test_no_clear_smile_is_not_a_reading():
    result, _ = run_task(FakeReader(corner=0.11, smiling=lambda ts: False))
    d = result.to_dict()
    assert d["outcome"] == "could_not_perform" and d["performance"] == "not_observed" and d["reading"] is None


def test_too_few_usable_frames_is_unusable_with_reasons():
    result, obs = run_task(FakeReader(usable=lambda ts: False))
    d = result.to_dict()
    assert d["outcome"] == "completed" and d["quality"]["status"] == "unusable" and d["reading"] is None
    assert d["performance"] == "uncertain"
    assert d["quality"]["reasons"] == ["face_not_in_view", "face_not_found"]  # v11: the reason is face_not_in_view
    assert all(o.signal == "framing_unusable" for o in obs if o.phase == "capturing")


def test_unreadable_jpeg_counts_as_an_unusable_frame():
    d = run_task(FakeReader(corner=0.11), decode=decode_fail)[0].to_dict()
    assert d["quality"]["status"] == "unusable" and d["reading"] is None
    assert "image_could_not_be_decoded" in d["quality"]["reasons"]


def test_samples_after_the_window_are_not_measured():
    reader = FakeReader(corner=0.01, smiling=lambda ts: ts >= 1500 and ts <= 5000)
    stream = sample_stream(last_ms=4800, extra=(5200, 5400))
    d = run_task(reader, stream=stream)[0].to_dict()
    assert d["outcome"] == "completed"


def test_cancel_signal_ends_the_run_without_a_reading():
    result, obs = run_task(FakeReader(corner=0.11), cancel=True)
    assert result.outcome == "cancelled" and result.reading is None and obs == []
    result.to_dict()


def test_final_result_is_pure_and_matches_clip_arithmetic():
    frames = [FrameReading(usable=True, unit=100.0, below_left=1.0, below_right=1.0, corner_height=0.0,
                           smile_strength=0.1, timestamp_ms=i * 200) for i in range(8)]
    frames += [FrameReading(usable=True, unit=100.0, below_left=1.0, below_right=1.0, corner_height=0.05,
                            smile_strength=0.9, timestamp_ms=2000 + i * 200) for i in range(12)]
    ctx = type("Ctx", (), {"attempt_id": "A9", "baseline": READY})()  # final_result reads only these two fields
    d = final_result(ctx, frames).to_dict()
    assert abs(d["reading"]["value"] - 0.05) < 1e-9 and d["reading"]["outside"] is True


def _clip(relax_corner, smile_corner, smile_strength):
    """1.5 s relaxed (strength 0), then the smile phase with the given corner height and strength."""
    frames = [FrameReading(usable=True, unit=100.0, below_left=1.0, below_right=1.0, corner_height=relax_corner,
                           smile_strength=0.0, timestamp_ms=i * 200) for i in range(8)]
    frames += [FrameReading(usable=True, unit=100.0, below_left=1.0, below_right=1.0, corner_height=smile_corner,
                            smile_strength=smile_strength, timestamp_ms=1600 + i * 200) for i in range(20)]
    return frames


def test_a_one_sided_smile_with_little_strength_is_still_measured():
    # owner's acted run: the face model scored the lopsided smile ~0.1, but the corner height moved 0.00 -> +0.11
    status, value, reason = phase_reading(_clip(-0.01, 0.11, 0.1))
    assert (status, reason) == ("usable", None) and abs(value - 0.11) < 1e-9


def test_no_smile_and_no_corner_move_is_still_no_smile_seen():
    assert phase_reading(_clip(-0.01, 0.02, 0.1)) == ("not_measured", None, "no_smile_seen")


def test_a_normal_smile_keeps_the_strength_rule():
    status, value, _ = phase_reading(_clip(-0.01, -0.05, 0.7))
    assert status == "usable" and abs(value - (-0.05)) < 1e-9


# ---- definitions and stubs ----------------------------------------------------------------------

def fixture(name):
    return next(f for f in FIXTURES if f["name"] == name)["data"]["settings"]


def test_definitions_use_the_published_capture_settings():
    assert STREAM_TASKS["smile"].definition()["capture"] == fixture("capture_smile")
    assert STREAM_TASKS["arms"].definition()["capture"] == dict(fixture("capture_arms"), duration_ms=20000)  # v12
    assert STREAM_TASKS["sentence"].definition()["capture"] == fixture("capture_sentence")
    smile = STREAM_TASKS["smile"].definition()
    assert smile["instruction_line"] == "task_smile" and smile["repeat_line"] == "repeat_smile"
    assert smile["metric_id"] == METRIC_ID and smile["required_resource"] == "camera"
    assert STREAM_TASKS["sentence"].definition()["required_resource"] == "microphone"


def test_sentence_is_a_stub_that_fails_with_a_clear_reason():  # arms became real in v6 (test_stream_arms.py)
    async def scenario():
        for name in ("sentence",):
            context = TaskContext("call_t", "A5", name, LEARNING, asyncio.Event())
            result = await STREAM_TASKS[name].run(context, sample_stream(), lambda o: None)
            d = result.to_dict()
            assert d["outcome"] == "failed" and d["quality"] == {"status": "unusable",
                                                                  "reasons": ["not_implemented_in_v5"]}
            assert d["reading"] is None and d["interruption"] is None and d["task"] == name

    asyncio.run(scenario())
