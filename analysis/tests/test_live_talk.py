"""v11 live talk: the smile with a relaxed phase and a cue, one repeat for a not-measured attempt, retry_hint, the
agent's own words (say_own / ask_followup) with every guard, the self-check and the terminal lines.

FakeClock, fake adapters, scripted fake Claude; no mediapipe, no network, no real Claude.
"""
from __future__ import annotations

import asyncio
import json

from analysis.face_metric import FrameReading
from analysis.session import limits
from analysis.session.agent import ClaudeAgent
from analysis.session.agent_tools import RETRY_HINT_TEXT, own_text_problem, retry_hint_for
from analysis.session.call_record import format_terminal_line
from analysis.session.claude_client import FakeClaudeClient, tool_use_response
from analysis.session.claude_sub_client import answer_format_section
from analysis.session.fallback import next_step
from analysis.tasks.stream_smile import StreamSmile, phase_reading
from analysis.tests.agent_helpers import CallRig, FakeSmile, identity_decode


# ---- the smile with a relaxed phase ---------------------------------------------------------------------

def frames(strength_of, corner=0.05, last_ms=5800, usable=lambda ts: True):
    out = []
    for ts in range(0, last_ms + 1, 200):
        if not usable(ts):
            out.append(FrameReading(usable=False, reason="face not found", timestamp_ms=ts))
            continue
        s = strength_of(ts)
        out.append(FrameReading(usable=True, unit=100.0, below_left=1.0, below_right=1.0, corner_height=corner,
                                smile_strength=s, timestamp_ms=ts))
    return out


def test_relaxed_then_smile_is_usable():
    status, value, why = phase_reading(frames(lambda ts: 0.1 if ts < 1500 else 0.9))
    assert (status, why) == ("usable", None) and abs(value - 0.05) < 1e-9


def test_smiling_throughout_is_not_measured_as_already_smiling():
    assert phase_reading(frames(lambda ts: 0.9)) == ("not_measured", None, "already_smiling")


def test_never_smiling_is_not_measured_as_no_smile_seen():
    assert phase_reading(frames(lambda ts: 0.1)) == ("not_measured", None, "no_smile_seen")


def test_too_few_face_frames_is_face_not_in_view():
    status, value, why = phase_reading(frames(lambda ts: 0.5, usable=lambda ts: ts < 1000))
    assert (status, value, why) == ("unusable", None, "face_not_in_view")


def test_relaxed_reference_comes_from_the_relax_phase_not_the_weakest_fifth():
    # relax phase 0.45 (not a strong smile), smile phase peaks at 0.75: the rise over the RELAX frames is 0.30
    status, value, why = phase_reading(frames(lambda ts: 0.45 if ts < 1500 else (0.75 if ts > 3000 else 0.5)))
    assert status == "usable"


def test_fewer_than_three_relax_frames_falls_back_to_the_old_rule():
    # the face is only seen from 1000 ms on: 2 relax frames (1000, 1200, 1400 -> 3 would be enough; start at 1200)
    seen = lambda ts: ts >= 1200  # noqa: E731
    status, value, why = phase_reading(frames(lambda ts: 0.1 if ts < 2000 else 0.9, usable=seen))
    assert status == "usable"  # weakest 20 % vs strongest 20 % of all usable frames
    status, value, why = phase_reading(frames(lambda ts: 0.9, usable=seen))
    assert (status, why) == ("not_measured", "already_smiling")


def test_the_session_result_of_a_smile_that_started_early_names_the_reason():
    async def scenario():
        from analysis.session.models import TaskContext

        reader = lambda image, ts: FrameReading(  # noqa: E731
            usable=True, unit=100.0, below_left=1.0, below_right=1.0, corner_height=0.05, smile_strength=0.9,
            timestamp_ms=ts)
        task = StreamSmile(reader, identity_decode, use_executor=False)

        async def stream():
            for i, ms in enumerate(range(0, 5801, 200)):
                yield {"sequence": i, "captured_ms": ms, "format": "jpeg", "data_base64": "AAAA"}

        ctx = TaskContext("call_t", "A1", "smile", {"snapshot_id": "b", "ready": False, "sessions": 0, "needed": 5},
                          asyncio.Event())
        d = (await task.run(ctx, stream(), lambda o: None)).to_dict()
        assert d["outcome"] == "could_not_perform" and d["reading"] is None
        assert d["quality"]["reasons"] == ["already_smiling"]

    asyncio.run(scenario())


