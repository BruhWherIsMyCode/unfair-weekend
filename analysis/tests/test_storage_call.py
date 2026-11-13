"""A whole fake call recorded in the shared database: the real migration on a temp file, the real smile and arms final
result code on made-up frames, the fixed-order driver, FakeClock. No network, no Claude, no mediapipe."""
from __future__ import annotations

import asyncio
import json

import pytest

from analysis.session.agent import ClaudeAgent
from analysis.session.claude_client import Delay, FakeClaudeClient, tool_use_response
from analysis.storage import SessionRecorder, Writer, repo
from analysis.tests.agent_helpers import CallRig, camera_event
from analysis.tests.storage_helpers import (PERSON, REQUIRED, add_call, agent2_final_summary, clock, count, end_call,
                                            make_db, make_recorder, rows, run_fixed_call, settle)


@pytest.fixture()
def env(tmp_path):
    path = make_db(tmp_path)
    writer = Writer(path)
    yield path, writer
    writer.close()


def test_a_whole_call_is_recorded_in_every_owner_3_table(env):
    path, writer = env
    add_call(path)
    recorder = make_recorder(writer)
    rig = CallRig(required_tasks=REQUIRED)
    summary = run_fixed_call(rig, recorder)
    end_call(path)
    settle(writer)
    assert summary["status"] == "as_usual" and recorder.enabled and recorder.failures == 0 and writer.failures == 0

    assert count(path, "policy_definitions") == 1
    defs = rows(path, "SELECT task, metric_id, unit, required_resource, window_ms FROM task_definitions ORDER BY task")
    assert [d["task"] for d in defs] == ["arms", "smile"]  # the sentence stub has no metric: nothing to store
    assert defs[0]["window_ms"] == 15000 and defs[1]["metric_id"] == "corner_height_v1"

    session = rows(path, "SELECT * FROM call_sessions")[0]
    assert session["phase"] == "finished" and session["finished_at_ms"] >= session["started_at_ms"]
    snapshot = json.loads(session["snapshot_json"])
    assert snapshot["resources"]["camera"]["state"] == "available" and snapshot["budgets"]["smile"]["usable"] == 1
    assert snapshot["answers"]["medicine"] == "yes"
    assert session["context_revision"] >= 3  # attempts finished, answers selected, finish: never backwards

    frozen = rows(path, "SELECT task, snapshot_id FROM session_baselines ORDER BY task")
    assert [f["task"] for f in frozen] == ["arms", "smile"]
    assert all(f["snapshot_id"].startswith("unseeded_") for f in frozen)  # her usual came from the "json" dicts
    unseeded = rows(path, "SELECT sessions, ready, usual_low FROM baseline_snapshots WHERE snapshot_id LIKE 'unseeded_%'")
    assert unseeded == [{"sessions": 0, "ready": 0, "usual_low": None}] * 2

    # fixed order: greet, three asks, three record_answers, smile, arms, finish_call (sentence has no module)
    tools = rows(path, "SELECT tool_name, state FROM tool_calls ORDER BY created_at_ms, rowid")
    assert [t["tool_name"] for t in tools] == ["say"] + ["ask", "record_answer"] * 3 + ["run_task"] * 2 + ["finish_call"] \
        or sorted(t["tool_name"] for t in tools) == sorted(["say"] + ["ask"] * 3 + ["record_answer"] * 3
                                                           + ["run_task"] * 2 + ["finish_call"])
    assert all(t["state"] == "completed" for t in tools) and len(tools) == 10
    turns = rows(path, "SELECT model_id, state FROM model_turns")
    assert len(turns) == 10 and {(t["model_id"], t["state"]) for t in turns} == {("fixed_order", "completed")}

    attempts = rows(path, "SELECT task, ordinal, state, ended_at_ms, terminal_result_attempt_id, "
                          "latest_observation_event_id FROM task_attempts ORDER BY task")
    assert [(a["task"], a["ordinal"], a["state"]) for a in attempts] == [("arms", 1, "completed"),
                                                                         ("smile", 1, "completed")]
    assert all(a["ended_at_ms"] and a["terminal_result_attempt_id"] and a["latest_observation_event_id"]
               for a in attempts)
    assert count(path, "task_observations") >= 2
    obs = rows(path, "SELECT sequence, elapsed_ms FROM task_observations WHERE attempt_id LIKE '%smile%' "
                     "OR attempt_id IN (SELECT attempt_id FROM task_attempts WHERE task = 'smile') ORDER BY sequence")
    assert [o["sequence"] for o in obs] == sorted(o["sequence"] for o in obs) and obs

    results = rows(path, "SELECT r.outcome, r.value, r.usual_low, r.outside, a.task FROM task_results r "
                         "JOIN task_attempts a USING (attempt_id) ORDER BY a.task")
    assert [r["outcome"] for r in results] == ["completed", "completed"]
    assert all(r["value"] is not None and r["usual_low"] is None and r["outside"] is None for r in results)
    tool = rows(path, "SELECT result_json FROM tool_calls WHERE tool_name = 'run_task' LIMIT 1")[0]
    assert json.loads(tool["result_json"])["task_result"]["outcome"] == "completed"
    finalized = rows(path, "SELECT payload_json FROM call_events WHERE event_type = 'attempt.finalized' "
                           "AND attempt_id IN (SELECT attempt_id FROM task_attempts WHERE task = 'arms')")
    assert set(json.loads(finalized[0]["payload_json"])["values"]) == {"left", "right"}

    answers = rows(path, "SELECT question_id, status, selected, normalized_value FROM answers ORDER BY question_id")
    assert [(a["question_id"], a["status"], a["selected"]) for a in answers] == [
        ("feeling", "heard", 1), ("medicine", "heard", 1), ("unusual", "heard", 1)]
    assert [a["normalized_value"] for a in answers if a["question_id"] == "medicine"] == ["yes"]

    summaries = rows(path, "SELECT revision, status, phase FROM session_summaries")
    assert summaries == [{"revision": 1, "status": "as_usual", "phase": "final"}]

    types = [e["event_type"] for e in rows(path, "SELECT event_type FROM call_events ORDER BY position")]
    assert types[0] == "session.started" and types.count("attempt.started") == 2
    assert types.count("attempt.finalized") == 2 and types.count("task.observation") >= 2
    positions = [e["position"] for e in rows(path, "SELECT position FROM call_events ORDER BY position")]
    assert positions == list(range(len(positions)))

    # an eligible normal call: a new snapshot per task with this call as its only member
    snaps = rows(path, "SELECT snapshot_id, sessions, ready FROM baseline_snapshots WHERE sessions > 0 "
                       "ORDER BY snapshot_id")
    assert [s["snapshot_id"] for s in snaps] == ["snap_call_t_arms", "snap_call_t_smile"]
    assert all(s["sessions"] == 1 and s["ready"] == 0 for s in snaps)
    assert count(path, "baseline_members", "source_call_id = 'call_t'") == 2


