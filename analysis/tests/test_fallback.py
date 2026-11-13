"""The fixed-order driver: the pure next_step, the answer parsing and whole calls on fakes. Written, not run."""
from __future__ import annotations

import asyncio

from analysis.session import limits
from analysis.session.fallback import FixedOrderDriver, answer_value, next_step, parse_medicine, step_key
from analysis.tests.agent_helpers import CallRig


def fresh_state():
    return {"ended": False, "greeted": False, "blocked": [],
            "questions": {q: {"asks": 0, "heard": False, "recorded": False, "transcript": ""}
                          for q in limits.QUESTIONS},
            "tasks": {t: {"done": False, "skip": False, "needs_another": True, "unimplemented": t == "sentence",
                          "resource": limits.TASK_RESOURCE[t], "recovery_pending": False} for t in limits.TASKS}}


def after_questions():
    s = fresh_state()
    s["greeted"] = True
    for q in s["questions"].values():
        q.update(asks=1, heard=True, recorded=True)
    return s


# ---- answer parsing ----------------------------------------------------------------------------------

def test_medicine_keywords():
    assert parse_medicine("Yes, after breakfast.") == "yes"
    assert parse_medicine("yeah I did") == "yes"
    assert parse_medicine("I took them this morning") == "yes"
    assert parse_medicine("No, I haven't") == "no"
    assert parse_medicine("not yet") == "no"
    assert parse_medicine("I have not taken it") == "no"  # a negative word wins over "have" and "taken"
    assert parse_medicine("I forgot") == "no"
    assert parse_medicine("") == "unclear"
    assert parse_medicine("What time is it?") == "unclear"
    assert parse_medicine("I'm not sure") == "unclear"
    assert parse_medicine("no problem, yes I did") == "unclear"  # an explicit yes next to a no


def test_free_text_answers_are_cut_to_200_characters():
    assert answer_value("q_feeling", "x" * 500) == "x" * 200
    assert answer_value("q_unusual", "") == ""
    assert answer_value("q_medicine", "yes please") == "yes"


# ---- next_step from different states -----------------------------------------------------------------

def test_fresh_state_starts_with_the_greeting():
    assert next_step(fresh_state()) == ("say", {"line_id": "greet"})


def test_questions_are_asked_once_and_re_asked_once_when_not_heard():
    s = fresh_state()
    s["greeted"] = True
    assert next_step(s) == ("ask", {"question_id": "q_medicine"})
    s["questions"]["q_medicine"].update(asks=1, heard=False)
    assert next_step(s) == ("ask", {"question_id": "q_medicine"})  # the one re-ask
    s["questions"]["q_medicine"].update(asks=2, heard=False)
    assert next_step(s) == ("record_answer", {"question_id": "q_medicine", "value": "unclear"})
    s["questions"]["q_medicine"].update(asks=2, heard=True, transcript="No.")
    assert next_step(s) == ("record_answer", {"question_id": "q_medicine", "value": "no"})
    s["questions"]["q_medicine"].update(recorded=True)
    assert next_step(s) == ("ask", {"question_id": "q_feeling"})


def test_a_heard_answer_is_recorded_without_asking_again():
    s = fresh_state()
    s["greeted"] = True
    s["questions"]["q_feeling"].update(asks=1, heard=True, transcript="y" * 300)
    s["questions"]["q_medicine"].update(recorded=True)
    assert next_step(s) == ("record_answer", {"question_id": "q_feeling", "value": "y" * 200})


def test_tasks_run_in_order_then_finish_and_the_unimplemented_sentence_is_skipped():
    s = after_questions()
    assert next_step(s) == ("run_task", {"task": "smile"})
    s["tasks"]["smile"]["done"] = True
    assert next_step(s) == ("run_task", {"task": "arms"})
    s["tasks"]["arms"]["done"] = True
    assert next_step(s) == ("finish_call", {})  # sentence is not implemented: skipped
    s["ended"] = True
    assert next_step(s) == (None, {})


def test_a_task_that_needs_another_reading_is_run_again():
    s = after_questions()
    s["tasks"]["smile"].update(done=False, needs_another=True)
    assert next_step(s) == ("run_task", {"task": "smile"})
    s["tasks"]["smile"].update(skip=True)  # no budget left
    assert next_step(s) == ("run_task", {"task": "arms"})


def test_a_recoverable_interruption_recovers_once_then_repeats_or_skips():
    s = after_questions()
    s["tasks"]["smile"]["done"] = True
    s["tasks"]["arms"].update(recovery_pending=True)
    assert next_step(s) == ("recover_resource", {"resource": "camera"})
    s["tasks"]["arms"].update(recovery_pending=False)  # restored: the camera is back, the task is simply open
    assert next_step(s) == ("run_task", {"task": "arms"})
    s["tasks"]["arms"].update(skip=True)  # not restored: the resource is unavailable, the handlers say skip
    assert next_step(s) == ("finish_call", {})


def test_blocked_steps_are_not_tried_again():
    s = after_questions()
    s["blocked"] = [step_key("run_task", {"task": "smile"})]
    assert next_step(s) == ("run_task", {"task": "arms"})
    s["blocked"].append(step_key("run_task", {"task": "arms"}))
    assert next_step(s) == ("finish_call", {})
    s = fresh_state()
    s["blocked"] = [step_key("say", {"line_id": "greet"})]
    assert next_step(s) == ("finish_call", {})


