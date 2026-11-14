"""ClaudeSubscriptionClient with a fake SDK client. The real claude_agent_sdk is never imported here."""
from __future__ import annotations

import asyncio
import json
import os
import sys

import pytest

from analysis.session.agent import ClaudeAgent
from analysis.session.claude_client import ClaudeRequestError, ClaudeUnavailable
from analysis.session.claude_sub_client import (BUILTIN_TOOLS, ClaudeSubscriptionClient, parse_first_json_object)
from analysis.tests.agent_helpers import CallRig

SECRET = "sk-ant-secret-for-test-0123"


class TextBlock:
    def __init__(self, text):
        self.text = text


class AssistantMessage:
    def __init__(self, text):
        self.content = [TextBlock(text)]


class ResultMessage:
    def __init__(self, is_error=False, result=None):
        self.is_error, self.result = is_error, result


class FakeSdk:
    def __init__(self, options, replies, errors=None):
        self.options, self.replies, self.errors = options, replies, errors
        self.prompts, self.connected, self.disconnected = [], False, False

    async def connect(self):
        self.connected = True

    async def query(self, prompt):
        self.prompts.append(prompt)

    async def receive_response(self):
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        yield AssistantMessage(reply)
        yield ResultMessage(is_error=self.errors == "result")

    async def disconnect(self):
        self.disconnected = True


def make(replies, errors=None, **kw):
    holder = {}

    def factory(options):
        holder["sdk"] = FakeSdk(options, replies, errors)
        return holder["sdk"]

    return ClaudeSubscriptionClient(sdk_factory=factory, **kw), holder


TOOLS = [{"name": "say", "description": "d", "input_schema": {"type": "object"}}]


def test_json_is_found_inside_fences_and_words():
    assert parse_first_json_object('```json\n{"tool": "say", "input": {"line_id": "greet"}}\n```') == {
        "tool": "say", "input": {"line_id": "greet"}}
    assert parse_first_json_object('Sure! {"tool": "finish_call", "input": {}} hope that helps {"x": 1}') == {
        "tool": "finish_call", "input": {}}
    assert parse_first_json_object("no braces") is None
    assert parse_first_json_object("{broken json") is None


def test_create_returns_a_tool_use_block_and_numbers_the_ids():
    async def scenario():
        client, _ = make(['Here: ```{"tool": "say", "input": {"line_id": "greet"}}```',
                          '{"tool": "ask", "input": {"question_id": "q_medicine"}}'])
        a = await client.create("SYSTEM", [{"role": "user", "content": "Call connected."}], TOOLS)
        b = await client.create("SYSTEM", [{"role": "user", "content": "Call connected."}], TOOLS)
        id_a, id_b = a["content"][0]["id"], b["content"][0]["id"]
        assert a == {"content": [{"type": "tool_use", "id": id_a, "name": "say", "input": {"line_id": "greet"}}],
                     "stop_reason": "tool_use"}
        # one random prefix per client (the shared database keys tool calls across ALL calls: issue #24), then 1, 2
        assert id_a.startswith("toolu_sub_") and id_a.endswith("_1") and id_b == id_a[:-1] + "2"
        assert b["content"][0]["name"] == "ask"
        other, _ = make(['{"tool": "say", "input": {"line_id": "greet"}}'])
        c = await other.create("SYSTEM", [{"role": "user", "content": "Call connected."}], TOOLS)
        assert c["content"][0]["id"] not in (id_a, id_b)  # a second call never reuses the first call's ids

    asyncio.run(scenario())


def test_subscription_runs_a_compound_turn_and_returns_the_new_answer_to_its_session():
    from analysis.tests.test_turn_plan import plan

    async def scenario():
        replies = [json.dumps({"tool": "advance_call", "input": plan(
            "ask", {"question_id": "q_medicine"}, greet=True)["content"][0]["input"]}),
                   json.dumps({"tool": "advance_call", "input": plan(
                       "finish_call", answer={"question_id": "q_medicine", "value": "yes"},
                       reaction="Thank you for telling me.")["content"][0]["input"]})]
        client, holder = make(replies)
        rig = CallRig(required_tasks=())
        await rig.drive(ClaudeAgent(rig.session, rig.handlers, client, rig.clock).run())
        assert len(holder["sdk"].prompts) == 2 and rig.session.answers["medicine"] == "yes"
        assert '"transcript":"Yes, after breakfast."' in holder["sdk"].prompts[1]
        assert rig.adapter.said == ["greet", "q_medicine", "bye"]
        assert rig.adapter.own_said == ["Thank you for telling me."] and rig.session.finished
        await client.close()

    asyncio.run(scenario())


