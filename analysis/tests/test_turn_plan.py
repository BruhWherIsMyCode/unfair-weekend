"""Compound model decisions: fewer requests, with the same ordered playback and task guards. No providers."""
from __future__ import annotations

import asyncio
import json

import pytest

from analysis.session.agent import ClaudeAgent
from analysis.session.claude_client import Delay, FakeClaudeClient, tool_use_response
from analysis.session.turn_plan import actions
from analysis.tests.agent_helpers import CallRig, camera_event


def plan(tool, input_=None, **fields):
    return tool_use_response("advance_call", {**fields, "next": {"tool": tool, "input": input_ or {}}})


def normal_plans():
    return [
        plan("ask", {"question_id": "q_medicine"}, greet=True),
        plan("ask", {"question_id": "q_feeling"}, answer={"question_id": "q_medicine", "value": "yes"},
             reaction="Thank you for telling me."),
        plan("ask", {"question_id": "q_unusual"}, answer={"question_id": "q_feeling", "value": "I feel fine."},
             reaction="Thank you for telling me."),
        plan("run_task", {"task": "smile"}, answer={"question_id": "q_unusual", "value": "Nothing unusual."},
             reaction="Thank you for telling me."),
        plan("run_task", {"task": "arms"}),
        plan("finish_call"),
    ]


def individual_steps():
    steps = [tool_use_response("say", {"line_id": "greet"})]
    for q, value in (("q_medicine", "yes"), ("q_feeling", "I feel fine."), ("q_unusual", "Nothing unusual.")):
        steps += [tool_use_response("ask", {"question_id": q}),
                  tool_use_response("record_answer", {"question_id": q, "value": value}),
                  tool_use_response("say_own", {"text": "Thank you for telling me."})]
    return steps + [tool_use_response("run_task", {"task": "smile"}),
                    tool_use_response("run_task", {"task": "arms"}), tool_use_response("finish_call")]


def last_result(request):
    return json.loads(request["messages"][-1]["content"][0]["content"])


def test_same_call_uses_six_requests_instead_of_thirteen():
    async def run(script):
        rig = CallRig(required_tasks=("smile", "arms"))
        client = FakeClaudeClient(script, rig.clock)
        await rig.drive(ClaudeAgent(rig.session, rig.handlers, client, rig.clock).run())
        assert rig.kinds("agent_fallback") == []
        return rig, client

    async def scenario():
        # Repeat the comparison to make the improvement reproducible; clocks and providers are mocked.
        for _ in range(2):
            before, old = await run(individual_steps())
            after, new = await run(normal_plans())
            assert len(old.requests) == 13 and len(new.requests) == 6
            assert before.session.answers == after.session.answers
            assert before.adapter.said == after.adapter.said
            assert before.adapter.own_said == after.adapter.own_said
            assert before.attempts_by_task == after.attempts_by_task == {"smile": 1, "arms": 1}
            assert before.session.build_session_result().status == after.session.build_session_result().status == "as_usual"
            assert all([t["name"] for t in r["tools"]] == ["advance_call"] for r in new.requests)
            # The result for each plan is the new information, not just an acknowledgment of local bookkeeping.
            assert last_result(new.requests[1])["transcript"] == "Yes, after breakfast."
            assert last_result(new.requests[4])["task"] == "smile"
            assert last_result(new.requests[5])["task"] == "arms"

    asyncio.run(scenario())


