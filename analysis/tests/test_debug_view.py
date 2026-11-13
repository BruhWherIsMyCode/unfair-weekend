"""Debug mode (UW_DEBUG=on): side facts, terminal lines, verdicts, captions, pictures. Fake readers, fake adapter, fake
clock, fake picture writer. No mediapipe, no cv2, no network. Made-up landmarks.

Geometry used below: her eyes at x=200 (her right eye) and x=300 (her left eye), so the midline is x=250 and one eye
width is 100 px. A hand at x=400 is 150 px to the larger-x side = her LEFT (1.5 eye widths); x=110 is her RIGHT (1.4)."""
from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from analysis import backend_entry
from analysis.debug_view import (DebugView, flipped_side, frame_category, hand_side_facts, phase_judgement,
                                 smile_line)
from analysis.face_metric import FrameObservation, FrameReading
from analysis.hand_metric import Hand, hand_frame_reading
from analysis.tasks.stream_arms import ProductionHandReader, StreamArms
from analysis.tests.agent_helpers import CallRig, identity_decode
from analysis.tests.test_backend_entry import Link, modules as entry_modules, run as run_entry, seeded_env


def face(found: bool = True) -> FrameObservation:
    if not found:
        return FrameObservation(face_found=False)
    return FrameObservation(face_found=True, image_w=640, image_h=480,
                            points={"her_left_eye": (300.0, 200.0), "her_right_eye": (200.0, 200.0)})


def hand(x: float) -> Hand:
    return Hand(points=[(x, 300.0)] * 21)


class Writer:
    def __init__(self) -> None:
        self.saved: List[Any] = []

    def __call__(self, path: Path, image: Any, overlay: Dict[str, Any]) -> None:
        self.saved.append((path, overlay))


def view(lines: List[str], writer: Optional[Writer] = None, **kw: Any) -> DebugView:
    return DebugView("call_t", lines.append, Path("/x/debug") if writer is not None else None, writer, **kw)


def feed(v: DebugView, xs: Dict[int, Optional[float]], side: str = "left", attempt: str = "A1",
         mirrored: bool = False, noface: bool = False) -> None:
    """One frame per entry: capture ms -> hand x (None = no hand)."""
    for ms, x in xs.items():
        v.arms_frame(attempt, ms, side, "img", face(not noface), [hand(x)] if x is not None else [], mirrored)


# ---- the pure side facts --------------------------------------------------------------------------------------------

@pytest.mark.parametrize("mirrored", [False, True])
def test_side_facts_follow_the_measuring_midline_rule_and_flip_with_the_other_setting(mirrored):
    facts = hand_side_facts(face(), [hand(400.0), hand(110.0)], "left", mirrored)
    assert facts["face"] is True and facts["mirrored"] is mirrored and facts["expected"] == "left"
    by_side = {h["side"]: h for h in facts["hands"]}
    assert abs(by_side["her_left"]["eye_widths"] - 1.5) < 1e-9 and abs(by_side["her_right"]["eye_widths"] - 1.4) < 1e-9
    assert by_side["her_left"]["if_mirrored_flipped"] == "her_right"  # the opposite setting swaps the side
    assert by_side["her_right"]["if_mirrored_flipped"] == "her_left"
    assert flipped_side("her_left") == "her_right"


def test_side_facts_agree_with_what_the_measuring_decides():
    assert hand_frame_reading(face(), [hand(400.0)], "left").usable is True  # her left = larger x
    assert hand_side_facts(face(), [hand(400.0)], "left")["hands"][0]["side"] == "her_left"
    wrong = hand_frame_reading(face(), [hand(110.0)], "left")
    assert wrong.wrong_side is True
    assert hand_side_facts(face(), [hand(110.0)], "left")["hands"][0]["side"] == "her_right"
    assert hand_frame_reading(face(), [hand(110.0)], "right").usable is True
    assert hand_side_facts(face(), [hand(110.0)], "right")["hands"][0]["side"] == "her_right"


def test_side_facts_without_face_without_hand_and_with_a_broken_hand():
    assert hand_side_facts(face(False), [hand(400.0)], "left")["face"] is False
    assert hand_side_facts(None, [], "left")["face"] is False
    no_hand = hand_side_facts(face(), [], "left")
    assert no_hand["face"] is True and no_hand["hands"] == []
    assert hand_side_facts(face(), [Hand(points=[(1.0, 1.0)] * 5)], "left")["hands"] == []  # fewer than 21 points
    assert [frame_category(hand_side_facts(*a, "left")) for a in
            ((face(False), []), (face(), []), (face(), [hand(400.0)]), (face(), [hand(110.0)]))] == [
        "no_face", "none", "left", "right"]