def test_options_close_every_tool_and_the_prompt_gets_the_answer_format():
    async def scenario():
        client, holder = make(['{"tool": "say", "input": {}}'])
        await client.create("SYSTEM PROMPT", [{"role": "user", "content": "go"}], TOOLS)
        o = holder["sdk"].options
        assert o["tools"] == [] and o["max_turns"] == 1 and o["setting_sources"] == [] and o["strict_mcp_config"] is True
        assert o["model"] == "haiku" and set(BUILTIN_TOOLS) <= set(o["disallowed_tools"]) and "Bash" in o["disallowed_tools"]
        assert o["system_prompt"].startswith("SYSTEM PROMPT") and "Answer format" in o["system_prompt"]
        assert '"name":"say"' in o["system_prompt"]
        assert os.path.isdir(o["cwd"]) and holder["sdk"].connected
        await client.close()
        assert holder["sdk"].disconnected and not os.path.exists(o["cwd"])

    asyncio.run(scenario())


def test_only_new_messages_are_sent_after_the_first_request():
    async def scenario():
        client, holder = make(['{"tool": "say", "input": {"line_id": "greet"}}',
                               '{"tool": "ask", "input": {"question_id": "q_medicine"}}'])
        first = [{"role": "user", "content": "Call connected."}]
        r1 = await client.create("S", first, TOOLS)
        history = first + [{"role": "assistant", "content": r1["content"]},
                           {"role": "user", "content": [{"type": "tool_result", "tool_use_id": r1["content"][0]["id"],
                                                         "content": json.dumps({"said": "greet", "played": True})}]}]
        await client.create("S", history, TOOLS)
        prompts = holder["sdk"].prompts
        assert prompts[0] == "Call connected."
        assert prompts[1] == 'Result of say: {"played":true,"said":"greet"}'
        assert "Call connected." not in prompts[1] and holder["sdk"].connected

    asyncio.run(scenario())


def test_garbage_gives_a_text_response_and_the_agent_falls_back():
    async def scenario():
        client, _ = make(["I think we should say hello first, " + "blah " * 100])
        r = await client.create("S", [{"role": "user", "content": "go"}], TOOLS)
        assert r["stop_reason"] == "end_turn" and r["content"][0]["type"] == "text" and len(r["content"][0]["text"]) == 200
        rig = CallRig()
        client2, _ = make(["no json here at all"])
        agent = ClaudeAgent(rig.session, rig.handlers, client2, rig.clock)
        await rig.drive(agent.run())
        assert [e["reason"] for e in rig.kinds("agent_fallback")] == ["no_tool_use"] and rig.session.finished

    asyncio.run(scenario())


def test_errors_carry_only_the_class_name():
    async def scenario():
        client, _ = make([], errors="result")
        client._factory = lambda o: FakeSdk(o, ["whatever text"], "result")
        with pytest.raises(ClaudeRequestError) as e1:
            await client.create("S", [{"role": "user", "content": "go"}], TOOLS)
        assert str(e1.value) == "ResultMessage"
        client2, _ = make([RuntimeError("secret detail " + SECRET)])
        with pytest.raises(ClaudeRequestError) as e2:
            await client2.create("S", [{"role": "user", "content": "go"}], TOOLS)
        assert str(e2.value) == "RuntimeError" and SECRET not in repr(e2.value)

    asyncio.run(scenario())


def test_a_slow_sdk_times_out():
    class Slow(FakeSdk):
        async def receive_response(self):
            await asyncio.sleep(5)
            yield AssistantMessage("{}")

    async def scenario():
        client = ClaudeSubscriptionClient(timeout_s=0.05, sdk_factory=lambda o: Slow(o, []))
        with pytest.raises(ClaudeRequestError) as e:
            await client.create("S", [{"role": "user", "content": "go"}], TOOLS)
        assert str(e.value) == "timeout"

    asyncio.run(scenario())


def test_missing_package_is_claude_unavailable(monkeypatch):
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", None)  # makes `import claude_agent_sdk` fail
    with pytest.raises(ClaudeUnavailable):
        ClaudeSubscriptionClient()


def test_the_api_key_variable_is_removed_and_never_logged(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", SECRET)

    async def scenario():
        client, _ = make(['{"tool": "say", "input": {"line_id": "greet"}}'])
        assert client.api_key_ignored is True and "ANTHROPIC_API_KEY" not in os.environ
        rig = CallRig()
        agent = ClaudeAgent(rig.session, rig.handlers, client, rig.clock)
        await rig.drive(agent.run())
        assert SECRET not in json.dumps(rig.session.log, default=str)
        assert SECRET not in client.last_excerpt

    asyncio.run(scenario())