def test_the_smile_definition_has_six_seconds_and_the_smile_now_cue():
    d = StreamSmile().definition()
    assert d["capture"]["duration_ms"] == 6000
    assert d["cues"] == [{"at_ms": 1500, "line_id": "smile_now"}]
    assert limits.TASK_WINDOW_S["smile"] == 6.0


def test_the_session_plays_smile_now_into_the_smile_capture():
    async def scenario():
        rig = CallRig()
        await rig.greet()
        result = await rig.run_tool("run_task", {"task": "smile"})
        assert result["outcome"] == "completed"
        said = rig.adapter.said
        assert said.index("task_smile") < said.index("smile_now")
        assert rig.kinds("cue")[0]["line"] == "smile_now" and rig.kinds("cue")[0]["at_ms"] == 1500

    asyncio.run(scenario())


# ---- one repeat for a not-measured attempt, handlers and driver -----------------------------------------------

class AlwaysSmiling(FakeSmile):
    def __call__(self, image, ts_ms):
        r = super().__call__(image, ts_ms)
        r.smile_strength = 0.9
        return r


def test_an_already_smiling_attempt_is_repeated_once_with_a_hint_and_then_skipped():
    async def scenario():
        rig = CallRig()
        rig.session.modules["smile"]._frame_reader = AlwaysSmiling()
        await rig.greet()
        first = await rig.run_tool("run_task", {"task": "smile"})
        assert first["outcome"] == "could_not_perform" and first["reasons"] == ["already_smiling"]
        assert first["retry_hint"] == "already_smiling" and first["retry_hint_text"] == RETRY_HINT_TEXT["already_smiling"]
        assert first["needs_another_reading"] is True and first["attempts_left"] == 1 and first["skip"] is False
        second = await rig.run_tool("run_task", {"task": "smile"})
        assert second["needs_another_reading"] is False and second["skip"] is True and second["attempts_left"] == 0
        assert second["retry_hint"] is None  # nothing is repeated any more
        refused = await rig.handlers.execute("run_task", {"task": "smile"})
        assert refused["error"].startswith("not allowed") and "no attempts are left" in refused["error"]

    asyncio.run(scenario())


def test_the_fixed_order_driver_obeys_the_same_budget():
    async def scenario():
        from analysis.session.run_call import run_call

        rig = CallRig(required_tasks=("smile", "arms"))
        rig.session.modules["smile"]._frame_reader = AlwaysSmiling()
        await rig.drive(run_call(rig.session, rig.adapter, "fixed"))
        assert rig.attempts_by_task["smile"] == 2  # one attempt and ONE repeat, then the smile is skipped
        assert rig.attempts_by_task["arms"] == 1
        assert rig.session.finished is True

    asyncio.run(scenario())


def test_a_usable_outside_reading_has_no_retry_hint():
    async def scenario():
        rig = CallRig(corners=(0.11, 0.09))
        await rig.greet()
        first = await rig.run_tool("run_task", {"task": "smile"})
        assert first["needs_another_reading"] is True and first["retry_hint"] is None
        assert first["retry_hint_text"] is None

    asyncio.run(scenario())


def test_retry_hint_maps_the_reasons_and_unknown_ones_to_other():
    def result(reasons, interruption=None, outcome="completed"):
        return {"outcome": outcome, "quality": {"status": "unusable", "reasons": reasons}, "reading": None,
                "interruption": {"code": interruption, "recoverable": True} if interruption else None}

    assert retry_hint_for("smile", result(["face_not_in_view", "face_not_found"])) == "face_not_in_view"
    assert retry_hint_for("smile", result(["too_few_usable_frames", "face_not_found"])) == "face_not_in_view"
    assert retry_hint_for("smile", result(["already_smiling"], outcome="could_not_perform")) == "already_smiling"
    assert retry_hint_for("smile", result(["no_smile_seen"], outcome="could_not_perform")) == "no_smile_seen"
    assert retry_hint_for("arms", result(["left_hand_not_seen"])) == "hand_not_seen"
    assert retry_hint_for("arms", result(["hand_too_late"])) == "hand_too_late"
    assert retry_hint_for("arms", result(["framing_unusable"], "framing_unusable")) == "hand_not_seen"
    assert retry_hint_for("smile", result(["camera_unavailable"], "camera_unavailable")) == "camera_lost"
    assert retry_hint_for("smile", result(["no_samples"], "no_samples")) == "camera_lost"
    assert retry_hint_for("smile", result(["image_too_dark"])) == "too_dark"
    assert retry_hint_for("smile", result(["something_new"])) == "other"
    assert all(retry_hint_for("smile", result([slug])) in limits.RETRY_HINTS for slug in ("a", "b_c", "reason_x"))
    assert set(RETRY_HINT_TEXT) == set(limits.RETRY_HINTS)