def test_followup_is_heard_before_the_answer_is_recorded_or_the_next_question_is_chosen():
    async def scenario():
        rig = CallRig(answers={"q_medicine": "Yes", "q_feeling": ["Not great", "My back hurts badly"],
                               "q_unusual": "Nothing else"}, required_tasks=("smile", "arms"))
        script = normal_plans()
        script[2:3] = [
            plan("ask_followup", {"question_id": "q_feeling", "text": "What is bothering you most today?"}),
            plan("ask", {"question_id": "q_unusual"},
                 answer={"question_id": "q_feeling", "value": "My back hurts badly"},
                 concern={"reason": "said_unwell", "note": "He said his back hurts badly."},
                 reaction="Thank you for telling me."),
        ]
        client = FakeClaudeClient(script, rig.clock)
        await rig.drive(ClaudeAgent(rig.session, rig.handlers, client, rig.clock).run())
        assert len(client.requests) == 7 and rig.kinds("agent_fallback") == []
        result = last_result(client.requests[3])
        assert result["transcript"] == "My back hurts badly" and result["completed_steps"] == ["ask_followup"]
        assert rig.session.answers["feeling"] == "My back hurts badly"
        assert rig.session.concern["reason"] == "said_unwell"
        recorded = [e for e in rig.session.log if e["kind"] == "answer_recorded" and e["question"] == "feeling"]
        followup = [e for e in rig.session.log if e["kind"] == "answer_heard" and e.get("followup")]
        assert len(recorded) == len(followup) == 1 and recorded[0]["t"] >= followup[0]["t"]
        # A concern may include her words; summaries and notes stay out of the model-decision log.
        shown = [e for e in rig.session.log if e["kind"] in ("agent_tool", "agent_plan_step")]
        assert "My back hurts badly" not in json.dumps(shown)
        assert "He said his back hurts badly" not in json.dumps(shown)

    asyncio.run(scenario())


@pytest.mark.parametrize("input_", [
    {"next": {"tool": "ask", "input": {"question_id": "q_feeling"}}, "then": {"tool": "finish_call"}},
    {"answer": {"question_id": "q_feeling", "value": "tired"},
     "next": {"tool": "ask_followup", "input": {"question_id": "q_feeling", "text": "What is bothering you?"}}},
    {"answer": {"question_id": "q_medicine", "value": "yes"}, "reaction": 42,
     "next": {"tool": "ask", "input": {"question_id": "q_feeling"}}},
    {"answer": {"question_id": "q_medicine", "value": "yes"},
     "next": {"tool": "ask", "input": {"question_id": "q_feeling", "after": "run_task"}}},
    {"greet": "true", "next": {"tool": "ask", "input": {"question_id": "q_medicine"}}},
    {"greet": True, "reaction": "Hello.", "next": {"tool": "ask", "input": {"question_id": "q_medicine"}}},
    {"next": {"tool": "record_answer", "input": {"question_id": "q_medicine", "value": "yes"}}},
])
def test_invalid_plans_are_rejected_before_any_part_is_executed(input_):
    async def scenario():
        rig = CallRig(required_tasks=())
        await rig.greet()
        await rig.handlers.execute("ask", {"question_id": "q_medicine"})
        before = list(rig.adapter.calls)
        client = FakeClaudeClient([tool_use_response("advance_call", input_), plan("finish_call")], rig.clock)
        await rig.drive(ClaudeAgent(rig.session, rig.handlers, client, rig.clock).run())
        assert rig.session.answers["medicine"] == "unclear"
        assert rig.adapter.calls[:-2] == before  # only the final publish and goodbye follow
        result = last_result(client.requests[1])
        assert result["error"].startswith("invalid input:")

    asyncio.run(scenario())


def test_a_resource_change_during_a_reaction_discards_the_unstarted_task():
    async def scenario():
        rig = CallRig(required_tasks=("smile",))
        await rig.greet()

        async def reaction(text):
            rig.session.handle_resource_event(camera_event("lost", "unavailable", 1))

        rig.adapter.say_text = reaction
        client = FakeClaudeClient([plan("run_task", {"task": "smile"}, reaction="Thank you."),
                                   plan("finish_call", {"reason": "she_asked_to_stop"})], rig.clock)
        await rig.drive(ClaudeAgent(rig.session, rig.handlers, client, rig.clock).run())
        result = last_result(client.requests[1])
        assert result["stale"] is True and result["completed_steps"] == ["say_own"]
        assert result["state"]["resources"]["camera"] == "unavailable"
        assert not rig.session.attempts and not rig.kinds("agent_fallback")

    asyncio.run(scenario())


