"""The Claude tool loop with FakeClaudeClient: normal call, one action per turn, stale answers, hang-up, and every
switch to the fixed-order fallback. Written, not run. FakeClock only; no network; the real client is never built."""
from __future__ import annotations

import asyncio
import json
import os

from analysis.session import limits
from analysis.session.agent import CAUSABLE_LINES, ClaudeAgent, build_system_prompt
from analysis.session.claude_client import (ClaudeRequestError, Delay, FakeClaudeClient, Hang, multi_tool_response,
                                            text_response, tool_use_response)
from analysis.tests.agent_helpers import CallRig, camera_event

SECRET = "sk-test-secret-0123456789"


def say(line):
    return tool_use_response("say", {"line_id": line})


def ask(q):
    return tool_use_response("ask", {"question_id": q})


def record(q, value):
    return tool_use_response("record_answer", {"question_id": q, "value": value})


def run_task(task):
    return tool_use_response("run_task", {"task": task})


def finish(**args):
    return tool_use_response("finish_call", args)


def normal_script():
    steps = [say("greet")]
    for q, value in (("q_medicine", "yes"), ("q_feeling", "I feel fine."), ("q_unusual", "Nothing unusual.")):
        steps += [ask(q), record(q, value)]
    steps += [run_task("smile"), run_task("arms"), run_task("sentence"), finish()]
    return steps


def make(rig, script, **kw):
    client = FakeClaudeClient(script, rig.clock)
    return ClaudeAgent(rig.session, rig.handlers, client, rig.clock, **kw), client


def check_history(messages):
    """Proper Messages history: roles alternate; every assistant tool_use is answered by exactly one tool_result."""
    assert messages[0]["role"] == "user" and isinstance(messages[0]["content"], str)
    for i, m in enumerate(messages):
        assert m["role"] == ("user" if i % 2 == 0 else "assistant")
        if m["role"] == "assistant":
            expected = [b["id"] for b in m["content"] if b["type"] == "tool_use"]
            assert expected
            answer = messages[i + 1]["content"]
            assert [b["tool_use_id"] for b in answer] == expected and all(b["type"] == "tool_result" for b in answer)


def fallback_reasons(rig):
    return [e["reason"] for e in rig.kinds("agent_fallback")]


def finished_by_fixed_order(rig):
    assert rig.session.finished is True and rig.adapter.said[-1] == "bye"
    assert rig.adapter.published[-1]["phase"] == "final"


# ---- the normal call ----------------------------------------------------------------------------------

def test_a_normal_call_uses_one_request_per_step_and_finishes():
    async def scenario():
        os.environ["ANTHROPIC_API_KEY"] = SECRET
        try:
            rig = CallRig()
            agent, client = make(rig, normal_script())
            await rig.drive(agent.run())
            assert len(client.requests) == 11 and rig.kinds("agent_fallback") == []
            check_history(client.requests[-1]["messages"])
            assert client.requests[0]["messages"][0]["content"] == "Call connected. Nothing done yet."
            assert rig.session.finished is True and rig.adapter.said[0] == "greet" and rig.adapter.said[-1] == "bye"
            assert rig.session.answers["medicine"] == "yes" and rig.session.answers["feeling"] == "I feel fine."
            assert all(r["tools"] == rig.handlers.tool_definitions() for r in client.requests)
            assert len(rig.kinds("agent_request")) == 11 and "latency_s" in rig.kinds("agent_request")[0]
            assert len(rig.kinds("agent_tool")) == 11
            # free text she said is never logged; the key and the prompt never reach the log
            logged = json.dumps(rig.session.log, default=str)
            assert "I feel fine" not in logged and "Nothing unusual" not in logged and SECRET not in logged
            assert client.requests[0]["system"] not in logged
        finally:
            os.environ.pop("ANTHROPIC_API_KEY", None)

    asyncio.run(scenario())


def test_the_system_prompt_fills_name_and_lines_and_nothing_else():
    rig = CallRig()
    prompt = build_system_prompt("Ona", rig.handlers.script)
    assert "{name}" not in prompt and "{lines}" not in prompt and "Ona" in prompt
    assert '- greet: "Hello, Ona. It\'s time for our short check-in."' in prompt
    for line_id in CAUSABLE_LINES:
        if line_id in rig.handlers.script:
            assert "- %s:" % line_id in prompt
    assert "- sentence:" not in prompt  # expected input, not a line the avatar says


# ---- one action per turn --------------------------------------------------------------------------------

