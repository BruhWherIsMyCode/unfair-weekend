"""v7: the numbers Claude gets from run_task, the alert_family tool, the concern_raised status, the required tasks."""
from __future__ import annotations

import pytest

import asyncio
import json

from analysis.session import limits, rules
from analysis.session.adapters import render_family_text
from analysis.session.call_session import CallSession
from analysis.session.fallback import FixedOrderDriver
from analysis.session.models import STATUSES
from analysis.tests.agent_helpers import CallRig

READING_KEYS = {"value", "usual_low", "usual_high", "outside_usual", "distance", "side", "unit"}


def not_allowed(r):
    return isinstance(r.get("error"), str) and r["error"].startswith("not allowed")


def test_reading_fields_and_distance_maths_for_an_outside_value():
    async def scenario():
        rig = CallRig(corners=(0.11,))
        await rig.greet()
        r = await rig.run_tool("run_task", {"task": "smile"})
        assert r["baseline_ready"] is True and set(r["reading"]) == READING_KEYS
        x = r["reading"]
        assert x["usual_low"] == -0.02 and x["usual_high"] == 0.03 and x["outside_usual"] is True
        assert x["side"] == "left" and x["unit"] == "eye_distance"
        assert x["value"] == round(x["value"], 3)
        half = (0.03 - -0.02) / 2
        assert abs(x["distance"] - round((x["value"] - 0.03) / half, 2)) < 0.05 and x["distance"] > 2
        assert json.dumps(r)

    asyncio.run(scenario())


def test_inside_value_has_distance_zero():
    async def scenario():
        rig = CallRig(corners=(0.01,))
        await rig.greet()
        x = (await rig.run_tool("run_task", {"task": "smile"}))["reading"]
        assert x["outside_usual"] is False and x["distance"] == 0.0 and x["side"] is None

    asyncio.run(scenario())


def test_learning_gives_a_value_without_range_and_distance_zero():
    async def scenario():
        rig = CallRig(smile_baseline=None)
        await rig.greet()
        r = await rig.run_tool("run_task", {"task": "smile"})
        x = r["reading"]
        assert r["baseline_ready"] is False
        assert x["usual_low"] is None and x["usual_high"] is None and x["outside_usual"] is None
        assert x["distance"] == 0.0 and x["value"] is not None

    asyncio.run(scenario())


def test_reading_is_null_when_nothing_usable_was_measured():
    async def scenario():
        rig = CallRig()
        await rig.greet()
        r = await rig.run_tool("run_task", {"task": "arms"}, lose_camera_in_first_arms=True)
        assert r["outcome"] == "interrupted" and r["reading"] is None
        s = await rig.handlers.execute("run_task", {"task": "sentence"})
        assert s["reading"] is None and s["baseline_ready"] is False

    asyncio.run(scenario())


def test_arms_reading_names_the_hand():
    async def scenario():
        rig = CallRig(sinks=({"left": 0.4},))
        await rig.greet()
        x = (await rig.run_tool("run_task", {"task": "arms"}))["reading"]
        assert x["side"] == "left" and x["outside_usual"] is True and x["distance"] == 3.0  # (0.4 - 0.1) / 0.1

    asyncio.run(scenario())


# ---- alert_family ------------------------------------------------------------------------------------------------

