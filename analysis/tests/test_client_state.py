"""The client's pure state machine, with a fake clock. No cv2, no asyncio."""
from __future__ import annotations

from analysis.pc.client_measure import hint_for
from analysis.pc.client_state import ANSWER_PROMPT, ClientState, say_duration_s, wrap_text
from analysis.face_metric import FrameReading
from analysis.hand_metric import HandFrameReading
from analysis.session import limits


class T:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def make(**kw):
    clock = T()
    s = ClientState("p", clock, **kw)
    s.on_connection(True)
    s.pop_outgoing()
    return s, clock


def srv(type_, **f):
    d = {"type": type_, "call_id": "c", "seq": 1}
    d.update(f)
    return d


def types(s):
    return [t for t, _ in s.pop_outgoing()]


def test_connect_sends_hello_and_disconnect_resets():
    clock = T()
    s = ClientState("p", clock)
    s.on_connection(True)
    out = s.pop_outgoing()
    assert out[0][0] == "hello" and out[0][1]["protocol"] == 1 and out[0][1]["capabilities"]["measures"] == ["smile", "arms"]
    assert "CONNECTED" in s.view().connection
    s.request_start()
    s.on_connection(False)
    assert s.view().connection.startswith("NOT CONNECTED") and s.in_call is False


def test_space_starts_a_call_only_when_connected_and_idle():
    clock = T()
    s = ClientState("p", clock)
    s.request_start()
    assert s.pop_outgoing() == []
    s.on_connection(True)
    s.pop_outgoing()
    s.request_start()
    s.request_start()
    assert types(s) == ["start_call"]


def test_text_only_said_after_max_1_5_s_or_words_per_2_5():
    s, clock = make()
    s.on_server_message(srv("say", line_id="a", text="Hello there"))
    s.on_server_message(srv("say", line_id="b", text=" ".join(["word"] * 10)))
    assert s.view().saying.startswith("word") and say_duration_s("Hello there") == 1.5 and say_duration_s("w " * 10) == 4.0
    s.tick()
    assert "said" not in types(s)
    clock.t += 1.6
    s.tick()
    assert [f["line_id"] for t, f in s.pop_outgoing() if t == "said"] == ["a"]
    clock.t += 3
    s.tick()
    assert [f["line_id"] for t, f in s.pop_outgoing() if t == "said"] == ["b"]


def test_tts_mode_speaks_and_reports_when_finished():
    s, clock = make(tts=True)
    s.on_server_message(srv("say", line_id="a", text="Hello"))
    clock.t += 10
    s.tick()
    assert "said" not in types(s)
    assert s.take_speech() == [("a", "Hello")] and s.take_speech() == []
    s.line_finished("a")
    assert s.pop_outgoing() == [("said", {"line_id": "a"})]


def test_answer_box_enter_escape_and_timeout():
    s, clock = make()
    s.on_server_message(srv("listen", question_id="q1", timeout_s=8.0))
    assert s.view().answer_prompt == ANSWER_PROMPT
    for ch in "I am ok":
        assert s.key_char(ch)
    s.key_backspace()
    assert s.view().answer_text == "I am o"
    assert s.key_enter() and s.pop_outgoing() == [("answer", {"question_id": "q1", "transcript": "I am o"})]
    assert s.view().answer_prompt is None and not s.key_char("x")
    s.on_server_message(srv("listen", question_id="q2", timeout_s=8.0))
    s.key_escape()
    assert s.pop_outgoing() == [("answer", {"question_id": "q2", "transcript": None})]
    s.on_server_message(srv("listen", question_id="q3", timeout_s=8.0))
    s.key_enter()
    assert s.pop_outgoing()[0][1]["transcript"] is None  # empty text = no answer
    s.on_server_message(srv("listen", question_id="q4", timeout_s=8.0))
    clock.t += 7.9
    s.tick()
    assert "answer" not in types(s)
    clock.t += 0.2
    s.tick()
    assert ("answer", {"question_id": "q4", "transcript": None}) in s.pop_outgoing()
    assert s.answer is None


def arms_message():
    return srv("start_capture", attempt_id="A2", task="arms",
               settings={"duration_ms": 12000, "sample_period_ms": 200},
               cues=[{"at_ms": 5000, "line_id": "swap_hands"}], phases=[list(p) for p in limits.ARMS_PHASES])


def test_capture_samples_every_period_and_ends_at_the_duration():
    s, clock = make()
    s.on_server_message(arms_message())
    assert types(s) == ["capture_started"]
    frames = []
    for _ in range(130):  # 10 ms steps over 13 s
        due = s.capture_due()
        if due is not None:
            frames.append(due)
            s.reading_done(due, {"usable": True})
        clock.t += 0.1
        s.tick()
    assert 60 <= len(frames) <= 62
    assert [f.sequence for f in frames] == list(range(len(frames)))
    assert all(b.captured_ms - a.captured_ms >= 200 for a, b in zip(frames, frames[1:]))
    sides = {f.phase: f.expected_side for f in frames}
    assert sides["hold_left"] == "left" and sides["hold_right"] == "right" and sides["swap"] is None
    out = s.pop_outgoing()
    readings = [f for t, f in out if t == "reading"]
    assert len(readings) == len(frames) and readings[0]["task"] == "arms" and readings[0]["attempt_id"] == "A2"
    assert [t for t, _ in out if t == "capture_ended"] == ["capture_ended"] and s.capture is None


