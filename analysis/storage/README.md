# analysis/storage: Agent 3's writers for the shared SQLite database

Implements OUR part of `backend/DATABASE_SCHEMA.md` (Agent 2's design; the migration `backend/storage/migrations/0001_initial.sql`
is the only schema and is never edited here). The database is optional: with `DATABASE_PATH` unset nothing in this
folder runs and every call behaves as before.

Status: written and tested on a real migrated SQLite file with fake calls (fake clock, made-up frames, scripted fake
Claude). Never run: a real phone call, Agent 2's store merged together with ours.

## Switching it on

`DATABASE_PATH=/path/to/checkin.sqlite3` (backend `.env`, see `backend/.env.example`). The backend applies the migrations
once at startup and hands the path to `run_backend_call(link, ..., database_path=...)`. That function also takes
`submit=` (below). A call then:

1. reads her usual per task from the database (own read connection, before the call), else from the JSON file;
2. freezes one task definition + one baseline snapshot per task and creates the session row (transaction 2);
3. records turns, tools, attempts, observations, results, answers and summaries as they happen;
4. after the call: finishes the session row and, once the call has ended in the database and the call is eligible,
   creates a NEW baseline snapshot (transaction 8).

**Precondition: the `calls` row.** The schema refuses a session without an answered (`connected`) `calls` row, and
that row belongs to Agent 2's call service. On `main` nothing writes it yet, so on `main` the first write of a call
fails, recording switches itself OFF for that call with ONE log line ("recording is off for call ..."), and the call is
exactly as without a database. With Agent 2's store merged the row exists and recording works.

## Tables we write (owner 3) and the guide's transactions

| Table | Written by | Guide transaction |
|---|---|---|
| `policy_definitions` | `repo.publish_policy` (version = hash of `limits`) | 2 |
| `task_definitions` | `repo.publish_task_definition` (version = metric id + hash of the definition) | 2 |
| `call_sessions` | `start_call_session`, `update_context`, `finish_session` | 2, 4, 8 |
| `baseline_snapshots` | placeholder at start (`ensure_unseeded_snapshot`), new snapshot in `learn_baselines` | 2, 8 |
| `session_baselines` | `start_call_session` (one row per task, never updated) | 2 |
| `model_turns` | `insert_turn` (completed / stale / failed / cancelled) | 3 |
| `tool_calls` | `insert_tool`, `finish_tool` | 3, 6 |
| `task_attempts` | `insert_attempt`, `advance_attempt`, `terminalize_attempt` | 3, 4, 5, 6 |
| `call_events` (server side) | `append_event`: `session.started`, `attempt.started`, `task.observation`, `attempt.finalized`, `resource.changed` | 2, 3, 4, 6 |
| `task_observations` | `record_observation` | 4 |
| `task_results` | `terminalize_attempt` | 6 |
| `answers` | `insert_answer` (one per ask, retries kept), `select_answer` (record_answer) | 3 |
| `session_summaries` | `append_summary` (mid_call / final), switchable | 7 (revision part) |
| `baseline_members` | `learn_baselines` | 8 |

Not ours and not written: `families`, `persons`, `devices`, `calls`, `client_commands`, `family_messages`,
`notification_outbox`, `delivery_attempts`. Of transaction 7 only the summary revision is ours; the message and the
outbox row stay with Agent 2. Transactions 1 and 9 are Agent 2's.

Rules implemented in `repo.py`: definitions published once (identical re-publish is a no-op, other content under the
same id is a `ConflictError`); append-only journal and observations (an identical retry is acknowledged, a conflicting
reuse of an id is refused; positions are allocated inside the write transaction, the same way Agent 2's writer does, so
both can write the same call); terminal results are never replaced (a duplicate terminal request reads and compares:
identical = `duplicate`, different = `ConflictError`); the attempt revision and the session context revision move by
compare-and-update with an affected-row check (`StaleWrite`); stale model turns are kept with state `stale` and their
tool never runs; at most one answer per question is selected (medicine is normalised to yes/no/unclear); a baseline
snapshot a call used is never changed, a new one is created.

## How writing never blocks or breaks the call