@pytest.mark.usefixtures("alert_mid_call")
def test_alert_family_guards_and_effect():
    async def scenario():
        rig = CallRig()
        h = rig.handlers
        args = {"reason": "asked_for_help", "note": "She asked for her daughter."}
        assert not_allowed(await h.execute("alert_family", args))  # before the greeting
        await rig.greet()
        assert "alert_family" in h.allowed_now()
        assert (await h.execute("alert_family", {"reason": "panic", "note": "x"}))["error"].startswith("invalid input")
        assert (await h.execute("alert_family", dict(args, extra=1)))["error"].startswith("invalid input")
        assert await h.execute("alert_family", args) == {"sent": True}
        s = rig.session
        assert s.concern == {"raised_by": "agent", "reason": "asked_for_help", "note": "She asked for her daughter."}
        assert [p["phase"] for p in rig.adapter.published] == ["mid_call"]
        assert rig.adapter.published[0]["summary"]["status"] == "concern_raised"
        assert "she asked for help" in rig.adapter.published[0]["text"]
        assert await h.execute("alert_family", dict(args, reason="other")) == {"sent": True}
        assert [p["phase"] for p in rig.adapter.published] == ["mid_call"]  # published mid-call only once
        third = await h.execute("alert_family", args)
        assert not_allowed(third) and "alert_family" not in h.allowed_now()
        final = await s.finish()
        assert final["status"] == "concern_raised" and final["concern"]["reason"] == "other"
        assert [p["phase"] for p in rig.adapter.published] == ["mid_call", "final"]
        assert len({p["message_id"] for p in rig.adapter.published}) == 1

    asyncio.run(scenario())


@pytest.mark.usefixtures("alert_mid_call")
def test_alert_note_is_cut_and_never_logged_in_full():
    async def scenario():
        rig = CallRig()
        await rig.greet()
        long_note = "x" * 500
        assert await rig.handlers.execute("alert_family", {"reason": "other", "note": long_note}) == {"sent": True}
        assert len(rig.session.concern["note"]) == limits.ALERT_NOTE_MAX_CHARS
        assert rig.kinds("alert_note_cut") and rig.kinds("concern_note_cut") == []
        assert "x" * 50 not in json.dumps(rig.session.log)

    asyncio.run(scenario())


def test_alert_family_after_hang_up_is_refused():
    async def scenario():
        rig = CallRig()
        await rig.greet()
        rig.session.hang_up()
        r = await rig.handlers.execute("alert_family", {"reason": "other", "note": "x"})
        assert r == {"error": "call ended"} and rig.session.concern is None

    asyncio.run(scenario())


def test_the_fixed_order_driver_never_alerts():
    async def scenario():
        rig = CallRig(required_tasks=limits.REQUIRED_TASKS, corners=(0.11, 0.09))
        await rig.drive(FixedOrderDriver(rig.handlers).run())
        assert rig.session.concern is None and rig.handlers.alerts == 0
        assert not any(e["kind"] == "concern_raised" for e in rig.session.log)

    asyncio.run(scenario())


@pytest.mark.usefixtures("alert_mid_call")
def test_a_confirmed_change_beats_a_concern_and_both_are_published():
    async def scenario():
        rig = CallRig(corners=(0.11, 0.09), required_tasks=("smile",))  # v12: no arms to wait for: the same-task repeat
        await rig.greet()
        assert await rig.handlers.execute("alert_family", {"reason": "other", "note": "n"}) == {"sent": True}
        await rig.run_tool("run_task", {"task": "smile"})
        await rig.run_tool("run_task", {"task": "smile"})
        assert [p["status"] for p in rig.session.published] == ["concern_raised", "change_noticed"]
        assert (await rig.session.finish())["status"] == "change_noticed"

    asyncio.run(scenario())


def test_status_order_and_family_text():
    assert "concern_raised" in STATUSES
    ev = {"smile": rules.SignEvaluation([], False, None, None, None)}
    assert rules.session_status(ev, ("smile",), concern=True) == "concern_raised"
    assert rules.session_status(ev, ("smile",)) == "incomplete"
    for reason, words in (("said_unwell", "she said she does not feel well"), ("asked_for_help", "she asked for help"),
                          ("confused_answers", "her answers were confused"),
                          ("reading_far_outside", "one check was far from her usual"), ("other", "something else")):
        text = render_family_text({"status": "concern_raised", "concern": {"reason": reason}}, "Ona")
        assert text == ("Ona said or showed something during today's check-in that needs a look (%s). "
                        "Please call her. If she seems unwell, call 112." % words)