# ---- terminal lines -----------------------------------------------------------------------------------------------

def test_one_plain_line_per_second_with_the_documented_shape():
    lines: List[str] = []
    feed(view(lines), {ms: 110.0 for ms in range(1000, 4000, 200)})
    assert len(lines) == 3  # 1.0 s, 2.0 s, 3.0 s
    assert lines[2] == ("3.0s DEBUG arms hold_left: face yes | hand on her RIGHT (1.4 eye widths) | expected LEFT | "
                        "mirroring off -> if flipped: her LEFT")


def test_the_line_says_no_face_no_hand_and_mirroring_on():
    lines: List[str] = []
    v = view(lines)
    feed(v, {1000: None})
    feed(v, {2000: 400.0}, mirrored=True)
    feed(v, {3000: 400.0}, noface=True)
    assert lines[0].endswith("face yes | no hand | expected LEFT | mirroring off -> if flipped: -")
    assert "mirroring on -> if flipped: her RIGHT" in lines[1]
    assert "face no |" in lines[2]


def test_smile_line_shows_strength_and_the_lower_mouth_corner():
    reading = FrameReading(usable=True, unit=100.0, corner_height=0.1, smile_strength=0.12, timestamp_ms=2000)
    assert smile_line(2000, "relax", reading, face()) == (
        "2.0s DEBUG smile relax: face yes | smile strength 0.12 | mouth corners left lower by 0.10")
    level = FrameReading(usable=True, corner_height=0.0, smile_strength=0.5)
    assert smile_line(4000, "smile", level, face()).endswith("smile strength 0.50 | mouth corners level")
    assert "face no | not usable: face not found" in smile_line(
        1000, "relax", FrameReading(usable=False, reason="face not found"), face(False))
    lines: List[str] = []
    v = view(lines)
    for ms in range(0, 3000, 200):
        v.smile_frame("A2", ms, "img", face(), reading, False)
    assert len(lines) == 3 and lines[0].startswith("0.0s DEBUG smile relax")


# ---- summary, verdict, captions -----------------------------------------------------------------------------------

def test_verdict_hand_on_the_wrong_side_names_the_mirroring_fix():
    lines: List[str] = []
    v = view(lines)
    feed(v, {ms: 110.0 for ms in range(1000, 6500, 200)})  # raised toward her right while her left was asked
    caption = v.finish_attempt("A1")
    summary = next(l for l in lines if "summary" in l)
    verdict = next(l for l in lines if "verdict" in l)
    assert "hold_left (expected LEFT): hand on her LEFT 0% | on her RIGHT 100% | no hand 0% | no face 0%" in summary
    assert "hand mostly on the side NOT expected" in verdict and "restart with --mirrored" in verdict
    assert "if she raised her left hand as asked, the picture is mirrored" in verdict
    assert caption == "Debug: I see a hand on your right side. I expected your left."


def test_verdict_with_mirroring_already_on_suggests_turning_it_off():
    lines: List[str] = []
    v = view(lines)
    feed(v, {ms: 110.0 for ms in range(1000, 3000, 200)}, mirrored=True)
    v.finish_attempt("A1")
    assert "restart without --mirrored" in next(l for l in lines if "verdict" in l)


def test_verdict_expected_side_and_no_hand_and_no_face():
    for xs, kw, verdict, caption in (
            ({ms: 400.0 for ms in range(1000, 3000, 200)}, {}, "hand on the expected side",
             "Debug: I see your left hand. Good."),
            ({ms: None for ms in range(1000, 3000, 200)}, {}, "no hand seen", "Debug: no hand seen."),
            ({ms: 400.0 for ms in range(1000, 3000, 200)}, {"noface": True}, "no face seen",
             "Debug: I do not see your face.")):
        lines: List[str] = []
        v = view(lines)
        feed(v, xs, **kw)
        assert v.finish_attempt("A1") == caption
        assert next(l for l in lines if "verdict" in l).endswith(verdict)
    assert phase_judgement({"left": 5, "right": 5}, "left") == "wrong"  # a tie goes to the informative answer


def test_a_caption_has_no_measurement_number_and_other_tasks_and_empty_attempts_give_none():
    lines: List[str] = []
    v = view(lines)
    feed(v, {1000: 110.0, 2400: 110.0})
    caption = v.finish_attempt("A1")
    assert not any(ch.isdigit() for ch in caption) and v.take_caption("A1") is not None
    assert v.finish_attempt("A9") is None  # never saw a frame
    v.smile_frame("A2", 0, "img", face(), FrameReading(usable=True, corner_height=0.0, smile_strength=0.1), False)
    assert v.finish_attempt("A2") is None  # smile has no end-of-attempt summary