`SessionRecorder` (`recorder.py`) is an optional argument of `CallSession` (`recorder=`); `ToolHandlers` and
`ClaudeAgent` reach it through the session. `None` = exactly the old behaviour. Every hook is synchronous, copies plain
data on the event loop and queues ONE transaction; nothing awaits SQLite; nothing raises into the call (hooks are also
wrapped by the callers).

Two ways to write, same job signature:

- **Shared writer (preferred when Agent 2's store is merged).** Pass `submit=` to `run_backend_call`: any callable
  `submit(name, job)` with the signature of `Store._submit` in Agent 2's store, so both packages use ONE writer thread
  and one connection. `job(db)` runs between their `BEGIN IMMEDIATE` and commit. Our jobs run inside a SAVEPOINT: a
  failing job writes nothing and cannot make their transaction fail (no deferred foreign key is left open).
  `summaries` defaults to OFF in this mode (below).
- **Own writer thread.** Without `submit=`, `writer.py` starts one daemon thread per database file (`shared_writer(path)`)
  that owns one connection, takes jobs from a queue, runs each in `BEGIN IMMEDIATE` / commit and rolls back on error.
  `summaries` defaults to ON.

A failed job logs one line and counts (`recorder.failures`, `writer.failures`). A failure of a call-level prerequisite
(the first job: definitions, baselines, session row) turns recording off for the call. A failed observation does not
desynchronize anything: the attempt revision is read at execution time and moves only after a committed job. If an
attempt row itself was not written, its later jobs are skipped and its tool is closed if possible. `flush()` (recorder
and writer) is for tests; `pending_retries()` counts baseline retries waiting for a call to end.

Timestamps are server wall-clock milliseconds taken when the hook runs, never decreasing within a call. Wire ids that are
only unique per call (attempts, events) are stored as `<call_id>.<id>`, like Agent 2's store. The model's real
`tool_use` id is kept as `tool_call_id`; the fixed-order driver (no model) gets synthetic turns (`model_id`
`fixed_order`) and ids `<call>.tool<n>`.

## Her usual from the database, and the JSON fallback

At call start `baselines.read_usual` reads, per task, the newest snapshot of the person and the task definition that has
sessions (a placeholder with 0 sessions does not count) and returns the dict shapes `analysis/pc/store.py` produces
(smile; arms with `hand_left`/`hand_right`; sentence). The database wins for every task that has such a snapshot; the JSON
`BaselineStore` is the fallback and is used for the whole call when the database is off, unreadable or has no snapshot of
her yet. That is said in ONE line: `baselines: json file (the database has no usual for this person yet)` or
`baselines: database for arms, smile`, in the live log and as a `baseline_source` note in the call record.
The JSON file keeps being appended after every call (v9 behaviour), so it stays a complete fallback.

A task that took its usual from the JSON file still needs a frozen snapshot row. It gets an empty placeholder
(`unseeded_<call>_<task>`, 0 sessions, not ready, null bounds, reused by later calls). Because that snapshot is not ready,
the schema's trigger requires such a result to carry NO comparison: the value is stored, `usual_low/high/outside` are NULL
(the comparison is in the finalized event and in the summary).

**After the call (transaction 8).** `finish_call` finishes the session row, then queues `learn_baselines`. It only acts
when the call is eligible: the v9 rules (`CallSession.baseline_update()`: not acted, no confirmed change, no concern, no
outside reading, the task completed with a usable reading) and a final status of `as_usual` or `learning` (the schema
refuses any other member). It creates a NEW snapshot per task with the previous members plus this call's attempt as
`baseline_members`; `UW_BASELINE_NEEDED` is the ready threshold. Agent 2 ends the call row only after `run_backend_call`
returned, and the schema's member trigger needs `calls.state = 'ended'`, our session `finished` and the final summary: the
job therefore waits (`NotReady`) and a timer re-queues it (0.5 s, up to 40 times; then one log line). If the process dies
meanwhile, that call's reading is not added (the JSON file still has it).

**How the demo gets a ready usual.** No seeding tool exists (nothing here invents calls or attempts). Run `needed` real
normal calls (`UW_BASELINE_NEEDED=2` is enough to try it): the first uses the JSON file (or learning), each eligible
call adds a snapshot, and the call after the `needed`-th reads a ready usual from the database. A usual that already
exists ONLY in the JSON file is ignored as soon as the database has a snapshot of her (database wins): either run the
`needed` calls again or leave `DATABASE_PATH` unset for that demo.

## Overlap with Agent 2's writer (`research/agent4_store/store.py`, PR #9) — for the team

- **`session_summaries`**: their `record_message` inserts a summary row for every family message (revision `max+1`). Our
  `append_summary` would add a second row for the same publish, so `summaries=True|False` switches ours. Default ON with our
  own writer, OFF when a shared `submit` is given (then the baseline step waits for THEIR final row). Do not run both ON.
- **`call_events` for resource events**: both write `resource.changed` as `<call>.client_<event_id>` with the same payload;
  whichever comes first wins, the second sees an identical event and does nothing. No switch needed.
- **`call_events` positions**: both allocate `max(position)+1` inside `BEGIN IMMEDIATE`; with two writer threads they
  serialize on the database lock (UNIQUE protects the rest).
- **`client_commands.attempt_id`**: their capture commands and events carry `attempt_id` NULL ("needs Agent 3's sessions").
  The attempt rows now exist (`<call>.<A1>`, created by `attempt_created` before the capture command is sent), so they could
  fill it in.
- **Start of the session row** needs their `record_call_answered` committed first: with ONE shared thread the order is
  guaranteed; with two threads our first job could run before it (no retry is built; recording would switch off).

## For Agent 2: mismatches found (the schema was not changed)

1. **`concern_raised`**: our `SessionResult.status` has it (v7, the agent's `alert_family`); `session_summaries.status` and
   `family_messages.kind` do not. We store `incomplete` and keep the real status in `summary_json.session_status`.
2. **`alert_family` is not in `tool_calls.tool_name`**: no tool row is written; the model turn keeps it (note omitted).
3. **Arms has two ranges (one per hand), a snapshot has one.** Arms snapshots are stored not ready with null bounds, arms
   results with a value and NO comparison; both hands' values are in the `attempt.finalized` event payload (`values`), and the
   next call's arms usual is rebuilt from the members' payloads. A ready arms usual in the database therefore appears as `ready=0`.
4. **Observations are computed on the server** from the phone's JPEG frames, but the observation trigger requires `source='client'`,
   `event_type='task.observation'` and the call's parent device. They are journaled that way, with `"derived_by":
   "server_analysis"` in the payload.
5. **No comparison without a ready snapshot** (trigger `result_matches_attempt`): results of a placeholder or JSON-sourced
   usual carry null bounds, see above.
6. **Baseline members**: the trigger needs `calls.state='ended'`; Agent 2 ends the call after our code returned (handled by the
   retry above). It also refuses an `incomplete` final, which the JSON store accepts, so the database learns from fewer calls.
7. **No `pending` model turn rows**: a turn is written once, complete, when the answer (or the failure) arrives; a crash
   mid-request leaves nothing. `model_one_pending_turn` is never exercised.
8. **IDs**: wire ids are per call (stored `<call>.<id>`); baseline snapshot ids are `snap_<call>_<task>` / `unseeded_<call>_<task>`
   because the session models only accept `[A-Za-z0-9_-]` ids.
9. **`task_results.source_event_id`** is filled only when the interruption's source event is in the journal (a resource event
   journaled as `client_<id>`); otherwise NULL (the wire id stays in the finalized event).
10. **`tool_calls.result_json` of `run_task`** is the `TaskResult` dict (written together with the result), not the "facts" text the
    model saw.
11. **`attempts_one_active` / `tools_one_active`**: if the terminal write of an attempt ever fails, the next attempt of that call
    cannot be recorded (it logs a line; the call goes on).

## Not done

- Startup recovery for OUR tables (open sessions, attempts, tools, pending baseline retries of a dead process): Agent 2's recovery
  closes `calls` only. Not built.
- `family_messages`, the outbox, `no_answer` summaries and client commands: Agent 2's.
- A real phone call, the real Claude, Agent 2's store merged together with this recorder: never run.
- Observations are written once per accepted observation (about one per second); no coalescing.