def test_resource_recovery_result_is_seen_before_a_fresh_attempt_is_decided():
    async def scenario():
        rig = CallRig(required_tasks=("smile", "arms"))
        script = normal_plans()[:-1] + [plan("recover_resource", {"resource": "camera"}),
                                         plan("run_task", {"task": "arms"}), plan("finish_call")]
        client = FakeClaudeClient(script, rig.clock)
        await rig.drive(ClaudeAgent(rig.session, rig.handlers, client, rig.clock).run(),
                        lose_camera_in_first_arms=True)
        assert rig.session.finished and rig.kinds("agent_fallback") == []
        assert last_result(client.requests[5])["interruption"]["recoverable"] is True
        assert last_result(client.requests[6])["result"] == "restored"
        assert rig.attempts_by_task == {"smile": 1, "arms": 2}

    asyncio.run(scenario())


def test_the_first_question_waits_for_successful_greeting_playback():
    async def scenario():
        rig = CallRig(required_tasks=())
        rig.adapter.fail_say = True
        client = FakeClaudeClient([plan("ask", {"question_id": "q_medicine"}, greet=True),
                                   plan("finish_call")], rig.clock)
        await rig.drive(ClaudeAgent(rig.session, rig.handlers, client, rig.clock).run())
        assert rig.handlers.asks["q_medicine"] == 0
        assert last_result(client.requests[1])["failed_step"] == "say"
        assert last_result(client.requests[1])["completed_steps"] == []

    asyncio.run(scenario())


def test_playback_failure_stops_the_plan_and_preserves_the_already_recorded_concern():
    async def scenario():
        rig = CallRig(required_tasks=("smile",))
        await rig.greet()

        async def broken(text):
            raise RuntimeError("playback failed")

        rig.adapter.say_text = broken
        client = FakeClaudeClient([
            plan("run_task", {"task": "smile"}, reaction="Thank you.",
                 concern={"reason": "asked_for_help", "note": "She asked for help."}),
            plan("finish_call", {"reason": "she_asked_to_stop"}),
        ], rig.clock)
        await rig.drive(ClaudeAgent(rig.session, rig.handlers, client, rig.clock).run())
        result = last_result(client.requests[1])
        assert result["failed_step"] == "say_own" and result["completed_steps"] == ["alert_family"]
        assert not rig.session.attempts and rig.session.concern["reason"] == "asked_for_help"
        assert rig.session.finished

    asyncio.run(scenario())


def test_hangup_during_a_plan_stops_without_the_next_action_or_another_request():
    async def scenario():
        rig = CallRig(required_tasks=("smile",))
        await rig.greet()

        async def hangup(text):
            rig.session.hang_up()

        rig.adapter.say_text = hangup
        client = FakeClaudeClient([plan("run_task", {"task": "smile"}, reaction="Thank you.")], rig.clock)
        await rig.drive(ClaudeAgent(rig.session, rig.handlers, client, rig.clock).run())
        assert len(client.requests) == 1 and not rig.session.attempts and not rig.kinds("agent_fallback")

    asyncio.run(scenario())


def test_a_plan_decided_before_a_resource_change_has_no_side_effects():
    async def scenario():
        rig = CallRig(required_tasks=())
        await rig.greet()
        await rig.handlers.execute("ask", {"question_id": "q_medicine"})
        client = FakeClaudeClient([
            Delay(1.0, plan("ask", {"question_id": "q_feeling"},
                            answer={"question_id": "q_medicine", "value": "yes"}, reaction="Thank you.")),
            plan("finish_call"),
        ], rig.clock)
        running = asyncio.create_task(ClaudeAgent(rig.session, rig.handlers, client, rig.clock).run())
        await rig.clock.advance(0.5)
        rig.session.handle_resource_event(camera_event("lost", "unavailable", 1))
        await rig.clock.advance(1.0)
        await running
        assert last_result(client.requests[1])["stale"] is True
        assert rig.session.answers["medicine"] == "unclear" and rig.adapter.own_said == []
        assert rig.handlers.asks["q_feeling"] == 0

    asyncio.run(scenario())