def test_the_mid_hold_caption_comes_after_one_second_on_the_wrong_side_and_only_once_per_hold():
    v = view([])
    feed(v, {1000: 110.0, 1400: 110.0, 1800: 110.0})
    assert v.take_caption("A1") is None  # 0.8 s so far
    feed(v, {2000: 110.0, 2400: 110.0, 3000: 110.0})
    assert v.take_caption("A1") == "Debug: I see a hand on your right side. I expected your left."
    assert v.take_caption("A1") is None  # once per hold
    feed(v, {3200: 400.0, 3400: 110.0, 5000: 110.0})  # back on the right side, then wrong again: still only one
    assert v.take_caption("A1") is None
    feed(v, {8600: 400.0, 9000: 400.0, 10000: 400.0}, side="right")  # the other hold, wrong side too -> its own one
    assert v.take_caption("A1") == "Debug: I see a hand on your left side. I expected your right."
    assert v.take_caption("A1") is None


# ---- pictures -----------------------------------------------------------------------------------------------------

def test_about_two_pictures_per_second_named_by_attempt_task_and_time_with_the_overlay_facts():
    writer = Writer()
    feed(view([], writer), {ms: 110.0 for ms in range(1000, 6500, 200)})
    assert 9 <= len(writer.saved) <= 12
    path, overlay = writer.saved[0]
    assert path == Path("/x/debug/call_t/A1_arms_1000.jpg")
    assert overlay["task"] == "arms" and overlay["phase"] == "hold_left" and overlay["expected"] == "left"
    assert overlay["mirrored"] is False and overlay["midline_x"] == 250.0
    assert overlay["hands"][0]["side"] == "her_right" and len(overlay["eye_points"]) == 2


def test_smile_pictures_are_saved_too_and_the_cap_per_call_holds():
    writer = Writer()
    v = view([], writer, max_pictures=3)
    for ms in range(0, 6000, 200):
        v.smile_frame("A2", ms, "img", face(), FrameReading(usable=True, corner_height=0.0, smile_strength=0.1), False)
    assert len(writer.saved) == 3
    assert writer.saved[0][0].name == "A2_smile_0.jpg" and writer.saved[0][1]["expected"] is None
    feed(v, {ms: 110.0 for ms in range(1000, 3000, 200)})
    assert len(writer.saved) == 3  # the cap is per call, over all attempts


def test_a_failing_writer_stops_pictures_and_never_the_call():
    def broken(path: Path, image: Any, overlay: Dict[str, Any]) -> None:
        raise OSError("disk full")

    lines: List[str] = []
    v = DebugView("call_t", lines.append, Path("/x"), broken)
    feed(v, {ms: 110.0 for ms in range(1000, 3000, 200)})
    assert sum("pictures stopped" in l for l in lines) == 1


def test_no_pictures_without_a_folder_and_the_folder_is_announced_once():
    lines: List[str] = []
    v = DebugView("call_t", lines.append, Path("/x/debug"), Writer())
    v.announce()
    v.announce()
    assert lines == ["DEBUG pictures are saved in /x/debug/call_t (about 2 per second, at most 200)"]


# ---- the production hand reader reports what it saw (fake extractor, no mediapipe) -----------------------------------

class ScriptedExtractor:
    def __init__(self, where) -> None:
        self.where = where

    def process(self, image: Any, ts_ms: int, mirrored: bool = False):
        x = self.where(ts_ms)
        return face(), ([hand(x)] if x is not None else [])


def production_reader(where, mirrored: bool = False) -> ProductionHandReader:
    reader = ProductionHandReader(mirrored)
    reader._reader = ScriptedExtractor(where)  # stands in for mediapipe: the lazy import never happens
    return reader


def test_the_reader_hook_is_called_only_when_set_and_never_changes_the_reading():
    seen: List[Any] = []
    plain = production_reader(lambda ts: 110.0)
    hooked = production_reader(lambda ts: 110.0)
    hooked.on_frame = lambda *args: seen.append(args)
    a, b = plain("img", 1000, "left"), hooked("img", 1000, "left")
    assert (a.usable, a.reason, a.wrong_side) == (b.usable, b.reason, b.wrong_side) == (False, a.reason, True)
    assert len(seen) == 1 and seen[0][2] == "left" and seen[0][5] is False

    def boom(*args: Any) -> None:
        raise RuntimeError("debug bug")

    hooked.on_frame = boom
    assert hooked("img", 1200, "left").wrong_side is True  # a failing hook is swallowed


# ---- whole calls: captions, lines, and debug off ------------------------------------------------------------------

