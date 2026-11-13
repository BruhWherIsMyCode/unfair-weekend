"""The shared tool handlers: every guard, with a fake clock, fake adapter and fake readers. Written, not run."""
from __future__ import annotations

import asyncio
import json

from analysis.session import limits
from analysis.session.agent_tools import TOOL_NAMES
from analysis.tasks.stream_base import NOT_IMPLEMENTED
from analysis.tests.agent_helpers import FACT_KEYS, CallRig


def not_allowed(result):
    return isinstance(result.get("error"), str) and result["error"].startswith("not allowed") and "allowed" in result


# ---- say / ask / record_answer ------------------------------------------------------------------------

def test_greet_is_the_first_step_and_only_once():
    async def scenario():
        rig = CallRig()
        h = rig.handlers
        refused = await h.execute("ask", {"question_id": "q_medicine"})
        assert not_allowed(refused) and refused["allowed"] == ["say"]
        assert (await h.execute("say", {"line_id": "setup_phone"}))["error"].startswith("not allowed")
        assert await h.execute("say", {"line_id": "greet"}) == {"said": "greet", "played": True}
        again = await h.execute("say", {"line_id": "greet"})
        assert not_allowed(again)
        assert rig.adapter.said == ["greet"]

    asyncio.run(scenario())


def test_say_accepts_only_greet_and_setup_phone_and_setup_only_once():
    async def scenario():
        rig = CallRig()
        h = rig.handlers
        await rig.greet()
        assert (await h.execute("say", {"line_id": "bye"}))["error"].startswith("invalid input")
        assert (await h.execute("say", {"line_id": "task_smile"}))["error"].startswith("invalid input")
        assert "error" not in await h.execute("say", {"line_id": "setup_phone"})
        assert not_allowed(await h.execute("say", {"line_id": "setup_phone"}))
        assert rig.adapter.said == ["greet", "setup_phone"]

    asyncio.run(scenario())


def test_ask_plays_the_question_and_returns_what_she_said():
    async def scenario():
        rig = CallRig()
        await rig.greet()
        r = await rig.handlers.execute("ask", {"question_id": "q_medicine"})
        assert r == {"question_id": "q_medicine", "heard": True, "transcript": "Yes, after breakfast."}
        assert rig.adapter.said[-1] == "q_medicine" and ("listen", ("q_medicine",)) in rig.adapter.calls
        # her answer was heard: asking again is refused, recording it is allowed
        assert not_allowed(await rig.handlers.execute("ask", {"question_id": "q_medicine"}))

    asyncio.run(scenario())


def test_ask_at_most_two_times_per_question():
    async def scenario():
        rig = CallRig(answers={"q_feeling": [None, None]})
        await rig.greet()
        h = rig.handlers
        for _ in range(2):
            r = await h.execute("ask", {"question_id": "q_feeling"})
            assert r == {"question_id": "q_feeling", "heard": False, "transcript": ""}
        third = await h.execute("ask", {"question_id": "q_feeling"})
        assert not_allowed(third) and "2" in third["error"]
        assert rig.adapter.said.count("q_feeling") == 2

    asyncio.run(scenario())


def test_a_second_ask_can_be_heard():
    async def scenario():
        rig = CallRig(answers={"q_feeling": [None, "A bit tired."]})
        await rig.greet()
        assert (await rig.handlers.execute("ask", {"question_id": "q_feeling"}))["heard"] is False
        r = await rig.handlers.execute("ask", {"question_id": "q_feeling"})
        assert r["heard"] is True and r["transcript"] == "A bit tired."

    asyncio.run(scenario())