def test_it_can_take_over_mid_call_from_the_handlers_state():
    async def scenario():
        rig = CallRig()
        h = rig.handlers
        await h.execute("say", {"line_id": "greet"})
        await h.execute("ask", {"question_id": "q_medicine"})
        await h.execute("record_answer", {"question_id": "q_medicine", "value": "yes"})
        assert next_step(h.snapshot()) == ("ask", {"question_id": "q_feeling"})
        await rig.drive(FixedOrderDriver(h).run())
        assert rig.adapter.said.count("greet") == 1 and rig.adapter.said.count("q_medicine") == 1
        assert rig.session.finished is True

    asyncio.run(scenario())


# ---- whole calls ------------------------------------------------------------------------------------

def test_a_whole_call_with_a_camera_loss_during_the_first_arms_attempt():
    async def scenario():
        rig = CallRig()
        await rig.drive(FixedOrderDriver(rig.handlers).run(), lose_camera_in_first_arms=True)
        said = rig.adapter.said
        order = ["greet", "q_medicine", "q_feeling", "q_unusual", "setup_phone", "task_smile", "task_arms",
                 "camera_unavailable", "repeat_arms", "bye"]
        positions = [said.index(line) for line in order]
        assert positions == sorted(positions), said
        assert "task_sentence" not in said  # not implemented: never played
        assert rig.session.answers["medicine"] == "yes" and rig.session.answers["feeling"] == "I feel fine, thank you."
        assert [a.state for a in rig.session.task_attempts["arms"]] == ["interrupted", "completed"]
        assert rig.session.finished is True and rig.adapter.published[-1]["phase"] == "final"
        assert rig.session.build_session_result().to_dict()["status"] == "incomplete"  # sentence has no reading
        assert set(rig.session.baseline_update()) == {"corner_height_v1", "arms.hand_left.hand_sink",
                                                      "arms.hand_right.hand_sink"}

    asyncio.run(scenario())


def test_not_heard_twice_records_unclear_and_empty():
    async def scenario():
        rig = CallRig(answers={})
        await rig.drive(FixedOrderDriver(rig.handlers).run())
        assert rig.adapter.said.count("q_medicine") == 2 and rig.adapter.said.count("q_feeling") == 2
        assert rig.session.answers == {"medicine": "unclear", "feeling": "", "unusual": ""}

    asyncio.run(scenario())


def test_re_ask_that_is_heard_the_second_time_is_recorded():
    async def scenario():
        rig = CallRig(answers={"q_medicine": [None, "No, not yet."]})
        await rig.drive(FixedOrderDriver(rig.handlers).run())
        assert rig.adapter.said.count("q_medicine") == 2 and rig.session.answers["medicine"] == "no"

    asyncio.run(scenario())


def test_a_first_outside_smile_is_repeated_and_two_outside_smiles_publish_once():
    async def scenario():
        rig = CallRig(corners=(0.11, 0.09))
        await rig.drive(FixedOrderDriver(rig.handlers).run())
        assert len(rig.session.task_attempts["smile"]) == 2
        assert [p["phase"] for p in rig.adapter.published] == ["mid_call", "final"]
        assert rig.adapter.published[0]["summary"]["status"] == "change_noticed"
        assert rig.session.baseline_update() == {}  # a confirmed change never enters the baseline

    asyncio.run(scenario())


def test_outside_then_within_is_repeated_once_and_not_confirmed():
    async def scenario():
        rig = CallRig(corners=(0.11, 0.01))
        await rig.drive(FixedOrderDriver(rig.handlers).run())
        assert len(rig.session.task_attempts["smile"]) == 2
        assert [p["phase"] for p in rig.adapter.published] == ["final"]

    asyncio.run(scenario())


def test_left_then_right_hand_outside_is_not_confirmed():
    async def scenario():
        rig = CallRig(sinks=({"left": 0.4}, {"right": 0.4}))
        await rig.drive(FixedOrderDriver(rig.handlers).run())
        assert len(rig.session.task_attempts["arms"]) == 2  # the first outside reading asked for another
        assert [p["phase"] for p in rig.adapter.published] == ["final"]  # different sides: no mid-call message

    asyncio.run(scenario())


def test_left_hand_outside_twice_is_confirmed_on_the_left():
    async def scenario():
        rig = CallRig(sinks=({"left": 0.4}, {"left": 0.3}))
        await rig.drive(FixedOrderDriver(rig.handlers).run())
        mid = rig.adapter.published[0]
        assert mid["phase"] == "mid_call" and mid["summary"]["status"] == "change_noticed"
        sign = next(s for s in mid["summary"]["signs"] if s["sign"] == "arm")
        assert sign["confirmed"] is True and sign["side"] == "left"

    asyncio.run(scenario())


def test_a_failed_recovery_skips_arms_and_the_call_still_ends():
    async def scenario():
        rig = CallRig()
        rig.restore_camera = False
        await rig.drive(FixedOrderDriver(rig.handlers).run(), lose_camera_in_first_arms=True)
        assert len(rig.session.task_attempts["arms"]) == 1 and rig.adapter.said.count("camera_unavailable") == 1
        assert rig.session.finished is True and rig.adapter.said[-1] == "bye"

    asyncio.run(scenario())


def test_after_hang_up_the_driver_stops_and_nothing_reaches_the_client():
    async def scenario():
        rig = CallRig()
        rig.session.hang_up()
        calls = list(rig.adapter.calls)
        await FixedOrderDriver(rig.handlers).run()
        assert rig.adapter.calls == calls

    asyncio.run(scenario())