def test_tool_definition_for_alert_family():
    rig = CallRig()
    tool = {t["name"]: t for t in rig.handlers.tool_definitions()}["alert_family"]
    props = tool["input_schema"]["properties"]
    assert props["reason"]["enum"] == list(limits.ALERT_REASONS) and props["note"]["maxLength"] == 200


# ---- required tasks ----------------------------------------------------------------------------------------------

def test_required_tasks_are_smile_and_arms_and_a_full_call_is_as_usual():
    assert limits.REQUIRED_TASKS == ("smile", "arms")

    async def scenario():
        rig = CallRig(required_tasks=limits.REQUIRED_TASKS)
        await rig.drive(FixedOrderDriver(rig.handlers).run())
        assert rig.session.finished and rig.session.build_session_result().to_dict()["status"] == "as_usual"
        assert "task_sentence" not in rig.adapter.said

    asyncio.run(scenario())
    session = CallSession("c", "p", "Ona", None, {})
    assert session.required_tasks == limits.REQUIRED_TASKS and session.safety_floor is True


# ---- v14: the family is told only when the call finishes -----------------------------------------------------------

def test_v14_switches_are_on_by_default_and_the_tool_says_when_the_family_is_told():
    assert limits.CONCERN_ALERT_AT_END is True and limits.CHANGE_ALERT_AT_END is True
    rig = CallRig()
    tool = {t["name"]: t for t in rig.handlers.tool_definitions()}["alert_family"]
    assert "when the call finishes" in tool["description"] and "not now" in tool["description"]


def test_a_concern_during_the_questions_is_told_once_at_the_end_then_reassurance_then_bye():
    async def scenario():
        rig = CallRig()
        await rig.greet()
        result = await rig.handlers.execute("alert_family", {"reason": "said_unwell", "note": "arm feels numb"})
        assert result == {"recorded": True, "family_told": "when_the_call_finishes"}
        assert rig.session.concern["reason"] == "said_unwell"
        assert rig.kinds("concern_raised") and rig.kinds("concern_pending")
        assert rig.adapter.published == [] and rig.adapter.own_said == []  # nothing before finish()
        assert not any(c[0] in ("publish", "say_text") for c in rig.adapter.calls)
        final = await rig.session.finish()
        assert final["status"] == "concern_raised"
        assert [p["phase"] for p in rig.adapter.published] == ["final"]
        assert rig.adapter.published[0]["summary"]["status"] == "concern_raised"
        order = [c[0] if c[0] != "say" else c[1][0] for c in rig.adapter.calls if c[0] in ("publish", "say_text", "say")]
        assert order[-3:] == ["publish", "say_text", "bye"]
        assert rig.adapter.own_said == [limits.ALERT_REASSURANCE]

    asyncio.run(scenario())


def test_a_confirmed_change_and_a_concern_in_one_call_make_one_final_publish_and_one_reassurance():
    async def scenario():
        rig = CallRig(corners=(0.11,), required_tasks=("smile",))
        await rig.greet()
        await rig.handlers.execute("alert_family", {"reason": "other", "note": "n"})
        await rig.run_tool("run_task", {"task": "smile"})
        assert rig.adapter.published == [] and rig.adapter.own_said == []
        final = await rig.session.finish()
        assert final["status"] == "change_noticed" and final["concern"]["reason"] == "other"
        assert [p["phase"] for p in rig.adapter.published] == ["final"]
        assert rig.adapter.published[0]["summary"]["concern"]["reason"] == "other"
        assert rig.adapter.own_said == [limits.ALERT_REASSURANCE] and rig.adapter.said[-1] == "bye"

    asyncio.run(scenario())


def test_hanging_up_after_a_concern_still_tells_the_family_once_and_says_nothing_to_her():
    async def scenario():
        rig = CallRig()
        await rig.greet()
        await rig.handlers.execute("alert_family", {"reason": "asked_for_help", "note": "n"})
        rig.session.hang_up()
        final = await rig.session.finish()
        assert final["status"] == "concern_raised" and final["concern"]["reason"] == "asked_for_help"
        assert [p["phase"] for p in rig.adapter.published] == ["final"]
        assert rig.adapter.own_said == [] and "bye" not in rig.adapter.said

    asyncio.run(scenario())


