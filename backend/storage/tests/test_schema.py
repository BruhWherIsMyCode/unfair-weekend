"""Storage invariants on synthetic records; no providers, recordings or runtime."""

from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest

from storage import apply_migrations, connect_database


class MigrationTests(unittest.TestCase):
    def test_reopen_preserves_data_and_migration_is_repeatable(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "test.sqlite3"
            with closing(connect_database(path)) as db:
                self.assertEqual(apply_migrations(db), ["0001_initial.sql"])
                db.execute("INSERT INTO families VALUES ('synthetic', 0)")
            with closing(connect_database(path)) as db:
                self.assertEqual(apply_migrations(db), [])
                self.assertEqual(db.execute("SELECT family_id FROM families").fetchone()[0], "synthetic")
                self.assertEqual(db.execute("PRAGMA foreign_keys").fetchone()[0], 1)
                self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")

    def test_failed_migration_rolls_back_schema_and_history(self):
        with tempfile.TemporaryDirectory() as directory, closing(connect_database(":memory:")) as db:
            path = Path(directory)
            (path / "0001_broken.sql").write_text("CREATE TABLE partial (id TEXT);\nINVALID SQL;\n")
            with self.assertRaises(sqlite3.OperationalError):
                apply_migrations(db, path)
            self.assertEqual(db.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall(), [])
            self.assertFalse(db.in_transaction)

    def test_edited_migration_or_newer_database_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory, closing(connect_database(":memory:")) as db:
            path = Path(directory)
            migration = path / "0001_test.sql"
            migration.write_text("CREATE TABLE example (id TEXT);\n")
            apply_migrations(db, path)
            migration.write_text("CREATE TABLE example (id INTEGER);\n")
            with self.assertRaisesRegex(ValueError, "history differs"):
                apply_migrations(db, path)
            migration.write_text("CREATE TABLE example (id TEXT);\n")
            (path / "0002_next.sql").write_text("CREATE TABLE second (id TEXT);\n")
            apply_migrations(db, path)
            (path / "0002_next.sql").unlink()
            with self.assertRaisesRegex(ValueError, "newer"):
                apply_migrations(db, path)

    def test_migration_rejects_existing_transaction_and_disabled_foreign_keys(self):
        with closing(connect_database(":memory:")) as db:
            db.execute("BEGIN")
            with self.assertRaisesRegex(ValueError, "outside"):
                apply_migrations(db)
            db.rollback()
            db.execute("PRAGMA foreign_keys = OFF")
            with self.assertRaisesRegex(ValueError, "Foreign keys"):
                apply_migrations(db)

    def test_all_tables_use_strict_types(self):
        with closing(connect_database(":memory:")) as db:
            apply_migrations(db)
            tables = db.execute("PRAGMA table_list").fetchall()
            self.assertTrue(all(row[5] == 1 for row in tables if not row[1].startswith("sqlite_")))
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("INSERT INTO families VALUES ('test', 'not a timestamp')")


class SchemaTests(unittest.TestCase):
    def setUp(self):
        self.db = connect_database(":memory:")
        self.addCleanup(self.db.close)
        apply_migrations(self.db)
        self.db.executemany("INSERT INTO families VALUES (?, 0)", [("f1",), ("f2",)])
        self.db.executemany(
            "INSERT INTO persons VALUES (?, ?, 'Synthetic person', 'en', 0)",
            [("p1", "f1"), ("p2", "f2")],
        )
        self.db.executemany(
            "INSERT INTO devices VALUES (?, ?, ?, ?, 0, NULL)",
            [("parent1", "f1", "p1", "parent"), ("family1", "f1", None, "family"),
             ("parent2", "f2", "p2", "parent"), ("family2", "f2", None, "family")],
        )
        self.db.execute("INSERT INTO policy_definitions VALUES ('test-policy', '{}', 0)")
        # Features are deliberately synthetic, not published product formulas.
        self.db.execute("""
            INSERT INTO task_definitions VALUES (
                'test-arms-v1', 'arms', 'test-v1', 'synthetic_feature_v1', 'test_unit',
                'task_arms', 'repeat_arms', 'camera', 12000,
                '{"formula":"synthetic supplied value for storage tests only"}', 0
            )
        """)

    def call(self, call_id="c1", person="p1", *, label="normal", answered=True, ready=False):
        self.db.execute("""
            INSERT INTO calls (
                call_id, person_id, parent_device_id, label, state, created_at_ms,
                ring_deadline_at_ms, answered_at_ms, call_deadline_at_ms
            ) VALUES (?, ?, ?, ?, ?, 1000, 31000, ?, ?)
        """, (call_id, person, "parent1" if person == "p1" else "parent2", label,
              "connected" if answered else "ringing", 1100 if answered else None,
              181100 if answered else None))
        if answered:
            self.db.execute("""
                INSERT INTO call_sessions VALUES (?, 'running', 'test-script', 'test-policy', 0, '{}', 1100, NULL)
            """, (call_id,))
            self.db.execute("""
                INSERT INTO baseline_snapshots VALUES (?, ?, 'test-arms-v1', ?, 3, ?, ?, ?, 1100)
            """, ("b_" + call_id, person, 3 if ready else 0, int(ready), 0.0 if ready else None, 1.0 if ready else None))
            self.db.execute("INSERT INTO session_baselines VALUES (?, 'arms', 'test-arms-v1', ?)",
                            (call_id, "b_" + call_id))

    def tool(self, call_id="c1", tool_id="t1", *, name="run_task", sequence=0):
        turn_id = "turn_" + tool_id
        self.db.execute("""
            INSERT INTO model_turns VALUES (?, ?, ?, 0, 'synthetic-model', 'completed', '{}', '{}', NULL, 1100, 1101)
        """, (turn_id, call_id, sequence))
        self.db.execute("""
            INSERT INTO tool_calls VALUES (?, ?, ?, ?, '{}', 'running', NULL, 1101, NULL)
        """, (tool_id, call_id, turn_id, name))

    def attempt(self, attempt_id="a1", call_id="c1", tool_id="t1", ordinal=1):
        self.db.execute("""
            INSERT INTO task_attempts (
                attempt_id, call_id, task, definition_id, snapshot_id, tool_call_id,
                ordinal, state, created_at_ms, preparation_deadline_at_ms,
                capture_started_at_ms, window_deadline_at_ms, observation_deadline_at_ms
            ) VALUES (?, ?, 'arms', 'test-arms-v1', ?, ?, ?, 'capturing', 1200, 6200, 1300, 13300, 4300)
        """, (attempt_id, call_id, "b_" + call_id, tool_id, ordinal))

    def running_attempt(self):
        self.call()
        self.tool()
        self.attempt()

    def observation(self, event_id="e1", sequence=0, elapsed=1000, *, attempt="a1", call="c1"):
        position = self.db.execute(
            "SELECT coalesce(max(position), -1) + 1 FROM call_events WHERE call_id = ?", (call,)
        ).fetchone()[0]
        self.db.execute("BEGIN")
        try:
            self.db.execute("""
                INSERT INTO call_events VALUES (?, ?, ?, 'parent1', ?, 'client', 'task.observation', 2300, '{}')
            """, (event_id, call, attempt, position))
            self.db.execute("""
                INSERT INTO task_observations VALUES (?, ?, ?, ?, ?, 'capturing', 'usable', '[]', '[]', 'none')
            """, (event_id, call, attempt, sequence, elapsed))
            self.db.execute("UPDATE task_attempts SET latest_observation_event_id = ? WHERE attempt_id = ?",
                            (event_id, attempt))
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise

    def finish(self, *, attempt="a1", call="c1", outcome="completed", value=0.25, comparison=(None, None, None)):
        self.db.execute("BEGIN")
        try:
            self.db.execute("""
                UPDATE task_attempts SET state = ?, ended_at_ms = 14000,
                    terminal_result_attempt_id = attempt_id WHERE attempt_id = ?
            """, (outcome, attempt))
            interrupted = outcome == "interrupted"
            self.db.execute("""
                INSERT INTO task_results VALUES (?, ?, 'test-arms-v1', ?, ?, ?, ?, ?,
                    ?, ?, ?, NULL, ?, NULL, ?, 14000)
            """, (attempt, call, outcome, "unusable" if interrupted else "usable",
                  '["observations_timeout"]' if interrupted else '[]',
                  "uncertain" if interrupted else "observed", value, *comparison,
                  "observations_timeout" if interrupted else None, 1 if interrupted else None))
            self.db.execute("""
                UPDATE tool_calls SET state = ?, result_json = '{}', ended_at_ms = 14000
                WHERE tool_call_id = (SELECT tool_call_id FROM task_attempts WHERE attempt_id = ?)
            """, ("interrupted" if interrupted else "completed", attempt))
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise

    def summary(self, call="c1", revision=1, status="learning", phase="final"):
        self.db.execute("INSERT INTO session_summaries VALUES (?, ?, ?, ?, '{}', 15000)",
                        (call, revision, status, phase))

    def close_session(self, call="c1"):
        self.db.execute("UPDATE calls SET state = 'ended', end_reason = 'completed', ended_at_ms = 15000 WHERE call_id = ?", (call,))
        self.db.execute("UPDATE call_sessions SET phase = 'finished', finished_at_ms = 15000 WHERE call_id = ?", (call,))

    def test_only_one_active_call_and_terminal_state_is_immutable(self):
        self.call(answered=False)
        with self.assertRaises(sqlite3.IntegrityError):
            self.call("c2", answered=False)
        self.db.execute("UPDATE calls SET state = 'no_answer', end_reason = 'ring_timeout', ended_at_ms = 31000 WHERE call_id = 'c1'")
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("UPDATE calls SET state = 'ringing', end_reason = NULL, ended_at_ms = NULL WHERE call_id = 'c1'")
        self.call("c2", answered=False)

    def test_session_cannot_start_before_answer(self):
        self.call(answered=False)
        with self.assertRaisesRegex(sqlite3.IntegrityError, "answered"):
            self.db.execute("INSERT INTO call_sessions VALUES ('c1', 'running', 's', 'test-policy', 0, '{}', 1100, NULL)")

    def test_devices_and_call_parent_cannot_cross_families(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("INSERT INTO devices VALUES ('bad', 'f2', 'p1', 'parent', 0, NULL)")
        self.call(answered=False)
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("UPDATE calls SET parent_device_id = 'parent2' WHERE call_id = 'c1'")

    def test_attempt_requires_running_task_tool_and_only_one_active_attempt(self):
        self.call()
        self.tool(name="say")
        with self.assertRaisesRegex(sqlite3.IntegrityError, "run_task"):
            self.attempt()
        self.db.execute("UPDATE tool_calls SET state = 'completed', result_json = '{}', ended_at_ms = 1102 WHERE tool_call_id = 't1'")
        self.tool(tool_id="t2", sequence=1)
        self.attempt(tool_id="t2")
        with self.assertRaises(sqlite3.IntegrityError):
            self.attempt("a2", tool_id="t2", ordinal=2)

    def test_snapshot_update_preserves_old_observations_and_journal(self):
        self.running_attempt()
        self.observation()
        self.observation("e2", 1, 2000)
        self.assertEqual(self.db.execute("SELECT latest_observation_event_id FROM task_attempts").fetchone()[0], "e2")
        self.assertEqual(self.db.execute("SELECT count(*) FROM task_observations").fetchone()[0], 2)
        for table in ("call_events", "task_observations"):
            with self.assertRaises(sqlite3.IntegrityError):
                self.db.execute(f"DELETE FROM {table}")
            with self.assertRaises(sqlite3.IntegrityError):
                self.db.execute(f"UPDATE {table} SET event_id = 'replacement'")

    def test_duplicate_or_reordered_observation_cannot_overwrite_snapshot(self):
        self.running_attempt()
        self.observation(sequence=2, elapsed=2000)
        for event, sequence, elapsed in [("e1", 2, 2000), ("old", 1, 1000), ("backwards", 3, 1500)]:
            with self.assertRaises(sqlite3.IntegrityError):
                self.observation(event, sequence, elapsed)
        self.assertEqual(self.db.execute("SELECT latest_observation_event_id FROM task_attempts").fetchone()[0], "e1")
        self.assertEqual(self.db.execute("SELECT count(*) FROM call_events").fetchone()[0], 1)

    def test_latest_observation_cannot_point_to_another_attempt(self):
        self.running_attempt()
        self.observation()
        self.finish()
        self.tool(tool_id="t2", sequence=1)
        self.attempt("a2", tool_id="t2", ordinal=2)
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("UPDATE task_attempts SET latest_observation_event_id = 'e1' WHERE attempt_id = 'a2'")

    def test_terminalization_requires_result_in_same_transaction(self):
        self.running_attempt()
        self.db.execute("BEGIN")
        self.db.execute("UPDATE task_attempts SET state = 'completed', ended_at_ms = 14000, terminal_result_attempt_id = attempt_id")
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.commit()
        self.db.rollback()
        self.assertEqual(self.db.execute("SELECT state FROM task_attempts").fetchone()[0], "capturing")

    def test_learning_reading_has_null_comparison_and_result_cannot_be_replaced(self):
        self.running_attempt()
        self.observation()
        self.finish()
        row = self.db.execute("SELECT value, usual_low, usual_high, outside FROM task_results").fetchone()
        self.assertEqual(tuple(row), (0.25, None, None, None))
        for sql in ("UPDATE task_results SET value = 4", "DELETE FROM task_results",
                    "UPDATE task_attempts SET state = 'capturing'", "UPDATE tool_calls SET result_json = '{\"new\":true}'"):
            with self.assertRaises(sqlite3.IntegrityError):
                self.db.execute(sql)
        with self.assertRaises(sqlite3.IntegrityError):
            self.observation("late", 1, 2000)

    def test_interrupted_attempt_has_no_numeric_reading(self):
        self.running_attempt()
        with self.assertRaises(sqlite3.IntegrityError):
            self.finish(outcome="interrupted", value=0.3)
        self.finish(outcome="interrupted", value=None)
        row = self.db.execute("SELECT outcome, value, interruption_code FROM task_results").fetchone()
        self.assertEqual(tuple(row), ("interrupted", None, "observations_timeout"))

    def test_result_comparison_uses_frozen_bounds_and_correct_outside_flag(self):
        self.call(ready=True)
        self.tool()
        self.attempt()
        for comparison in [(None, None, None), (0.1, 1.0, 1), (0.0, 1.0, 0)]:
            with self.assertRaises(sqlite3.IntegrityError):
                self.finish(value=2.0, comparison=comparison)
        self.finish(value=2.0, comparison=(0.0, 1.0, 1))
        self.assertEqual(self.db.execute("SELECT outside FROM task_results").fetchone()[0], 1)

    def test_nonfinite_measurement_cannot_be_stored(self):
        self.running_attempt()
        with self.assertRaises(sqlite3.IntegrityError):
            self.finish(value=float("inf"))
        self.finish()

    def test_wrong_client_device_cannot_write_call_events(self):
        self.call()
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("INSERT INTO call_events VALUES ('wrong', 'c1', NULL, 'parent2', 0, 'client', 'resource.changed', 2000, '{}')")

    def test_hangup_rejects_observations_even_before_attempt_cleanup(self):
        self.running_attempt()
        self.db.execute("UPDATE calls SET state = 'ended', end_reason = 'hangup', ended_at_ms = 2000")
        with self.assertRaises(sqlite3.IntegrityError):
            self.observation()
        self.assertEqual(self.db.execute("SELECT count(*) FROM call_events").fetchone()[0], 0)

    def test_commands_preserve_identity_acknowledgment_and_terminal_state(self):
        self.running_attempt()
        self.db.execute("INSERT INTO call_events VALUES ('cmd-event', 'c1', 'a1', 'parent1', 0, 'server', 'command.execute', 1200, '{}')")
        self.db.execute("""
            INSERT INTO client_commands VALUES ('cmd1', 'cmd-event', 'c1', 'a1', 'parent1',
                'task.start', 'queued', 1200, 6200, NULL, NULL, NULL, NULL)
        """)
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("UPDATE client_commands SET device_id = 'parent2'")
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("UPDATE client_commands SET state = 'started'")
        self.db.execute("UPDATE client_commands SET state = 'started', sent_at_ms = 1250, acknowledged_at_ms = 1300")
        self.db.execute("UPDATE client_commands SET state = 'completed', ended_at_ms = 14000")
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("UPDATE client_commands SET state = 'queued', ended_at_ms = NULL")

    def test_summary_history_cannot_reopen_final_or_erase_confirmed_change(self):
        self.call()
        self.summary(status="change_noticed", phase="mid_call")
        with self.assertRaises(sqlite3.IntegrityError):
            self.summary(revision=2, status="as_usual")
        self.summary(revision=2, status="change_noticed")
        with self.assertRaises(sqlite3.IntegrityError):
            self.summary(revision=3, status="change_noticed", phase="mid_call")

    def test_baseline_and_definition_versions_are_frozen(self):
        self.running_attempt()
        for sql in ("UPDATE baseline_snapshots SET needed = 4", "UPDATE task_definitions SET unit = 'other'",
                    "UPDATE policy_definitions SET definition_json = '{\"new\":true}'",
                    "UPDATE session_baselines SET snapshot_id = 'other'", "DELETE FROM session_baselines"):
            with self.assertRaises(sqlite3.IntegrityError):
                self.db.execute(sql)

    def test_baseline_members_count_calls_and_exclude_acted_or_changed_sessions(self):
        for label, status in [("normal", "learning"), ("acted", "learning"), ("normal", "change_noticed")]:
            with self.subTest(label=label, status=status):
                suffix = label + status
                call, attempt, tool = "c_" + suffix, "a_" + suffix, "t_" + suffix
                self.call(call, label=label)
                self.tool(call, tool)
                self.attempt(attempt, call, tool)
                self.finish(attempt=attempt, call=call)
                self.close_session(call)
                self.summary(call, status=status)
                snapshot = "next_" + suffix
                self.db.execute("INSERT INTO baseline_snapshots VALUES (?, 'p1', 'test-arms-v1', 1, 3, 0, NULL, NULL, 16000)", (snapshot,))
                values = (snapshot, attempt, call)
                if label == "normal" and status == "learning":
                    self.db.execute("INSERT INTO baseline_members VALUES (?, ?, ?, 'test-arms-v1')", values)
                    with self.assertRaises(sqlite3.IntegrityError):
                        self.db.execute("INSERT INTO baseline_members VALUES (?, ?, ?, 'test-arms-v1')", values)
                    with self.assertRaises(sqlite3.IntegrityError):
                        self.db.execute("INSERT INTO baseline_members VALUES (?, ?, ?, 'test-arms-v1')", ("b_" + call, attempt, call))
                else:
                    with self.assertRaises(sqlite3.IntegrityError):
                        self.db.execute("INSERT INTO baseline_members VALUES (?, ?, ?, 'test-arms-v1')", values)

    def test_one_family_message_keeps_id_and_confirmed_change_on_final_update(self):
        self.call()
        self.summary(status="change_noticed", phase="mid_call")
        text = "A change from her usual was noticed. Call her now. If she seems unwell, call 112."
        self.db.execute("INSERT INTO family_messages VALUES ('m1', 'c1', 'f1', 1, 'change_noticed', 'test-template', ?, 15000, 15000)", (text,))
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("INSERT INTO family_messages SELECT 'm2', call_id, family_id, summary_revision, kind, template_id, text, created_at_ms, updated_at_ms FROM family_messages")
        self.summary(revision=2, status="change_noticed")
        self.db.execute("UPDATE family_messages SET summary_revision = 2, updated_at_ms = 16000 WHERE message_id = 'm1'")
        self.assertEqual(self.db.execute("SELECT message_id, summary_revision FROM family_messages").fetchone()[:], ("m1", 2))
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("UPDATE family_messages SET kind = 'as_usual', summary_revision = 3")

    def test_notification_recipient_and_idempotency_key_are_enforced(self):
        self.call(answered=False)
        sql = """
            INSERT INTO notification_outbox VALUES (?, ?, 'incoming_call', 'c1', ?,
                NULL, NULL, '{}', 'queued', 0, 1000, NULL, 1000, 1000)
        """
        self.db.execute(sql, ("n1", "incoming:c1:parent1", "parent1"))
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("UPDATE notification_outbox SET device_id = 'parent2'")
        for values in [("n2", "incoming:c1:parent1", "parent1"), ("n3", "other", "parent2"),
                       ("n4", "wrong-role", "family1"), ("n5", "different-key-same-call", "parent1")]:
            with self.assertRaises(sqlite3.IntegrityError):
                self.db.execute(sql, values)


if __name__ == "__main__":
    unittest.main()