# ---- own words: say_own and ask_followup ---------------------------------------------------------------

def test_say_own_plays_the_text_through_say_text_and_is_logged():
    async def scenario():
        rig = CallRig()
        await rig.greet()
        out = await rig.handlers.execute("say_own", {"text": "Oh, I'm sorry to hear that."})
        assert out == {"said": "own", "played": True}
        assert rig.adapter.own_said == ["Oh, I'm sorry to hear that."]
        assert ("say_text", ("Oh, I'm sorry to hear that.",)) in rig.adapter.client_calls()
        assert rig.kinds("own_line")[0]["text"] == "Oh, I'm sorry to hear that."
        assert "say_own" in rig.handlers.allowed_now() and "ask_followup" not in rig.handlers.allowed_now()

    asyncio.run(scenario())


def test_say_own_before_the_greeting_is_refused():
    async def scenario():
        rig = CallRig()
        out = await rig.handlers.execute("say_own", {"text": "Hello there."})
        assert out["error"].startswith("not allowed") and rig.adapter.own_said == []

    asyncio.run(scenario())


def refused(out):
    return out.get("error", "").startswith("not allowed") and "allowed" in out


def test_say_own_text_guards_refuse_and_play_nothing():
    async def scenario():
        rig = CallRig()
        await rig.greet()
        bad = ["", "   ", "x " * 26, "a" * 201, "You took 2 pills.", "That is 100% fine.",
               "This is not an emergency.", "No Stroke here.", "I detect nothing.", "We will diagnose it.",
               "It is safe.", "The risk is low.", "A medical matter.", "I suspect it.", "Do not predict.",
               "Emergencies happen.", "Stroked it.", "Predictions vary.", "She was diagnosed.", "A strokes risk."]
        for text in bad:
            out = await rig.handlers.execute("say_own", {"text": text})
            assert refused(out), text
        assert rig.adapter.own_said == [] and rig.handlers.own_lines == 0
        # whole words only: "safely"-like cousins aside, ordinary words that merely contain a banned stem are fine
        assert own_text_problem("Take your time, there is no hurry.") is None
        assert own_text_problem("Let's go on with the next one.") is None
        assert own_text_problem("Unsafe-ish") is None  # the word boundary rule: a prefix is another word

    asyncio.run(scenario())


def test_say_own_is_refused_without_say_text_on_the_adapter():
    async def scenario():
        rig = CallRig()
        await rig.greet()
        rig.handlers.adapter = type("NoText", (), {})()
        out = await rig.handlers.execute("say_own", {"text": "Hello."})
        assert refused(out) and "not available" in out["error"]
        assert "say_own" not in rig.handlers.allowed_now()

    asyncio.run(scenario())


def test_say_own_is_refused_while_an_attempt_is_active_and_after_the_call_ended():
    async def scenario():
        rig = CallRig()
        await rig.greet()
        rig.session._active = type("A", (), {"attempt_id": "A1"})()  # an attempt is running
        assert refused(await rig.handlers.execute("say_own", {"text": "Hello."}))
        rig.session._active = None
        rig.session.hang_up()
        assert await rig.handlers.execute("say_own", {"text": "Hello."}) == {"error": "call ended"}
        assert rig.adapter.own_said == []

    asyncio.run(scenario())


def test_say_own_is_refused_while_finish_call_is_in_progress():
    async def scenario():
        rig = CallRig()
        await rig.greet()
        rig.handlers.finishing = True
        out = await rig.handlers.execute("say_own", {"text": "One more thing."})
        assert refused(out) and "ending" in out["error"]

    asyncio.run(scenario())