def test_dropped_frames_are_not_queued_when_measuring_is_slow():
    s, clock = make()
    s.on_server_message(arms_message())
    first = s.capture_due()
    clock.t += 1.0  # a slow frame: the next one is measured now, once
    second = s.capture_due()
    assert second.captured_ms >= 1000 and s.capture_due() is None and first.sequence == 0


def test_timer_shows_time_left_and_the_swap_countdown():
    s, clock = make()
    s.on_server_message(arms_message())
    assert s.view().timer == "12 s left  |  swap in 5 s"
    clock.t += 6
    assert s.view().timer == "6 s left"


def test_cancel_capture_acks_and_stops_readings():
    s, clock = make()
    s.on_server_message(arms_message())
    s.pop_outgoing()
    due = s.capture_due()
    s.on_server_message(srv("cancel_capture", attempt_id="A2", reason="x"))
    s.reading_done(due, None)  # measured while cancelled: dropped
    assert s.pop_outgoing() == [("cancel_ack", {"attempt_id": "A2"})] and s.capture_due() is None
    s.on_server_message(srv("cancel_capture", attempt_id="OLD", reason="x"))
    assert types(s) == ["cancel_ack"]


def test_status_every_second_with_the_camera_state():
    s, clock = make()
    s.tick()
    first = s.pop_outgoing()
    assert first[0][0] == "status" and first[0][1]["camera"] == "unavailable" and first[0][1]["camera_reason"] == "camera_lost"
    s.set_camera("available", None)
    clock.t += 0.5
    s.tick()
    assert s.pop_outgoing() == []
    clock.t += 0.6
    s.tick()
    out = s.pop_outgoing()
    assert out[0][1]["camera"] == "available" and out[0][1]["microphone"] == "unavailable"
    s.set_local_hint("face not visible")
    clock.t += 1.0
    s.tick()
    assert s.pop_outgoing()[0][1]["text"] == "face not visible"


def test_hint_expires_and_server_progress_shows():
    s, clock = make()
    s.set_local_hint("Raise your LEFT hand")
    assert s.view().hint == "Raise your LEFT hand"
    clock.t += 2.5
    assert s.view().hint == ""
    s.on_server_message(srv("progress", text="Please look at the camera."))
    assert s.view().hint == "Please look at the camera."


def test_result_card_and_next_call_clears_it():
    s, clock = make()
    s.request_start()
    s.on_server_message(srv("call_result", summary={"status": "change_noticed"}, family_text="Call her. If she seems unwell, call 112."))
    v = s.view()
    assert v.result_title == "Something looked different from her usual" and "112" in v.result_text and not v.in_call
    s.request_start()
    assert s.view().result_title is None


def test_hang_up_then_quit_and_auto_start():
    s, clock = make()
    s.request_start()
    s.pop_outgoing()
    s.request_hang_up_or_quit()
    assert types(s) == ["hang_up"] and not s.quit
    s.request_hang_up_or_quit()
    assert s.quit
    a, _ = make(auto_start=True)
    a.on_server_message(srv("hello_ok", protocol=1, server_version="x"))
    assert types(a) == ["start_call"]


def test_wrap_text():
    assert wrap_text("one two three four", 9) == ["one two", "three", "four"]
    assert wrap_text("supercalifragilistic a", 5) == ["supercalifragilistic", "a"]
    assert wrap_text("", 10) == []


def test_hints_in_plain_words():
    ok = FrameReading(usable=True)
    assert hint_for("smile", ok, None) == "going fine"
    assert hint_for("smile", None, None) == "face not visible"
    assert hint_for("smile", FrameReading(usable=False, reason="face not found"), None) == "face not visible"
    assert hint_for("smile", FrameReading(usable=False, reason="face too small or far away"), None) == "come closer to the camera"
    wrong = HandFrameReading(usable=False, reason="wrong hand: use your LEFT hand", face_ok=True, wrong_side=True)
    assert hint_for("arms", wrong, "left") == "Raise your LEFT hand"
    nohand = HandFrameReading(usable=False, reason="no hand on your RIGHT side", face_ok=True)
    assert hint_for("arms", nohand, "right") == "Raise your RIGHT hand"
    assert hint_for("arms", HandFrameReading(usable=False, reason="face not found"), "left") == "face not visible"
    assert hint_for("arms", HandFrameReading(usable=True, face_ok=True), "left") == "going fine"