def test_several_tool_uses_execute_only_the_first_and_every_one_gets_a_result():
    async def scenario():
        rig = CallRig()
        first = multi_tool_response(("say", {"line_id": "greet"}, "t1"), ("ask", {"question_id": "q_medicine"}, "t2"),
                                    ("say", {"line_id": "setup_phone"}, "t3"))
        agent, client = make(rig, [first, finish(reason="she_asked_to_stop")])
        await rig.drive(agent.run())
        assert rig.adapter.said == ["greet", "bye"]  # the ask and the set-up line were not executed
        results = client.requests[1]["messages"][-1]["content"]
        assert [b["tool_use_id"] for b in results] == ["t1", "t2", "t3"]
        assert json.loads(results[0]["content"]) == {"played": True, "said": "greet"}
        for block in results[1:]:
            assert json.loads(block["content"]) == {"error": "one action at a time, not executed"}
        check_history(client.requests[1]["messages"])

    asyncio.run(scenario())


# ---- stale decisions and hang-up ---------------------------------------------------------------------------

def test_an_answer_that_arrives_after_a_camera_loss_is_not_executed():
    async def scenario():
        rig = CallRig()
        script = [say("greet"), Delay(1.0, ask("q_medicine")), finish(reason="she_asked_to_stop")]
        agent, client = make(rig, script)
        running = asyncio.ensure_future(agent.run())
        await rig.clock.advance(0.5)  # greet done; the second request is generating
        before = rig.session.event_version
        rig.session.handle_resource_event(camera_event("e1", "unavailable", 1))
        assert rig.session.event_version > before
        await rig.clock.advance(1.0)  # the answer arrives: decided on the old state
        await running
        assert "q_medicine" not in rig.adapter.said and rig.handlers.asks["q_medicine"] == 0
        assert len(rig.kinds("agent_stale")) == 1
        stale = json.loads(client.requests[2]["messages"][-1]["content"][0]["content"])
        assert stale["stale"] is True and stale["state"]["resources"]["camera"] == "unavailable"
        assert "value" not in json.dumps(stale) and len(client.requests) == 3
        check_history(client.requests[2]["messages"])

    asyncio.run(scenario())


def test_hang_up_during_a_request_stops_at_once_with_no_further_request():
    async def scenario():
        rig = CallRig()
        agent, client = make(rig, [say("greet"), Delay(1.0, ask("q_medicine")), finish()])
        running = asyncio.ensure_future(agent.run())
        await rig.clock.advance(0.5)
        rig.session.hang_up()
        calls = list(rig.adapter.calls)
        await rig.clock.advance(1.0)
        await running
        assert len(client.requests) == 2 and rig.adapter.calls == calls
        assert rig.kinds("agent_fallback") == [] and len(rig.kinds("agent_tool")) == 1

    asyncio.run(scenario())


def test_the_event_version_moves_on_resource_changes_interruptions_and_hang_up():
    async def scenario():
        rig = CallRig()
        v = rig.session.event_version
        rig.session.handle_resource_event(camera_event("e1", "unavailable", 1))
        assert rig.session.event_version == v + 1
        rig.session.handle_resource_event(camera_event("e1", "unavailable", 1))  # duplicate: no change
        assert rig.session.event_version == v + 1
        rig.session.hang_up()
        assert rig.session.event_version == v + 2

    asyncio.run(scenario())


# ---- the switch to the fixed-order driver --------------------------------------------------------------------

def test_a_request_timeout_switches_to_the_fixed_order_for_the_rest_of_the_call():
    async def scenario():
        rig = CallRig()
        agent, client = make(rig, [say("greet"), Hang()])
        await rig.drive(agent.run())
        assert fallback_reasons(rig) == ["request_timeout"] and agent.using_fallback is True
        assert len(client.requests) == 2  # never asked again
        assert rig.adapter.said.count("greet") == 1  # the fixed order took over from the current state
        assert "task_smile" in rig.adapter.said and "task_arms" in rig.adapter.said
        finished_by_fixed_order(rig)

    asyncio.run(scenario())


def test_a_request_error_switches_and_the_key_text_never_reaches_the_log():
    async def scenario():
        rig = CallRig()
        agent, _ = make(rig, [RuntimeError("boom " + SECRET)])
        await rig.drive(agent.run())
        assert fallback_reasons(rig) == ["request_error_RuntimeError"]
        assert SECRET not in json.dumps(rig.session.log, default=str)
        finished_by_fixed_order(rig)

        rig = CallRig()
        agent, _ = make(rig, [ClaudeRequestError("timeout")])
        await rig.drive(agent.run())
        assert fallback_reasons(rig) == ["request_error_ClaudeRequestError"]

    asyncio.run(scenario())