# ---- the Claude driver: turns and tool calls --------------------------------------------------------------------

def claude_rig_call(path, writer, script, *, stop_early=None, **rig_kw):
    """A CallRig whose tool loop is run by a scripted fake Claude, recorded in the database."""
    add_call(path)
    recorder = make_recorder(writer)
    rig = CallRig(required_tasks=REQUIRED, **rig_kw)
    rig.session.recorder = recorder
    recorder.begin(rig.session, {})
    client = FakeClaudeClient(script, rig.clock)
    agent = ClaudeAgent(rig.session, rig.handlers, client, rig.clock)

    async def scenario():
        if stop_early is None:
            await rig.drive(agent.run())
        else:
            await stop_early(rig, agent)
        recorder.finish_call(rig.session, rig.session.build_session_result().to_dict())

    asyncio.run(scenario())
    return rig, recorder


def tu(name, args=None, tool_id=None):
    return tool_use_response(name, args or {}, tool_id)


def test_claude_turns_and_tool_calls_are_recorded_with_their_real_ids_and_a_stale_turn_is_kept(env):
    path, writer = env

    async def stop_early(rig, agent):
        running = asyncio.ensure_future(agent.run())
        await rig.clock.advance(0.5)  # greet done; the second request is generating
        rig.session.handle_resource_event(camera_event("e1", "unavailable", 1))
        await rig.clock.advance(1.0)  # the answer arrives, decided on the old state: stale
        await running

    script = [tu("say", {"line_id": "greet"}, "toolu_A"), Delay(1.0, tu("ask", {"question_id": "q_medicine"}, "toolu_B")),
              tu("finish_call", {"reason": "she_asked_to_stop"}, "toolu_C")]
    rig, recorder = claude_rig_call(path, writer, script, stop_early=stop_early)
    settle(writer)
    assert recorder.failures == 0 and writer.failures == 0

    turns = rows(path, "SELECT sequence, state, model_id, context_revision, request_json, response_json, error_code "
                       "FROM model_turns ORDER BY sequence")
    assert [(t["sequence"], t["state"]) for t in turns] == [(0, "completed"), (1, "stale"), (2, "completed")]
    assert {t["model_id"] for t in turns} == {"fake-claude"}
    assert turns[1]["context_revision"] < turns[2]["context_revision"]  # the stale one was decided on the old context
    stale_output = json.loads(turns[1]["response_json"])["content"][0]
    assert stale_output["name"] == "ask" and stale_output["id"] == "toolu_B"  # kept, never executed
    assert json.loads(turns[0]["request_json"])["message_count"] == 1

    tools = rows(path, "SELECT tool_call_id, tool_name, state, turn_id FROM tool_calls ORDER BY created_at_ms, rowid")
    assert [(t["tool_name"], t["state"]) for t in tools] == [("say", "completed"), ("finish_call", "completed")]
    assert [t["tool_call_id"] for t in tools] == ["toolu_A", "toolu_C"]  # the model's own ids
    assert tools[0]["turn_id"].endswith(".turn0")
    assert "q_medicine" not in rig.adapter.said and count(path, "answers") == 0
    stale_event = rows(path, "SELECT source, payload_json FROM call_events WHERE event_type = 'resource.changed'")[0]
    assert stale_event["source"] == "client" and json.loads(stale_event["payload_json"])["state"] == "unavailable"