def test_record_answer_checks_the_medicine_enum_and_the_length():
    async def scenario():
        rig = CallRig()
        h = rig.handlers
        await rig.greet()
        assert not_allowed(await h.execute("record_answer", {"question_id": "q_medicine", "value": "yes"}))  # not asked
        await h.execute("ask", {"question_id": "q_medicine"})
        await h.execute("ask", {"question_id": "q_feeling"})
        bad = await h.execute("record_answer", {"question_id": "q_medicine", "value": "maybe"})
        assert bad["error"].startswith("invalid input")
        too_long = await h.execute("record_answer", {"question_id": "q_feeling", "value": "x" * 201})
        assert too_long["error"].startswith("invalid input")
        assert rig.session.answers == {"medicine": "unclear", "feeling": "", "unusual": ""}
        assert await h.execute("record_answer", {"question_id": "q_medicine", "value": "yes"}) == {
            "recorded": True, "question_id": "q_medicine"}
        assert "error" not in await h.execute("record_answer", {"question_id": "q_feeling", "value": "x" * 200})
        assert rig.session.answers["medicine"] == "yes" and rig.session.answers["feeling"] == "x" * 200

    asyncio.run(scenario())


def test_unknown_tool_and_wrong_input_shapes_are_invalid_input():
    async def scenario():
        rig = CallRig()
        h = rig.handlers
        assert (await h.execute("fly", {}))["error"] == "invalid input: unknown tool"
        assert (await h.execute("say", "greet"))["error"].startswith("invalid input")
        assert (await h.execute("say", {"line_id": "greet", "extra": 1}))["error"].startswith("invalid input")
        assert (await h.execute("run_task", {"task": "walk"}))["error"].startswith("invalid input")
        assert (await h.execute("finish_call", {"reason": "bored"}))["error"].startswith("invalid input")

    asyncio.run(scenario())


# ---- run_task ---------------------------------------------------------------------------------------

def test_setup_phone_is_played_before_the_first_video_task_and_not_again():
    async def scenario():
        rig = CallRig()
        await rig.greet()
        r = await rig.run_tool("run_task", {"task": "smile"})
        assert r["outcome"] == "completed"
        assert rig.adapter.said[:3] == ["greet", "setup_phone", "task_smile"]
        r2 = await rig.run_tool("run_task", {"task": "arms"})
        assert r2["outcome"] == "completed" and rig.adapter.said.count("setup_phone") == 1
        assert "swap_hands" in rig.adapter.said  # the mid-task cue of the one-hand arms task
        assert not_allowed(await rig.handlers.execute("say", {"line_id": "setup_phone"}))

    asyncio.run(scenario())


def test_run_task_returns_plain_facts_only():
    async def scenario():
        rig = CallRig(corners=(0.11, 0.09))  # both smile readings outside her usual
        await rig.greet()
        first = await rig.run_tool("run_task", {"task": "smile"})
        assert set(first) == FACT_KEYS
        assert first["outcome"] == "completed" and first["quality"] == "usable" and first["reasons"] == []
        assert first["interruption"] is None and first["attempts_left"] == 2
        assert first["needs_another_reading"] is True and first["skip"] is False
        assert first["resources"] == {"camera": "available", "microphone": "available"}
        second = await rig.run_tool("run_task", {"task": "smile"})  # confirms: the family message goes out in code
        assert set(second) == FACT_KEYS
        assert second["needs_another_reading"] is False and second["attempts_left"] == 0 and second["skip"] is True
        assert len(rig.adapter.published) == 1
        for facts in (first, second):
            text = json.dumps(facts).lower()
            # v7: value / usual range / outside flag / side are now part of the facts; only the family message,
            # the confirmation and the metric names stay hidden from Claude
            for word in ("family", "message", "confirm", "alert", "left_hand", "sink", "corner"):
                assert word not in text, word
        assert not_allowed(await rig.handlers.execute("run_task", {"task": "smile"}))

    asyncio.run(scenario())


def test_a_within_usual_first_reading_needs_nothing_more_and_cannot_be_repeated():
    async def scenario():
        rig = CallRig()
        await rig.greet()
        r = await rig.run_tool("run_task", {"task": "smile"})
        assert r["needs_another_reading"] is False and r["attempts_left"] == 2 and r["skip"] is False
        refused = await rig.handlers.execute("run_task", {"task": "smile"})
        assert not_allowed(refused) and "already has its result" in refused["error"]

    asyncio.run(scenario())