def test_with_the_switch_off_a_concern_is_published_mid_call_at_once(monkeypatch):
    monkeypatch.setattr(limits, "CONCERN_ALERT_AT_END", False)

    async def scenario():
        rig = CallRig()
        await rig.greet()
        assert await rig.handlers.execute("alert_family", {"reason": "said_unwell", "note": "n"}) == {"sent": True}
        assert [p["phase"] for p in rig.adapter.published] == ["mid_call"]
        assert rig.adapter.own_said == [limits.ALERT_REASSURANCE]
        await rig.session.finish()
        assert [p["phase"] for p in rig.adapter.published] == ["mid_call", "final"]

    asyncio.run(scenario())


# ---- v14: the call prompt and the family text follow the person's profile --------------------------------------------

def _prompt(name, pronoun, age=None, about=()):
    from analysis.session.agent import build_system_prompt
    from analysis.session.agent_tools import load_script

    return build_system_prompt(name, load_script(), None, ("smile", "arms"), pronoun, age, about)


@pytest.mark.parametrize("pronoun", ["she", "he"])
def test_the_built_prompt_has_no_placeholder_left_and_has_the_name_and_the_facts(pronoun):
    text = _prompt("Feodor", pronoun, 21, ("Likes tea in the morning.", "Lives in Vilnius."))
    for placeholder in ("{name}", "{checks}", "{lines}", "{about}", "{she}", "{her}", "{her_obj}"):
        assert placeholder not in text
    assert "Feodor" in text and "- Age: 21." in text
    assert "- Likes tea in the morning." in text and "- Lives in Vilnius." in text
    assert "smile, then arms" in text


def test_the_prompt_without_facts_says_nothing_else_is_known():
    assert "- Nothing else is known." in _prompt("Feodor", "he")
    assert "- Age:" not in _prompt("Feodor", "he")


def test_the_he_prompt_has_no_she_or_her_words_outside_the_stop_reason():
    import re

    text = _prompt("Feodor", "he", 21, ("Likes tea.",)).replace("she_asked_to_stop", "")
    assert not re.search(r"\b(she|her|hers)\b", text, re.IGNORECASE)
    she_text = _prompt("Ona", "she", None, ())
    assert re.search(r"\bshe\b", she_text) and not re.search(r"\b(he|his|him)\b", she_text)


def test_the_session_keeps_the_profile_for_the_prompt_and_old_callers_default_to_she():
    old = CallSession("c", "p", "Ona", None, {})
    assert (old.person_pronoun, old.person_age, old.person_about) == ("she", None, ())
    new = CallSession("c", "p", "Feodor", None, {}, person_pronoun="he", person_age=21, person_about=["a", "b"])
    assert (new.person_pronoun, new.person_age, new.person_about) == ("he", 21, ("a", "b"))


def test_the_family_text_uses_he_his_him_for_he():
    change = {"status": "change_noticed", "signs": [{"sign": "face", "confirmed": True}]}
    assert render_family_text(change, "Feodor", "he") == (
        "Feodor's smile looks different from his usual. Call him now. If he seems unwell, call 112.")
    assert render_family_text(change, "Ona") == (
        "Ona's smile looks different from her usual. Call her now. If she seems unwell, call 112.")
    concern = {"status": "concern_raised", "concern": {"reason": "said_unwell"}}
    assert render_family_text(concern, "Feodor", "he") == (
        "Feodor said or showed something during today's check-in that needs a look (he said he does not feel well). "
        "Please call him. If he seems unwell, call 112.")
    assert render_family_text({"status": "incomplete"}, "Feodor", "he") == (
        "Feodor's check-in could not be finished. Please call him. If he seems unwell, call 112.")
    assert "his usual" in render_family_text({"status": "learning"}, "Feodor", "he")
    assert render_family_text({"status": "as_usual"}, "Feodor", "he") == "Feodor's check-in today looked as usual."