def test_a_failed_request_and_refused_and_rejected_steps_are_recorded(env):
    path, writer = env
    # 1. say greet; 2. an ask before... (allowed); 3. run_task for an unknown-to-the-rig sentence (not allowed: no module?)
    script = [tu("say", {"line_id": "greet"}, "toolu_1"), tu("say", {"line_id": "greet"}, "toolu_2"),
              tu("ask", {"question_id": "nonsense"}, "toolu_3"), RuntimeError("boom")]
    rig, recorder = claude_rig_call(path, writer, script)
    end_call(path)
    settle(writer)
    assert recorder.failures == 0 and writer.failures == 0
    turns = rows(path, "SELECT state, error_code FROM model_turns ORDER BY sequence")
    assert [t["state"] for t in turns[:3]] == ["completed"] * 3
    assert turns[3] == {"state": "failed", "error_code": "request_error_RuntimeError"}
    tools = rows(path, "SELECT tool_call_id, state, result_json FROM tool_calls WHERE tool_call_id LIKE 'toolu_%' "
                       "ORDER BY created_at_ms, rowid")
    assert [(t["tool_call_id"], t["state"]) for t in tools] == [
        ("toolu_1", "completed"), ("toolu_2", "rejected"), ("toolu_3", "rejected")]
    assert "not allowed" in json.loads(tools[1]["result_json"])["error"]
    assert "invalid input" in json.loads(tools[2]["result_json"])["error"]
    # the fixed order took over for the rest: its turns are synthetic and the call still finished and was recorded
    assert rig.session.finished is True
    assert count(path, "model_turns", "model_id = 'fixed_order'") >= 8
    assert rows(path, "SELECT phase FROM call_sessions")[0]["phase"] == "finished"


def test_alert_family_has_no_tool_row_but_its_turn_and_the_summary_status_are_kept(env):
    path, writer = env
    script = [tu("say", {"line_id": "greet"}, "toolu_1"),
              tu("alert_family", {"reason": "said_unwell", "note": "She said she feels dizzy."}, "toolu_2"),
              tu("finish_call", {"reason": "she_asked_to_stop"}, "toolu_3")]
    rig, recorder = claude_rig_call(path, writer, script)
    end_call(path)
    settle(writer)
    assert recorder.failures == 0 and writer.failures == 0
    assert rig.session.concern is not None
    assert [t["tool_name"] for t in rows(path, "SELECT tool_name FROM tool_calls ORDER BY created_at_ms, rowid")] == [
        "say", "finish_call"]
    turn = rows(path, "SELECT response_json FROM model_turns WHERE sequence = 1")[0]
    assert json.loads(turn["response_json"])["content"][0]["input"]["note"] == "<omitted>"  # her words are not stored
    summaries = rows(path, "SELECT status, phase, summary_json FROM session_summaries ORDER BY revision")
    assert [s["phase"] for s in summaries] == ["mid_call", "final"]
    assert {s["status"] for s in summaries} == {"incomplete"}  # the schema has no concern status (README: For Agent 2)
    assert json.loads(summaries[1]["summary_json"])["session_status"] == "concern_raised"
    assert count(path, "baseline_snapshots", "sessions > 0") == 0  # a concern never enters her usual


