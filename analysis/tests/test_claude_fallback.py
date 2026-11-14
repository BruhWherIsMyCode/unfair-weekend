"""Disabling scripted substitution stops Claude failures, while preserving call cleanup and publication."""
from __future__ import annotations

import asyncio
import json

import pytest

from analysis.session.agent import ClaudeAgent, ClaudeFailed
from analysis.session.claude_client import ClaudeUnavailable, FakeClaudeClient, Hang, text_response, tool_use_response
from analysis.session import run_call as driver
from analysis.tests.agent_helpers import CallRig
from analysis.tests.test_turn_plan import normal_plans, plan


@pytest.mark.parametrize("value,expected", [(None, True), ("", True), ("on", True), ("off", False), (" OFF ", False)])
def test_fallback_setting(value, expected):
    assert driver.fallback_from_env({} if value is None else {"UW_CLAUDE_FALLBACK": value}) is expected


def test_invalid_setting_is_rejected():
    with pytest.raises(ValueError, match="UW_CLAUDE_FALLBACK"):
        driver.fallback_from_env({"UW_CLAUDE_FALLBACK": "yes"})


@pytest.mark.parametrize("failure,reason", [
    ([RuntimeError("private provider detail")], "request_error_RuntimeError"),
    ([Hang()], "request_timeout"),
    ([text_response("No decision")], "no_tool_use"),
    ([{"content": None}], "malformed_response"),
    ([tool_use_response("unknown"), tool_use_response("unknown")], "bad_tool_input_twice"),
    ([plan("ask", {"question_id": "q_medicine"}) for _ in range(3)], "refused_3x"),
])
def test_failures_end_without_running_the_remaining_script(failure, reason):
    async def scenario():
        rig = CallRig(required_tasks=("smile", "arms"))
        client = FakeClaudeClient([plan("ask", {"question_id": "q_medicine"}, greet=True)] + failure, rig.clock)
        with pytest.raises(ClaudeFailed) as caught:
            await rig.drive(driver.run_call(rig.session, rig.adapter, "claude", client=client,
                                             agent_clock=rig.clock, allow_fallback=False))
        assert caught.value.reason == reason and caught.value.code == "claude_failed"
        assert rig.session.finished and rig.adapter.said == ["greet", "q_medicine", "bye"]
        assert not rig.session.attempts and rig.kinds("agent_fallback") == rig.kinds("fallback_step") == []
        assert rig.kinds("agent_failed")[-1]["reason"] == reason
        assert len(rig.adapter.published) == 1 and rig.adapter.published[0]["summary"]["status"] == "incomplete"
        assert "private provider detail" not in json.dumps(rig.session.log)

    asyncio.run(scenario())


def test_unavailable_client_stops_without_a_script_and_still_publishes(monkeypatch):
    def unavailable(backend):
        raise ClaudeUnavailable("the Claude client is missing")

    monkeypatch.setattr(driver, "build_client", unavailable)

    async def scenario():
        rig = CallRig(required_tasks=("smile", "arms"))
        with pytest.raises(ClaudeFailed, match="client_unavailable"):
            await driver.run_call(rig.session, rig.adapter, "claude", allow_fallback=False)
        assert rig.session.finished and rig.adapter.said == ["bye"]
        assert len(rig.adapter.published) == 1 and not rig.kinds("fallback_step")

    asyncio.run(scenario())


def test_turn_cap_never_switches_to_a_script_when_disabled():
    async def scenario():
        rig = CallRig(required_tasks=("smile", "arms"))
        client = FakeClaudeClient([plan("ask", {"question_id": "q_medicine"}, greet=True)], rig.clock)
        agent = ClaudeAgent(rig.session, rig.handlers, client, max_turns=1, allow_fallback=False)
        with pytest.raises(ClaudeFailed) as caught:
            await rig.drive(agent.run())
        assert caught.value.reason == "turn_cap" and agent.using_fallback is False
        assert rig.adapter.said == ["greet", "q_medicine"] and not rig.session.attempts
        await rig.session.finish()

    asyncio.run(scenario())


def test_unexpected_driver_bug_also_stops_instead_of_substituting_a_script(monkeypatch):
    async def broken(self):
        raise RuntimeError("private bug details")

    monkeypatch.setattr(ClaudeAgent, "run", broken)

    async def scenario():
        rig = CallRig(required_tasks=("smile", "arms"))
        with pytest.raises(ClaudeFailed, match="driver_error_RuntimeError"):
            await driver.run_call(rig.session, rig.adapter, "claude", client=FakeClaudeClient(), allow_fallback=False)
        assert rig.session.finished and rig.adapter.said == ["bye"] and not rig.kinds("fallback_step")

    asyncio.run(scenario())


def test_concern_survives_failure_and_the_subscription_client_is_closed():
    class Client(FakeClaudeClient):
        closed = False

        async def close(self):
            self.closed = True

    async def scenario():
        rig = CallRig(required_tasks=("smile", "arms"))
        client = Client([
            plan("ask", {"question_id": "q_medicine"}, greet=True),
            plan("ask", {"question_id": "q_feeling"},
                 concern={"reason": "asked_for_help", "note": "She asked for help."}),
            RuntimeError("provider failed"),
        ], rig.clock)
        with pytest.raises(ClaudeFailed):
            await rig.drive(driver.run_call(rig.session, rig.adapter, "claude", client=client,
                                             agent_clock=rig.clock, allow_fallback=False))
        assert client.closed and rig.session.finished
        assert len(rig.adapter.published) == 1
        assert rig.adapter.published[0]["summary"]["concern"]["reason"] == "asked_for_help"
        assert rig.adapter.published[0]["text"].endswith("If she seems unwell, call 112.")
        assert "q_unusual" not in rig.adapter.said and not rig.session.attempts

    asyncio.run(scenario())


def test_successful_call_still_uses_six_live_decisions_with_fallback_disabled():
    async def scenario():
        rig = CallRig(required_tasks=("smile", "arms"))
        client = FakeClaudeClient(normal_plans(), rig.clock)
        summary = await rig.drive(driver.run_call(rig.session, rig.adapter, "claude", client=client,
                                                  agent_clock=rig.clock, allow_fallback=False))
        assert summary["status"] == "as_usual" and len(client.requests) == 6
        assert rig.kinds("agent_failed") == rig.kinds("agent_fallback") == []

    asyncio.run(scenario())


def test_environment_toggle_applies_to_direct_run_call_users(monkeypatch):
    monkeypatch.setenv("UW_CLAUDE_FALLBACK", "off")

    async def scenario():
        rig = CallRig()
        with pytest.raises(ClaudeFailed):
            await driver.run_call(rig.session, rig.adapter, "claude", client=FakeClaudeClient([RuntimeError()]))
        assert rig.session.finished and not rig.kinds("fallback_step")

    asyncio.run(scenario())