def test_a_response_without_a_tool_use_switches():
    async def scenario():
        rig = CallRig()
        agent, _ = make(rig, [text_response("Hello there!")])
        await rig.drive(agent.run())
        assert fallback_reasons(rig) == ["no_tool_use"]
        finished_by_fixed_order(rig)

    asyncio.run(scenario())


def test_malformed_blocks_switch():
    async def scenario():
        for response in ({"content": "oops"}, {"content": [42]},
                         {"content": [{"type": "tool_use", "name": "say", "input": {"line_id": "greet"}}]}):
            rig = CallRig()
            agent, _ = make(rig, [response])
            await rig.drive(agent.run())
            assert fallback_reasons(rig) == ["malformed_response"], response
            finished_by_fixed_order(rig)

    asyncio.run(scenario())


def test_three_refused_steps_in_a_row_switch():
    async def scenario():
        rig = CallRig()
        agent, client = make(rig, [say("greet"), say("greet"), say("greet"), say("greet"), finish()])
        await rig.drive(agent.run())
        assert fallback_reasons(rig) == ["refused_3x"] and len(client.requests) == 4
        assert len(rig.kinds("agent_refused")) == 3
        check_history(client.requests[3]["messages"])
        finished_by_fixed_order(rig)

    asyncio.run(scenario())


def test_a_refused_step_between_good_ones_does_not_switch():
    async def scenario():
        rig = CallRig()
        agent, _ = make(rig, [say("greet"), say("greet"), ask("q_medicine"), say("greet"), ask("q_feeling"),
                              finish(reason="she_asked_to_stop")])
        await rig.drive(agent.run())
        assert rig.kinds("agent_fallback") == [] and len(rig.kinds("agent_refused")) == 2

    asyncio.run(scenario())


def test_an_unknown_tool_or_bad_input_twice_in_a_row_switches_once_does_not():
    async def scenario():
        rig = CallRig()
        agent, _ = make(rig, [tool_use_response("fly", {}), tool_use_response("say", {"line_id": "bye"}), finish()])
        await rig.drive(agent.run())
        assert fallback_reasons(rig) == ["bad_tool_input_twice"]
        finished_by_fixed_order(rig)

        rig = CallRig()
        agent, _ = make(rig, [tool_use_response("fly", {}), say("greet"), finish(reason="she_asked_to_stop")])
        await rig.drive(agent.run())
        assert rig.kinds("agent_fallback") == []

    asyncio.run(scenario())


def test_more_than_the_turn_cap_switches():
    async def scenario():
        rig = CallRig()
        script = [say("greet"), ask("q_medicine"), record("q_medicine", "yes"), ask("q_feeling")]
        agent, client = make(rig, script, max_turns=3)
        await rig.drive(agent.run())
        assert fallback_reasons(rig) == ["turn_cap"] and len(client.requests) == 3
        finished_by_fixed_order(rig)

    asyncio.run(scenario())
    assert limits.CLAUDE_MAX_TURNS == 30


def test_a_client_that_could_not_be_built_goes_straight_to_the_fixed_order():
    async def scenario():
        rig = CallRig()
        agent = ClaudeAgent(rig.session, rig.handlers, None, rig.clock, unavailable_reason="client_unavailable: test")
        await rig.drive(agent.run())
        assert fallback_reasons(rig) == ["client_unavailable: test"]
        finished_by_fixed_order(rig)

    asyncio.run(scenario())


# ---- Claude never decides a result -----------------------------------------------------------------------------

def test_a_confirmed_change_is_published_even_if_claude_fails_right_after():
    async def scenario():
        os.environ["ANTHROPIC_API_KEY"] = SECRET
        try:
            rig = CallRig(corners=(0.11, 0.09))
            script = [say("greet"), run_task("smile"), run_task("smile"), Hang()]
            agent, client = make(rig, script)
            await rig.drive(agent.run())
            phases = [p["phase"] for p in rig.adapter.published]
            assert phases[0] == "mid_call" and phases[-1] == "final" and phases.count("mid_call") == 1
            assert rig.adapter.published[0]["summary"]["status"] == "change_noticed"
            assert fallback_reasons(rig) == ["request_timeout"]
            # what Claude saw after the second smile: plain facts, nothing about the finding or the family
            seen = json.dumps(client.requests[3]["messages"]).lower()
            for word in ("confirm", "family", "outside", "usual", "alert", "published"):
                assert word not in seen, word
            assert SECRET not in json.dumps(rig.session.log, default=str)
        finally:
            os.environ.pop("ANTHROPIC_API_KEY", None)

    asyncio.run(scenario())