# ---- summaries, change and learning rules -------------------------------------------------------------------------

def test_a_confirmed_change_writes_a_mid_call_then_a_final_summary_and_no_new_baseline(env):
    path, writer = env
    add_call(path)
    recorder = make_recorder(writer)
    rig = CallRig(required_tasks=REQUIRED, corners=(0.11, 0.11))
    summary = run_fixed_call(rig, recorder)
    end_call(path)
    settle(writer)
    assert summary["status"] == "change_noticed" and recorder.failures == 0 and writer.failures == 0
    summaries = rows(path, "SELECT revision, status, phase FROM session_summaries ORDER BY revision")
    assert summaries == [{"revision": 1, "status": "change_noticed", "phase": "mid_call"},
                         {"revision": 2, "status": "change_noticed", "phase": "final"}]
    assert count(path, "task_attempts", "task = 'smile'") == 2
    assert count(path, "baseline_snapshots", "sessions > 0") == 0 and count(path, "baseline_members") == 0
    assert count(path, "baseline_snapshots", "snapshot_id LIKE 'unseeded_%'") == 2  # only the frozen placeholders


def test_answers_keep_every_retry_and_select_exactly_one(env):
    path, writer = env
    add_call(path)
    recorder = make_recorder(writer)
    rig = CallRig(required_tasks=REQUIRED, answers={"q_medicine": [None, "No, not yet."], "q_feeling": None})
    run_fixed_call(rig, recorder)
    end_call(path)
    settle(writer)
    assert recorder.failures == 0 and writer.failures == 0
    answers = rows(path, "SELECT question_id, ordinal, status, transcript, selected, normalized_value FROM answers "
                         "ORDER BY question_id, ordinal")
    medicine = [a for a in answers if a["question_id"] == "medicine"]
    assert [(a["ordinal"], a["status"], a["selected"]) for a in medicine] == [(1, "no_speech", 0), (2, "heard", 1)]
    assert medicine[1]["transcript"] == "No, not yet." and medicine[1]["normalized_value"] == "no"
    feeling = [a for a in answers if a["question_id"] == "feeling"]
    assert [(a["status"], a["selected"], a["transcript"]) for a in feeling] == [("no_speech", 0, None),
                                                                               ("no_speech", 1, None)]
    assert all(sum(a["selected"] for a in answers if a["question_id"] == q) == 1
               for q in ("medicine", "feeling", "unusual"))


def test_a_hang_up_is_recorded_as_cancelled_and_the_session_still_finishes(env):
    path, writer = env
    add_call(path)
    recorder = make_recorder(writer)
    rig = CallRig(required_tasks=REQUIRED)

    async def scenario():
        rig.session.recorder = recorder
        recorder.begin(rig.session, {})
        await rig.handlers.execute("say", {"line_id": "greet"})
        running = asyncio.ensure_future(rig.run_tool("run_task", {"task": "smile"}))
        await rig.clock.settle()
        await rig.clock.advance(1.0)
        rig.session.hang_up()
        await running
        await rig.session.finish()
        recorder.finish_call(rig.session, rig.session.build_session_result().to_dict())

    asyncio.run(scenario())
    settle(writer)
    assert recorder.failures == 0 and writer.failures == 0
    assert rows(path, "SELECT state FROM task_attempts")[0]["state"] == "cancelled"
    assert rows(path, "SELECT outcome, value FROM task_results")[0] == {"outcome": "cancelled", "value": None}
    assert rows(path, "SELECT state FROM tool_calls WHERE tool_name = 'run_task'")[0]["state"] == "cancelled"
    assert rows(path, "SELECT phase FROM call_sessions")[0]["phase"] == "finished"


# ---- the call never depends on the database ----------------------------------------------------------------------

def comparable(rig, summary):
    return summary, [e["kind"] for e in rig.session.log if e["kind"] != "baseline_source"], list(rig.adapter.said)


