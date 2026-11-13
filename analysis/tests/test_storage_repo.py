"""The repository rules on a real migrated file: versioned definitions are published once, the journal and results are
append-only (identical retry = read and compare, different content = refused), revisions move by compare-and-update."""
from __future__ import annotations

import json
from contextlib import closing

import pytest

from analysis.session import limits
from analysis.storage import Writer, definitions, repo
from analysis.storage.util import ConflictError, StaleWrite, StorageError, connect, transact
from analysis.tests.agent_helpers import CallRig
from analysis.tests.storage_helpers import (PERSON, REQUIRED, add_call, count, end_call, make_db, make_recorder, rows,
                                            run_fixed_call, settle)


@pytest.fixture()
def recorded(tmp_path):
    """A finished, eligible fake call in a database; yields (path, rig)."""
    path = make_db(tmp_path)
    writer = Writer(path)
    add_call(path)
    recorder = make_recorder(writer)
    rig = CallRig(required_tasks=REQUIRED)
    run_fixed_call(rig, recorder)
    end_call(path)
    settle(writer)
    assert recorder.failures == 0 and writer.failures == 0
    yield path, rig
    writer.close()


def run(path, fn):
    with closing(connect(path)) as db:
        return transact(db, fn)


def test_definitions_are_published_once_and_a_changed_one_is_refused(recorded):
    path, rig = recorded
    row = definitions.task_rows(rig.session.modules)["smile"]
    assert run(path, lambda db: definitions.publish_task_definition(db, row, 1)) is False  # identical: a no-op
    changed = dict(row, unit="other_unit")
    with pytest.raises(ConflictError):
        run(path, lambda db: definitions.publish_task_definition(db, changed, 1))
    changed = dict(row, definition_json=dict(row["definition_json"], extra=1))
    with pytest.raises(ConflictError):
        run(path, lambda db: definitions.publish_task_definition(db, changed, 1))
    version, policy = definitions.policy_definition()
    assert run(path, lambda db: definitions.publish_policy(db, version, policy, 1)) is False
    with pytest.raises(ConflictError):
        run(path, lambda db: definitions.publish_policy(db, version, {"limits": {}}, 1))
    assert count(path, "task_definitions") == 2 and count(path, "policy_definitions") == 1


def test_the_version_carries_the_content_so_a_changed_formula_is_a_new_version(recorded):
    path, rig = recorded
    arms = rig.session.modules["arms"]
    before = definitions.task_rows(rig.session.modules)["arms"]
    arms.phases = tuple(arms.phases[:-1])  # a different hold layout is a different feature
    after = definitions.task_rows(rig.session.modules)["arms"]
    assert before["definition_id"] != after["definition_id"] and before["metric_id"] == after["metric_id"]
    assert after["version"].startswith(after["metric_id"] + ".")


def test_the_journal_acknowledges_an_identical_retry_and_refuses_a_conflicting_one(recorded):
    path, _ = recorded
    event = rows(path, "SELECT event_id, call_id, source, event_type, payload_json, position FROM call_events "
                       "WHERE event_type = 'session.started'")[0]
    payload = json.loads(event["payload_json"])
    before = count(path, "call_events")
    assert run(path, lambda db: repo.append_event(db, "call_t", event["event_id"], "session.started", payload, 1)) is False
    with pytest.raises(ConflictError):
        run(path, lambda db: repo.append_event(db, "call_t", event["event_id"], "session.started",
                                               dict(payload, policy_version="other"), 1))
    assert run(path, lambda db: repo.append_event(db, "call_t", "call_t.new", "note", {"a": 1}, 1)) is True
    assert count(path, "call_events") == before + 1
    assert rows(path, "SELECT max(position) AS p FROM call_events")[0]["p"] == before  # next position, no gaps


def terminal_args(path, task):
    attempt = rows(path, "SELECT attempt_id, tool_call_id FROM task_attempts WHERE task = ?", (task,))[0]
    payload = json.loads(rows(path, "SELECT payload_json FROM call_events WHERE event_type = 'attempt.finalized' "
                                    "AND attempt_id = ?", (attempt["attempt_id"],))[0]["payload_json"])
    return attempt, payload