def test_at_most_ten_own_lines_per_call_say_own_and_ask_followup_together():
    async def scenario():
        rig = CallRig()
        await rig.greet()
        await rig.handlers.execute("ask", {"question_id": "q_feeling"})
        assert (await rig.handlers.execute("ask_followup", {"question_id": "q_feeling", "text": "Tell me more?"}))["heard"]
        for i in range(limits.OWN_LINES_MAX - 1):
            assert "error" not in await rig.handlers.execute("say_own", {"text": "That is fine, thank you."})
        assert len(rig.adapter.own_said) == limits.OWN_LINES_MAX
        out = await rig.handlers.execute("say_own", {"text": "One too many."})
        assert refused(out) and "already used" in out["error"]
        assert "say_own" not in rig.handlers.allowed_now() and len(rig.adapter.own_said) == limits.OWN_LINES_MAX

    asyncio.run(scenario())


def test_ask_followup_plays_listens_and_leaves_the_question_unrecorded():
    async def scenario():
        rig = CallRig(answers={"q_feeling": ["Not great", "My knee hurts a bit"]})
        await rig.greet()
        refused_first = await rig.handlers.execute("ask_followup", {"question_id": "q_feeling", "text": "What hurts?"})
        assert refused(refused_first) and "ask this question first" in refused_first["error"]
        await rig.handlers.execute("ask", {"question_id": "q_feeling"})
        out = await rig.handlers.execute("ask_followup", {"question_id": "q_feeling", "text": "What is bothering you?"})
        assert out == {"heard": True, "transcript": "My knee hurts a bit"}
        assert rig.adapter.own_said == ["What is bothering you?"]
        assert rig.handlers.recorded["q_feeling"] is False
        snap = rig.handlers.snapshot()["questions"]["q_feeling"]
        assert snap["followups"] == 1 and snap["followup_transcript"] == "My knee hurts a bit" and snap["recorded"] is False
        # a second follow-up for the same question is refused
        again = await rig.handlers.execute("ask_followup", {"question_id": "q_feeling", "text": "Anything else?"})
        assert refused(again) and "follow-up" in again["error"]
        # the answer can be recorded after the follow-up
        assert (await rig.handlers.execute("record_answer", {"question_id": "q_feeling",
                                                             "value": "knee hurts"}))["recorded"] is True
        assert rig.session.answers["feeling"] == "knee hurts"
        # a recorded question gets no follow-up
        await rig.handlers.execute("ask", {"question_id": "q_unusual"})
        await rig.handlers.execute("record_answer", {"question_id": "q_unusual", "value": "nothing"})
        late = await rig.handlers.execute("ask_followup", {"question_id": "q_unusual", "text": "Really nothing?"})
        assert refused(late) and "already recorded" in late["error"]

    asyncio.run(scenario())


def test_ask_followup_when_nothing_is_heard_returns_null_transcript():
    async def scenario():
        rig = CallRig(answers={"q_feeling": ["Hm", None]})
        await rig.greet()
        await rig.handlers.execute("ask", {"question_id": "q_feeling"})
        out = await rig.handlers.execute("ask_followup", {"question_id": "q_feeling", "text": "Could you say more?"})
        assert out == {"heard": False, "transcript": None}

    asyncio.run(scenario())


def test_own_lines_are_refused_in_the_last_thirty_seconds():
    async def scenario():
        rig = CallRig()
        await rig.greet()
        await rig.clock.advance(limits.CALL_MAX_S - limits.FINISH_GUARD_S + 1)
        out = await rig.handlers.execute("say_own", {"text": "Almost done."})
        assert refused(out) and "almost over" in out["error"] and rig.adapter.own_said == []

    asyncio.run(scenario())


def test_the_fixed_order_driver_finishes_from_a_state_where_a_followup_happened():
    state = {"ended": False, "greeted": True,
             "questions": {"q_medicine": {"asks": 1, "heard": True, "recorded": True, "transcript": "yes"},
                           "q_feeling": {"asks": 1, "heard": False, "recorded": False, "transcript": "",
                                         "followups": 1, "followup_transcript": "my knee hurts"},
                           "q_unusual": {"asks": 0, "heard": False, "recorded": False, "transcript": ""}},
             "tasks": {t: {"done": True, "skip": False, "needs_another": False, "unimplemented": False,
                           "resource": "camera", "recovery_pending": False} for t in limits.TASKS}}
    tool, args = next_step(state)
    assert tool == "record_answer" and args == {"question_id": "q_feeling", "value": "my knee hurts"}  # no re-ask