def test_a_missing_calls_row_turns_recording_off_and_the_call_is_identical(env, caplog):
    path, writer = env
    plain = CallRig(required_tasks=REQUIRED)
    expected = comparable(plain, run_fixed_call(plain, None))

    recorder = make_recorder(writer)  # no calls row: Agent 2's call service is not writing in this setup
    rig = CallRig(required_tasks=REQUIRED)
    with caplog.at_level("WARNING"):
        got = comparable(rig, run_fixed_call(rig, recorder))
        settle(writer)
    assert got == expected
    assert recorder.enabled is False and recorder.failures == 1
    assert len([r for r in caplog.records if "recording is off" in r.getMessage()]) == 1  # one line, once
    for table in ("call_sessions", "session_baselines", "model_turns", "tool_calls", "task_attempts", "task_results",
                  "call_events", "session_summaries", "answers", "baseline_members"):
        assert count(path, table) == 0, table
    assert count(path, "baseline_snapshots") == 0 and count(path, "policy_definitions") == 0  # one rolled-back job


def test_a_write_failure_in_one_job_does_not_touch_the_call(env, monkeypatch, caplog):
    path, writer = env
    add_call(path)
    plain = CallRig(required_tasks=REQUIRED)
    expected = comparable(plain, run_fixed_call(plain, None))

    def boom(*args, **kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(repo, "record_observation", boom)
    recorder = make_recorder(writer)
    rig = CallRig(required_tasks=REQUIRED)
    with caplog.at_level("WARNING"):
        got = comparable(rig, run_fixed_call(rig, recorder))
        end_call(path)
        settle(writer)
    assert got == expected and recorder.enabled is True
    assert recorder.failures >= 1 and any("observation was not saved" in r.getMessage() for r in caplog.records)
    # the rest of the call was still recorded: the attempts ended and their results exist
    assert count(path, "task_results") == 2 and count(path, "task_observations") == 0
    assert rows(path, "SELECT phase FROM call_sessions")[0]["phase"] == "finished"


def test_a_failing_job_cannot_break_a_shared_transaction(tmp_path):
    """With a shared writer the job runs inside the other writer's transaction: its failure must write nothing and
    must not make that transaction fail (it would roll back Agent 2's rows too)."""
    path = make_db(tmp_path)
    add_call(path)
    other = Writer(path)  # stands in for Agent 2's single writer thread
    try:
        recorder = SessionRecorder("call_t", PERSON, "live", submit=other.submit, now_ms=clock())
        rig = CallRig(required_tasks=REQUIRED)
        rig.session.recorder = recorder
        recorder.begin(rig.session, {})
        recorder._submit("bad job", lambda db: (db.execute("INSERT INTO families VALUES ('half', 0)"),
                                                db.execute("INSERT INTO nonexistent VALUES (1)")))
        other.submit("agent 2 row", lambda db: db.execute("INSERT INTO families VALUES ('agent2', 0)"))
        settle(other)
        assert other.failures == 0 and recorder.failures == 1
        assert count(path, "families", "family_id = 'half'") == 0 and count(path, "families", "family_id = 'agent2'") == 1
        assert count(path, "call_sessions") == 1  # our begin job committed in the same writer
    finally:
        other.close()


def test_with_a_shared_writer_summaries_are_left_to_agent_2_and_learning_waits_for_their_rows(tmp_path):
    path = make_db(tmp_path)
    add_call(path)
    other = Writer(path)
    try:
        recorder = SessionRecorder("call_t", PERSON, "live", submit=other.submit, now_ms=clock(), learn_retry_s=0.02,
                                   learn_retries=500)
        assert recorder.summaries is False  # default OFF when a shared submit is given
        rig = CallRig(required_tasks=REQUIRED)
        run_fixed_call(rig, recorder)
        end_call(path)
        assert other.flush()  # not settle(): learning is still waiting for their summary row
        assert count(path, "session_summaries") == 0  # we wrote none
        assert count(path, "baseline_snapshots", "sessions > 0") == 0  # no final summary yet: not learned
        agent2_final_summary(path, "call_t", "as_usual")  # what record_message does when the family message is made
        settle(other)
        assert count(path, "baseline_snapshots", "sessions > 0") == 2
        assert recorder.failures == 0 and other.failures == 0
    finally:
        other.close()