def debug_rig(where, mirrored: bool = False):
    rig = CallRig()
    lines: List[str] = []
    dv = DebugView("call_t", lines.append, None)
    reader = production_reader(where, mirrored)
    rig.session.modules["arms"] = StreamArms(reader, identity_decode, use_executor=False, debug=dv)
    rig.session.debug = dv
    return rig, lines


def test_a_call_with_the_hand_on_the_wrong_side_prints_lines_and_plays_both_captions():
    rig, lines = debug_rig(lambda ts: 110.0)  # her right side in both holds
    asyncio.run(rig.drive(rig.session.run_task("arms")))
    wrong = "Debug: I see a hand on your right side. I expected your left."
    assert rig.adapter.own_said.count(wrong) == 2  # once during the left hold, once at the end of the attempt
    assert [e["when"] for e in rig.kinds("debug_caption")] == ["mid_hold", "end_of_attempt"]
    assert any(l.startswith("1.0s DEBUG arms hold_left:") for l in lines)
    assert any("verdict: hand mostly on the side NOT expected" in l for l in lines)
    assert rig.session.results[-1]["task"] == "arms"  # the attempt still ended on its own rules


def test_a_call_with_the_expected_side_plays_only_the_good_caption():
    rig, lines = debug_rig(lambda ts: 400.0 if ts < 7000 else 110.0)  # left hold: her left; right hold: her right
    result = asyncio.run(rig.drive(rig.session.run_task("arms")))
    assert result["outcome"] == "completed"
    assert rig.adapter.own_said == ["Debug: I see your left hand. Good."]
    assert any("verdict: hand on the expected side" in l for l in lines)


def test_an_adapter_without_say_text_skips_the_captions_but_keeps_the_lines():
    rig, lines = debug_rig(lambda ts: 110.0)
    rig.adapter.say_text = None  # an adapter that cannot play the agent's own words
    asyncio.run(rig.drive(rig.session.run_task("arms")))
    assert rig.adapter.own_said == [] and rig.kinds("debug_caption") == []
    assert any("verdict" in l for l in lines)


def test_a_session_without_debug_has_no_debug_log_entries_and_no_captions():
    rig = CallRig()
    asyncio.run(rig.drive(rig.session.run_task("arms")))
    assert rig.session.debug is None and rig.adapter.own_said == []
    assert [e for e in rig.session.log if e["kind"].startswith("debug")] == []


# ---- the switch in backend_entry ---------------------------------------------------------------------------------

def test_the_switch_is_validated_like_the_others_and_off_by_default():
    assert backend_entry.Settings({}, "p").debug is False
    assert backend_entry.Settings({"UW_DEBUG": "off"}, "p").debug is False
    assert backend_entry.Settings({"UW_DEBUG": " ON "}, "p").debug is True
    with pytest.raises(ValueError, match="UW_DEBUG"):
        backend_entry.Settings({"UW_DEBUG": "yes"}, "p")


def test_default_modules_carry_the_view_only_when_given():
    plain = backend_entry.default_modules(False)
    assert plain["arms"].debug is None and plain["smile"].debug is None
    dv = DebugView("c", print)
    both = backend_entry.default_modules(False, dv)
    assert both["arms"].debug is dv and both["smile"].debug is dv


def untimed(lines: List[str]) -> List[str]:
    """The live lines without their time stamp (the real clock may tick over a tenth between two runs)."""
    return [re.sub(r"^(\[call [^\]]*\]) +\d+\.\ds", r"\1", l) for l in lines]


def one_run(tmp_path: Path, name: str, **extra: str):
    (tmp_path / name).mkdir()
    link, lines = Link(), []
    run_entry(link, seeded_env(tmp_path / name, **extra), entry_modules(), lines)
    return untimed(lines), link.calls


def test_debug_off_gives_the_same_call_log_and_adapter_calls_as_without_the_variable(tmp_path):
    plain, off = one_run(tmp_path, "plain"), one_run(tmp_path, "off", UW_DEBUG="off")
    assert plain == off and plain[0]  # the same lines, the same calls to the client
    assert not any("DEBUG" in l for l in plain[0])
    assert not (backend_entry.config.RUNS_DIR / "debug" / "call_t1").exists()  # nothing saved when debug is off


def test_debug_on_announces_the_folder_once_and_changes_nothing_else_the_call_does(tmp_path):
    (off_lines, off_calls), (on_lines, on_calls) = one_run(tmp_path, "off"), one_run(tmp_path, "on", UW_DEBUG="on")
    extra = [l for l in on_lines if "DEBUG" in l]
    assert len(extra) == 1 and extra[0].startswith("[call call_t1] DEBUG pictures are saved in ")
    assert [l for l in on_lines if "DEBUG" not in l] == off_lines  # the injected modules give no frames
    assert on_calls == off_calls  # no captions without frames
