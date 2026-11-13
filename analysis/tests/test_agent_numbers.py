"""v7: the numbers Claude gets from run_task, the alert_family tool, the concern_raised status, the required tasks."""
from __future__ import annotations

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


def test_a_confirmed_change_beats_a_concern_and_both_are_published():
    async def scenario():
        rig = CallRig(corners=(0.11, 0.09))
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