def test_an_interruption_is_reported_as_plain_facts_and_recovery_is_offered():
    async def scenario():
        rig = CallRig()
        await rig.greet()
        r = await rig.run_tool("run_task", {"task": "arms"}, lose_camera_in_first_arms=True)
        assert set(r) == FACT_KEYS and r["outcome"] == "interrupted" and r["quality"] == "unusable"
        assert r["interruption"] == {"code": "camera_unavailable", "recoverable": True}
        assert r["resources"]["camera"] == "unavailable"
        assert r["needs_another_reading"] is True and r["skip"] is False and r["attempts_left"] == 2
        h = rig.handlers
        assert "recover_resource" in h.allowed_now()
        refused = await h.execute("run_task", {"task": "arms"})
        assert not_allowed(refused) and "recover_resource" in refused["error"]
        restored = await rig.run_tool("recover_resource", {"resource": "camera"})
        assert restored == {"resource": "camera", "result": "restored"}
        assert not_allowed(await h.execute("recover_resource", {"resource": "camera"}))  # once per interruption
        again = await rig.run_tool("run_task", {"task": "arms"})
        assert again["outcome"] == "completed" and again["needs_another_reading"] is False

    asyncio.run(scenario())


def test_after_a_failed_recovery_the_task_is_skipped():
    async def scenario():
        rig = CallRig()
        rig.restore_camera = False
        await rig.greet()
        await rig.run_tool("run_task", {"task": "arms"}, lose_camera_in_first_arms=True)
        r = await rig.run_tool("recover_resource", {"resource": "camera"})
        assert r == {"resource": "camera", "result": "unavailable"}
        h = rig.handlers
        assert h.skip("arms") is True and "recover_resource" not in h.allowed_now()
        refused = await h.execute("run_task", {"task": "arms"})
        assert not_allowed(refused) and "camera is not available" in refused["error"]

    asyncio.run(scenario())


def test_not_implemented_sentence_returns_skip_and_is_never_retried():
    async def scenario():
        rig = CallRig()
        await rig.greet()
        said_before = list(rig.adapter.said)
        r = await rig.handlers.execute("run_task", {"task": "sentence"})
        assert set(r) == FACT_KEYS and r["skip"] is True and r["reasons"] == [NOT_IMPLEMENTED]
        assert r["needs_another_reading"] is False and r["attempts_left"] == 0
        assert rig.adapter.said == said_before  # no instruction was played for a check that cannot run
        assert not_allowed(await rig.handlers.execute("run_task", {"task": "sentence"}))

    asyncio.run(scenario())


# ---- finish_call -------------------------------------------------------------------------------------

def test_finish_is_refused_while_a_required_task_is_open():
    async def scenario():
        rig = CallRig()
        await rig.greet()
        r = await rig.handlers.execute("finish_call", {})
        assert not_allowed(r) and "smile" in r["error"] and "run_task" in r["allowed"]
        assert "finish_call" not in r["allowed"]
        assert rig.session.finished is False and "bye" not in rig.adapter.said and rig.adapter.published == []

    asyncio.run(scenario())


def test_stop_words_in_an_answer_do_not_end_the_call():
    async def scenario():
        rig = CallRig(answers={"q_feeling": "I want to stop now."})
        await rig.greet()
        await rig.handlers.execute("ask", {"question_id": "q_feeling"})
        await rig.handlers.execute("record_answer", {"question_id": "q_feeling", "value": "I want to stop now."})
        assert not_allowed(await rig.handlers.execute("finish_call", {}))

    asyncio.run(scenario())


def test_finish_is_allowed_when_she_asked_to_stop_and_it_is_logged():
    async def scenario():
        rig = CallRig()
        await rig.greet()
        r = await rig.handlers.execute("finish_call", {"reason": "she_asked_to_stop"})
        assert r == {"finished": True} and rig.session.finished is True
        assert rig.adapter.said[-1] == "bye" and rig.kinds("finish_reason")[0]["reason"] == "she_asked_to_stop"
        assert [p["phase"] for p in rig.adapter.published][-1] == "final"

    asyncio.run(scenario())


def test_finish_is_allowed_when_less_than_30_s_of_the_call_remain():
    async def scenario():
        rig = CallRig()
        await rig.greet()
        await rig.clock.advance(limits.CALL_MAX_S - limits.FINISH_GUARD_S - 5.0)
        assert not_allowed(await rig.handlers.execute("finish_call", {}))
        await rig.clock.advance(10.0)
        assert await rig.handlers.execute("finish_call", {}) == {"finished": True}

    asyncio.run(scenario())