def test_a_duplicate_terminal_result_is_read_and_compared_a_conflicting_one_is_refused(recorded):
    path, _ = recorded
    attempt, payload = terminal_args(path, "smile")
    stored = rows(path, "SELECT * FROM task_results WHERE attempt_id = ?", (attempt["attempt_id"],))[0]

    def terminalize(result):
        return run(path, lambda db: repo.terminalize_attempt(
            db, call_id="call_t", attempt_id=attempt["attempt_id"], result=result, values=None, expected_revision=99,
            tool_call_id=attempt["tool_call_id"], stamp=5, decision={}))

    assert terminalize(payload["result"]) == "duplicate"  # identical: nothing written, no revision needed
    other = json.loads(json.dumps(payload["result"]))
    other["reading"]["value"] = other["reading"]["value"] + 1.0
    with pytest.raises(ConflictError):
        terminalize(other)
    other = json.loads(json.dumps(payload["result"]))
    other["outcome"], other["performance"], other["reading"] = "cancelled", "uncertain", None
    other["quality"] = {"status": "unusable", "reasons": ["call_ended"]}
    with pytest.raises(ConflictError):
        terminalize(other)
    assert rows(path, "SELECT * FROM task_results WHERE attempt_id = ?", (attempt["attempt_id"],))[0] == stored
    assert count(path, "task_results") == 2


def test_revisions_move_by_compare_and_update(recorded):
    path, _ = recorded
    attempt, _payload = terminal_args(path, "smile")
    with pytest.raises(StaleWrite):  # the attempt is terminal: nothing may advance it
        run(path, lambda db: repo.advance_attempt(db, attempt["attempt_id"], 0, "stopping", 9, window_ms=5000,
                                                  stall_ms=3000, finalize_extra_ms=20000))
    with pytest.raises(StaleWrite):  # the session is finished and its context never goes backwards
        run(path, lambda db: repo.update_context(db, "call_t", 1, {}))
    with pytest.raises(StorageError):  # a terminal tool keeps its result
        run(path, lambda db: repo.finish_tool(db, attempt["tool_call_id"], "completed", {"other": True}, 9))
    result = json.loads(rows(path, "SELECT result_json FROM tool_calls WHERE tool_call_id = ?",
                             (attempt["tool_call_id"],))[0]["result_json"])
    assert run(path, lambda db: repo.finish_tool(db, attempt["tool_call_id"], "completed", result, 9)) is False


def test_the_placeholder_snapshot_is_reused_and_learning_twice_adds_nothing(recorded):
    path, _ = recorded
    definition = rows(path, "SELECT definition_id FROM task_definitions WHERE task = 'smile'")[0]["definition_id"]
    placeholder = run(path, lambda db: repo.ensure_unseeded_snapshot(db, PERSON, definition, "call_other", "smile", 5, 3))
    assert placeholder.startswith("unseeded_call_t_") and count(path, "baseline_snapshots", "sessions = 0") == 2
    items = {r["task"]: r["attempt_id"] for r in rows(path, "SELECT task, attempt_id FROM task_attempts")}
    again = run(path, lambda db: repo.learn_baselines(db, call_id="call_t", person_id=PERSON, label="live", needed=5,
                                                      items=items, stamp=99))
    assert again == [] and count(path, "baseline_snapshots", "sessions > 0") == 2
    assert count(path, "baseline_members") == 2


def test_learning_waits_until_the_call_is_ended_and_refuses_an_unfinished_one(tmp_path):
    path = make_db(tmp_path)
    writer = Writer(path)
    try:
        add_call(path)
        recorder = make_recorder(writer, learn_retries=0)  # no retry: the first NotReady is final
        run_fixed_call(CallRig(required_tasks=REQUIRED), recorder)
        settle(writer)
        assert recorder.failures == 1 and count(path, "baseline_snapshots", "sessions > 0") == 0
        items = {r["task"]: r["attempt_id"] for r in rows(path, "SELECT task, attempt_id FROM task_attempts")}
        with pytest.raises(Exception, match="not ended"):
            run(path, lambda db: repo.learn_baselines(db, call_id="call_t", person_id=PERSON, label="live", needed=5,
                                                      items=items, stamp=99))
    finally:
        writer.close()


def test_a_second_selected_answer_moves_the_selection_and_medicine_is_normalised(recorded):
    path, _ = recorded
    assert count(path, "answers", "question_id = 'medicine' AND selected = 1") == 1
    run(path, lambda db: repo.select_answer(db, "call_t", "medicine", "Maybe, I think so"))
    row = rows(path, "SELECT normalized_value, selected FROM answers WHERE question_id = 'medicine'")[0]
    assert row == {"normalized_value": "unclear", "selected": 1}  # never free text for the medicine question
    assert run(path, lambda db: repo.select_answer(db, "call_t", "feeling", "A" * 5000)) is not None
    assert len(rows(path, "SELECT normalized_value FROM answers WHERE question_id = 'feeling'")[0]["normalized_value"]) == 2000
    assert limits.ANSWER_KEY["q_medicine"] == "medicine"