def test_a_claude_call_with_say_own_and_ask_followup_runs_to_the_end():
    async def scenario():
        rig = CallRig(required_tasks=("smile", "arms"))
        script = [tool_use_response("say", {"line_id": "greet"}),
                  tool_use_response("ask", {"question_id": "q_medicine"}),
                  tool_use_response("say_own", {"text": "Good, thank you."}),
                  tool_use_response("record_answer", {"question_id": "q_medicine", "value": "yes"}),
                  tool_use_response("ask", {"question_id": "q_feeling"}),
                  tool_use_response("ask_followup", {"question_id": "q_feeling", "text": "How is your sleep?"}),
                  tool_use_response("record_answer", {"question_id": "q_feeling", "value": "fine"}),
                  tool_use_response("run_task", {"task": "smile"}),
                  tool_use_response("say_own", {"text": "Next one is quick."}),
                  tool_use_response("run_task", {"task": "arms"}),
                  tool_use_response("finish_call", {})]
        client = FakeClaudeClient(script, rig.clock)
        agent = ClaudeAgent(rig.session, rig.handlers, client, rig.clock)
        await rig.drive(agent.run())
        assert rig.session.finished and rig.kinds("agent_fallback") == []
        assert rig.adapter.own_said == ["Good, thank you.", "How is your sleep?", "Next one is quick."]
        logged = json.dumps(rig.session.log, default=str)
        assert "I feel fine, thank you." not in logged  # her words are never in the log
        tools = [t["name"] for t in client.requests[0]["tools"]]
        assert "say_own" in tools and "ask_followup" in tools

    asyncio.run(scenario())


def test_the_subscription_client_describes_the_new_tools():
    rig = CallRig()
    section = answer_format_section(rig.handlers.tool_definitions())
    assert '"name":"say_own"' in section and '"name":"ask_followup"' in section


# ---- the terminal lines ----------------------------------------------------------------------------------------

def test_terminal_lines_show_steps_without_transcripts_numbers_or_keys():
    async def scenario():
        rig = CallRig(answers={"q_medicine": "Yes, SECRETWORDS", "q_feeling": "fine", "q_unusual": "no"},
                      corners=(0.01,))
        await rig.greet()
        await rig.handlers.execute("ask", {"question_id": "q_medicine"})
        await rig.handlers.execute("say_own", {"text": "Thank you."})
        await rig.run_tool("run_task", {"task": "smile"})
        lines = [format_terminal_line(e, rig.session) for e in rig.session.log]
        text = "\n".join(l for l in lines if l)
        assert "said: greet" in text and "said: own: Thank you." in text
        assert "answer heard (q_medicine): yes" in text
        assert "attempt A1 of smile ended: completed" in text
        assert "SECRETWORDS" not in text and "value " not in text and "usual " not in text

    asyncio.run(scenario())


def test_the_terminal_line_of_a_not_measured_attempt_has_reasons_and_retry_hint():
    async def scenario():
        rig = CallRig()
        rig.session.modules["smile"]._frame_reader = AlwaysSmiling()
        await rig.greet()
        await rig.run_tool("run_task", {"task": "smile"})
        entry = rig.kinds("attempt_finished")[0]
        line = format_terminal_line(entry, rig.session)
        assert "could_not_perform" in line and "reasons: already_smiling" in line
        assert "retry_hint: already_smiling" in line

    asyncio.run(scenario())


# ---- the self-check ----------------------------------------------------------------------------------------

def test_selfcheck_names_what_is_missing(tmp_path):
    from analysis import selfcheck

    present = tmp_path / "face.task"
    present.write_text("x")

    def importer(name):
        if name in ("mediapipe", "claude_agent_sdk"):
            raise ImportError(name)

    problems = selfcheck.check(import_module=importer, model_paths=[("face", present), ("hand", tmp_path / "no.task")])
    assert len(problems) == 2
    assert "mediapipe" in problems[0] and "uv pip install -r analysis/requirements.txt" in problems[0]
    assert "hand model file is missing" in problems[1] and "python -m analysis.get_models" in problems[1]
    with_claude = selfcheck.check("subscription", importer, [("face", present)])
    assert any("claude_agent_sdk" in p and "uv pip install claude-agent-sdk" in p for p in with_claude)
    assert selfcheck.check("api", importer, [("face", present)]) == problems[:1]


def test_selfcheck_is_ready_when_everything_is_there(tmp_path):
    from analysis import selfcheck

    model = tmp_path / "m.task"
    model.write_text("x")
    assert selfcheck.check(import_module=lambda name: None, model_paths=[("face", model), ("hand", model)]) == []