def test_finish_is_allowed_once_the_required_tasks_have_their_results():
    async def scenario():
        rig = CallRig(required_tasks=("smile", "arms"))
        await rig.greet()
        await rig.run_tool("run_task", {"task": "smile"})
        assert not_allowed(await rig.handlers.execute("finish_call", {}))  # arms still open
        await rig.run_tool("run_task", {"task": "arms"})
        assert "finish_call" in rig.handlers.allowed_now()
        assert await rig.handlers.execute("finish_call", {}) == {"finished": True}
        assert await rig.handlers.execute("say", {"line_id": "greet"}) == {"error": "call ended"}

    asyncio.run(scenario())


# ---- refusals and hang-up -----------------------------------------------------------------------------

def test_refused_steps_change_nothing():
    async def scenario():
        rig = CallRig()
        await rig.greet()
        calls, log_len, answers = list(rig.adapter.calls), len(rig.session.log), dict(rig.session.answers)
        refused = [("say", {"line_id": "greet"}), ("record_answer", {"question_id": "q_feeling", "value": "fine"}),
                   ("recover_resource", {"resource": "camera"}), ("finish_call", {})]
        for name, args in refused:
            result = await rig.handlers.execute(name, args)
            assert not_allowed(result), name
            assert set(result["allowed"]) <= set(TOOL_NAMES)
        assert rig.adapter.calls == calls and len(rig.session.log) == log_len and rig.session.answers == answers
        assert rig.handlers.asks == {q: 0 for q in limits.QUESTIONS}

    asyncio.run(scenario())


def test_after_hang_up_every_tool_says_call_ended_and_nothing_reaches_the_client():
    async def scenario():
        rig = CallRig()
        await rig.greet()
        rig.session.hang_up()
        calls = list(rig.adapter.calls)
        steps = [("say", {"line_id": "setup_phone"}), ("ask", {"question_id": "q_medicine"}),
                 ("record_answer", {"question_id": "q_medicine", "value": "yes"}),
                 ("run_task", {"task": "smile"}), ("recover_resource", {"resource": "camera"}),
                 ("finish_call", {}), ("fly", {})]
        for name, args in steps:
            assert await rig.handlers.execute(name, args) == {"error": "call ended"}, name
        assert rig.adapter.calls == calls and rig.handlers.allowed_now() == []

    asyncio.run(scenario())


def test_hang_up_while_a_question_is_playing_stops_before_listening():
    async def scenario():
        rig = CallRig()
        await rig.greet()
        rig.adapter.say_gate = asyncio.Event()
        task = asyncio.ensure_future(rig.handlers.execute("ask", {"question_id": "q_medicine"}))
        await rig.clock.settle()
        rig.session.hang_up()
        rig.adapter.say_gate.set()
        assert await task == {"error": "call ended"}
        assert ("listen", ("q_medicine",)) not in rig.adapter.calls

    asyncio.run(scenario())


# ---- tool definitions -----------------------------------------------------------------------------------

def test_tool_definitions_match_the_script_and_the_limits():
    rig = CallRig()
    tools = {t["name"]: t for t in rig.handlers.tool_definitions()}
    assert tuple(tools) == TOOL_NAMES
    for t in tools.values():
        assert t["description"] and t["input_schema"]["type"] == "object"
    props = lambda name: tools[name]["input_schema"]["properties"]  # noqa: E731
    assert props("say")["line_id"]["enum"] == ["greet", "setup_phone"]
    assert props("ask")["question_id"]["enum"] == ["q_medicine", "q_feeling", "q_unusual"]
    assert set(props("ask")["question_id"]["enum"]) <= set(rig.handlers.script)
    assert props("record_answer")["value"]["maxLength"] == 200
    assert props("run_task")["task"]["enum"] == ["smile", "arms", "sentence"]
    assert props("recover_resource")["resource"]["enum"] == ["camera", "microphone"]
    assert props("finish_call")["reason"]["enum"] == ["she_asked_to_stop"]
    assert tools["finish_call"]["input_schema"].get("required") is None
    json.dumps(list(tools.values()))  # plain JSON
