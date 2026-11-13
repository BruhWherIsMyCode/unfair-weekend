"""SessionRecorder: what the session and the agent loop call at their events; it writes our tables of the shared
SQLite database without ever blocking, or failing, the call.

Rules (analysis/storage/README.md has the long version):
- Every hook is synchronous, cheap and fire-and-forget. It copies plain data on the event loop, then hands ONE
  transaction to `submit(name, job)`: either a callable supplied by the backend (the same signature as
  `Store._submit` of Agent 2's store, so both share one writer thread) or the `Writer` of this package (its own
  thread and connection). Nothing awaits SQLite; nothing raises into the call.
- A failed write logs one line and counts (`failures`). The first failure of a call-level prerequisite (for example the
  `calls` row of Agent 2 is missing or not connected) turns recording OFF for the call with one log line.
- Each job runs inside a SAVEPOINT: a failing job writes nothing, also when the enclosing transaction belongs to a
  shared writer, and is never able to make that transaction fail.
- Order inside a call is the submission order. Timestamps are server wall-clock milliseconds, never decreasing.
"""
from __future__ import annotations

import json
import logging
import threading
from typing import Any, Callable, Dict, List, Optional

from analysis import config
from analysis.session import limits, rules
from analysis.storage import definitions, repo
from analysis.storage.util import NotReady, StorageError, now_ms as _wall_ms, stored_id
from analysis.storage.writer import Writer

log = logging.getLogger(__name__)

_ORDER = {"preparing": 0, "capturing": 1, "finalizing": 2, "stopping": 3, "terminal": 4}
_PENDING = {"n": 0}
_PENDING_LOCK = threading.Lock()


def pending_retries() -> int:
    with _PENDING_LOCK:
        return _PENDING["n"]


def _tool_outcome(payload: Dict[str, Any]) -> str:
    error = payload.get("error")
    if error is None:
        return repo.TOOL_END.get(payload.get("outcome"), "completed") if payload.get("task") else "completed"
    if error == "call ended":
        return "cancelled"
    if str(error).startswith(("invalid input", "not allowed")):
        return "rejected"
    return "failed"


def _redact_tool_result(name: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(payload)
    if name == "ask" and out.get("transcript"):
        out["transcript"] = "<stored in answers>"
    return out


def _redact_json_text(text: Any) -> Any:
    """A tool_result body for model_turns.request_json: no transcript, short."""
    if not isinstance(text, str):
        return str(text)[:300]
    try:
        body = json.loads(text)
    except ValueError:
        return text[:300]
    if isinstance(body, dict):
        for key in ("transcript", "speech"):
            if body.get(key):
                body[key] = "<omitted>"
    return json.dumps(body, sort_keys=True)[:800]


def _redact_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The relevant context of a request: the last two messages, tool ids and structured results only."""
    out: List[Dict[str, Any]] = []
    for message in (messages or [])[-2:]:
        content = message.get("content")
        blocks: List[Dict[str, Any]] = []
        if isinstance(content, str):
            blocks.append({"type": "text", "text": content[:200]})
        elif isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                kind = block.get("type")
                if kind == "tool_result":
                    item = {"type": "tool_result", "tool_use_id": block.get("tool_use_id"),
                            "content": _redact_json_text(block.get("content"))}
                    if block.get("is_error"):
                        item["is_error"] = True
                    blocks.append(item)
                elif kind == "tool_use":
                    blocks.append({"type": "tool_use", "id": block.get("id"), "name": block.get("name")})
                elif kind == "text":
                    blocks.append({"type": "text", "text": str(block.get("text", ""))[:200]})
        out.append({"role": message.get("role"), "content": blocks})
    return out


class SessionRecorder:
    def __init__(self, call_id: str, person_id: str, label: str, *, submit: Optional[Callable[[str, Any], None]] = None,
                 writer: Optional[Writer] = None, summaries: Optional[bool] = None,
                 now_ms: Optional[Callable[[], int]] = None, needed: Optional[int] = None,
                 script_version: str = definitions.SCRIPT_VERSION, learn_retry_s: float = 0.5,
                 learn_retries: int = 40) -> None:
        if submit is None and writer is None:
            raise ValueError("a SessionRecorder needs a submit callable or a Writer")
        self.shared = submit is not None
        self._submit_fn = submit if submit is not None else writer.submit  # type: ignore[union-attr]
        # Agent 2's store also writes session_summaries (record_message). With their writer shared, ours is OFF.
        self.summaries = (not self.shared) if summaries is None else bool(summaries)
        self.call_id, self.person_id, self.label = call_id, person_id, label
        self.needed = int(needed) if needed is not None else config.BASELINE_NEEDED
        self.script_version = script_version
        self.learn_retry_s, self.learn_retries = learn_retry_s, learn_retries
        self.enabled = True
        self.failures = 0
        self._now = now_ms or _wall_ms
        self._last = 0
        self._stamp_lock = threading.Lock()
        self._defs: Dict[str, Dict[str, Any]] = {}
        self._att: Dict[str, Dict[str, Any]] = {}
        self._dead: set = set()
        self._tool: Optional[Dict[str, Any]] = None
        self._bind: Optional[Dict[str, Any]] = None
        self._turn_seq = 0
        self._turn_t0 = 0
        self._turn_ctx = 0
        self._tool_seq = 0
        self._ctx_rev = 0

    # ---- plumbing -------------------------------------------------------------------------------------------------

    def _stamp(self) -> int:
        with self._stamp_lock:
            self._last = max(int(self._now()), self._last)
            return self._last

    def _failed(self, name: str, exc: BaseException, prerequisite: bool, attempt: Optional[str]) -> None:
        self.failures += 1
        detail = "%s: %s" % (type(exc).__name__, str(exc)[:120])
        if prerequisite:
            self.enabled = False
            log.warning("recording is off for call %s (%s was not saved: %s); the call goes on", self.call_id, name,
                        detail)
            return
        if attempt is not None:
            self._dead.add(attempt)
        log.warning("recording: %s was not saved for call %s (%s)", name, self.call_id, detail)

    def _submit(self, name: str, fn: Callable[[Any], None], *, prerequisite: bool = False,
                attempt: Optional[str] = None, check_attempts: bool = False,
                fallback: Optional[Callable[[Any], None]] = None, kills_attempt: bool = False,
                after: Optional[Callable[[], None]] = None) -> None:
        """Queue one transaction body. `attempt`: jobs of an attempt whose creation failed are skipped (or replaced by
        `fallback`); `kills_attempt`: a failure of this job is that creation failure. `after` runs on the writer
        thread only when the job really committed its changes (the attempt revision moves only then)."""
        def job(db: Any) -> None:
            if not self.enabled:
                return
            body = fn
            if attempt is not None and attempt in self._dead:
                if fallback is None:
                    return
                body = fallback
            try:
                db.execute("SAVEPOINT uw_rec")
                try:
                    body(db)
                    if check_attempts and db.execute("PRAGMA foreign_key_check(task_attempts)").fetchall():
                        raise StorageError("a deferred foreign key was left open")
                except BaseException:
                    db.execute("ROLLBACK TO uw_rec")
                    db.execute("RELEASE uw_rec")
                    raise
                db.execute("RELEASE uw_rec")
            except Exception as exc:  # never into the transaction of the writer, never into the call
                self._failed(name, exc, prerequisite, attempt if kills_attempt else None)
                return
            if after is not None and body is fn:
                after()

        try:
            self._submit_fn(name, job)
        except Exception as exc:
            self._failed(name, exc, prerequisite, attempt)

    def flush(self, timeout: float = 10.0) -> bool:
        """Wait until everything submitted so far is committed (tests)."""
        done = threading.Event()
        self._submit_fn("flush", lambda db: done.set())
        return done.wait(timeout)

    def _context(self, session: Any) -> Dict[str, Any]:
        seqs = getattr(session, "_resource_seq", {}) or {}
        budgets: Dict[str, Any] = {}
        for task in limits.TASKS:
            records = session.records_for(task)
            if records:
                budgets[task] = {"attempts": len(records), "usable": sum(1 for r in records if r.usable),
                                 "attempts_left": rules.attempts_left(records)}
        answers = session.answers
        return {"person_id": session.person_id, "label": session.label,
                "required_tasks": list(session.required_tasks),
                "resources": {r: {"state": i.state, "reason": i.reason, "event_id": i.event_id,
                                  "sequence": seqs.get(r)} for r, i in session.resources.items()},
                "budgets": budgets,
                "answers": {"medicine": answers.get("medicine"), "feeling": bool(answers.get("feeling")),
                            "unusual": bool(answers.get("unusual"))},
                "concern": session.concern["reason"] if session.concern else None,
                "message_id": session.message_id, "event_version": session.event_version}

    def context(self, session: Any) -> None:
        """Something in the conversation state changed (for example a concern was raised): move the snapshot on."""
        if self.enabled:
            self._bump_context(session)

    def _bump_context(self, session: Any) -> None:
        self._ctx_rev += 1
        revision, snapshot = self._ctx_rev, self._context(session)
        self._submit("context", lambda db: repo.update_context(db, self.call_id, revision, snapshot))

    # ---- transaction 2: answer and prepare ----------------------------------------------------------------------------

    def begin(self, session: Any, frozen_snapshots: Optional[Dict[str, str]] = None) -> None:
        """Publish the definitions, freeze one definition + baseline per task and create the session row.
        `frozen_snapshots`: task -> database source snapshot id the call's usual came from. A changed learning
        target publishes a new snapshot from those same members before freezing; any other task gets the empty
        placeholder snapshot (its usual came from the JSON file)."""
        frozen_snapshots = dict(frozen_snapshots or {})
        rows = definitions.task_rows(session.modules)
        self._defs = rows
        policy_version, policy = definitions.policy_definition()
        stamp = self._stamp()
        snapshot = self._context(session)
        snapshot["baseline_source"] = {t: ("database" if t in frozen_snapshots else "json") for t in rows}

        def fn(db: Any) -> None:
            definitions.publish_policy(db, policy_version, policy, stamp)
            frozen: Dict[str, Any] = {}
            for task, row in rows.items():
                definitions.publish_task_definition(db, row, stamp)
                source_id = frozen_snapshots.get(task)
                snapshot_id = repo.ensure_configured_snapshot(db, source_id, task, self.needed, stamp) if source_id \
                    else repo.ensure_unseeded_snapshot(
                        db, self.person_id, row["definition_id"], self.call_id, task, self.needed, stamp)
                frozen[task] = (row["definition_id"], snapshot_id)
            repo.start_call_session(db, call_id=self.call_id, policy_version=policy_version,
                                    script_version=self.script_version, snapshot=snapshot, stamp=stamp, frozen=frozen)

        self._submit("begin call", fn, prerequisite=True)

    # ---- model turns and tools (transaction 3) ------------------------------------------------------------------------

    def turn_started(self) -> None:
        self._turn_t0 = self._stamp()
        self._turn_ctx = self._ctx_rev

    def _turn_row(self, *, state: str, model_id: str, request: Dict[str, Any], response: Optional[Dict[str, Any]],
                  error_code: Optional[str], created: Optional[int] = None) -> Dict[str, Any]:
        sequence = self._turn_seq
        self._turn_seq += 1
        stamp = self._stamp()
        return {"turn_id": "%s.turn%d" % (self.call_id, sequence), "call_id": self.call_id, "sequence": sequence,
                "context_revision": self._ctx_rev if created is None else self._turn_ctx, "model_id": model_id,
                "state": state, "request": request, "response": response, "error_code": error_code,
                "created_at_ms": stamp if created is None else min(created, stamp), "ended_at_ms": stamp}

    def _flush_bind(self) -> None:
        bind, self._bind = self._bind, None
        if bind is not None:
            turn = bind["turn"]
            self._submit("model turn", lambda db: repo.insert_turn(db, turn))

    def model_turn(self, *, state: str, messages: List[Dict[str, Any]], content: Optional[List[Dict[str, Any]]] = None,
                   stop_reason: Optional[str] = None, error_code: Optional[str] = None, model_id: str = "unknown",
                   tool_use: Optional[Dict[str, Any]] = None) -> None:
        """One finished Claude request. state: completed / stale / failed / cancelled. A completed turn whose tool is
        about to run is written WITH that tool (one transaction); every other turn is written at once. A stale turn
        keeps its output but no tool ever follows it."""
        self._flush_bind()
        request = {"turn": self._turn_seq + 1, "message_count": len(messages or []),
                   "context": _redact_messages(messages)}
        response = {"content": content or [], "stop_reason": stop_reason} if state in ("completed", "stale") else None
        turn = self._turn_row(state=state, model_id=model_id, request=request, response=response,
                              error_code=error_code, created=self._turn_t0 or None)
        if state == "completed" and tool_use and tool_use.get("name") in repo.DB_TOOLS:
            self._bind = {"turn": turn, "tool_use_id": tool_use["id"], "tool_name": tool_use["name"]}
            return
        self._submit("model turn", lambda db: repo.insert_turn(db, turn))

    def _take_turn(self, name: str) -> Dict[str, Any]:
        """(turn row, tool id) for a tool that starts now: the Claude turn bound to it, or a fixed-order turn."""
        bind, self._bind = self._bind, None
        if bind is not None and bind["tool_name"] == name:
            return {"turn": bind["turn"], "tool_id": bind["tool_use_id"]}
        if bind is not None:
            turn = bind["turn"]
            self._submit("model turn", lambda db: repo.insert_turn(db, turn))
        turn = self._turn_row(state="completed", model_id="fixed_order", request={"driver": "fixed_order"},
                              response={"tool": name}, error_code=None)
        self._tool_seq += 1
        return {"turn": turn, "tool_id": "%s.tool%d" % (self.call_id, self._tool_seq)}

    def _new_tool(self, name: str, args: Any) -> Dict[str, Any]:
        taken = self._take_turn(name)
        stamp = self._stamp()
        return {"id": taken["tool_id"], "name": name, "args": args if isinstance(args, dict) else {},
                "turn": taken["turn"], "created": stamp, "persisted": False, "done": False,
                "stored": name in repo.DB_TOOLS}

    def _tool_row(self, tool: Dict[str, Any]) -> Dict[str, Any]:
        return {"tool_call_id": tool["id"], "call_id": self.call_id, "turn_id": tool["turn"]["turn_id"],
                "name": tool["name"], "arguments": tool["args"], "created_at_ms": tool["created"]}

    def tool_started(self, name: str, args: Any) -> None:
        if not self.enabled:
            return
        tool = self._new_tool(name, args)
        self._tool = tool
        if not tool["stored"]:  # alert_family has no tool row: its turn is kept, the concern lives in the summary
            turn = tool["turn"]
            self._submit("model turn", lambda db: repo.insert_turn(db, turn))
            return
        if name == "run_task":  # written together with the attempt it starts (one transaction)
            return
        self._persist_tool(tool)

    def _persist_tool(self, tool: Dict[str, Any]) -> None:
        turn, row = tool["turn"], self._tool_row(tool)
        tool["persisted"] = True

        def fn(db: Any) -> None:
            repo.insert_turn(db, turn)
            repo.insert_tool(db, row)

        self._submit("tool started", fn)

    def tool_finished(self, payload: Dict[str, Any]) -> None:
        tool, self._tool = self._tool, None
        if tool is None or not self.enabled or not tool["stored"] or tool["done"]:
            return
        self._finish_tool(tool, payload)

    def _finish_tool(self, tool: Dict[str, Any], payload: Dict[str, Any]) -> None:
        state, result = _tool_outcome(payload), _redact_tool_result(tool["name"], payload)
        stamp = self._stamp()
        tool_id = tool["id"]
        if tool["persisted"]:
            self._submit("tool finished", lambda db: repo.finish_tool(db, tool_id, state, result, stamp))
            return
        turn, row = tool["turn"], self._tool_row(tool)
        tool["persisted"] = True

        def fn(db: Any) -> None:
            repo.insert_turn(db, turn)
            repo.insert_tool(db, row)
            repo.finish_tool(db, tool_id, state, result, stamp)

        self._submit("tool finished", fn)

    def tool_rejected(self, name: Any, args: Any, payload: Dict[str, Any]) -> None:
        """A step the guards refused (invalid input or not allowed now): kept as a 'rejected' tool."""
        if not self.enabled:
            return
        if name not in repo.DB_TOOLS:
            self._flush_bind()
            return
        tool = self._new_tool(name, args)
        self._finish_tool(tool, payload)

    def answer_heard(self, question_id: str, status: str, transcript: Optional[str]) -> None:
        """One row per ask (retries kept). Needs the ask tool row, which was queued before this call."""
        tool = self._tool
        if not self.enabled or tool is None or not tool["persisted"] or tool["name"] != "ask":
            return
        key = limits.ANSWER_KEY.get(question_id)
        if key is None:
            return
        stamp, tool_id = self._stamp(), tool["id"]
        text = transcript if status == "heard" and transcript else None
        self._submit("answer", lambda db: repo.insert_answer(db, call_id=self.call_id, tool_call_id=tool_id,
                                                              question_id=key, status=status, transcript=text,
                                                              stamp=stamp))

    def answer_selected(self, session: Any, question_id: str, value: str) -> None:
        if not self.enabled:
            return
        self._submit("answer selected", lambda db: repo.select_answer(db, self.call_id, question_id, value))
        self._bump_context(session)

    # ---- attempts (transactions 3, 4, 5, 6) ---------------------------------------------------------------------------

    def attempt_created(self, attempt: Any, session: Any) -> None:
        row = self._defs.get(attempt.task)
        if not self.enabled or row is None:
            return
        tool = self._tool
        if tool is None or tool["name"] != "run_task" or tool["persisted"] or tool["done"]:
            tool = self._new_tool("run_task", {"task": attempt.task})
            self._tool = tool
        stored = stored_id(self.call_id, attempt.attempt_id)
        self._att[attempt.attempt_id] = {"stored": stored, "state": "preparing", "rev": 0, "task": attempt.task,
                                         "tool_id": tool["id"]}
        turn, tool_row = tool["turn"], self._tool_row(tool)
        tool["persisted"] = True
        stamp = self._stamp()

        def fn(db: Any) -> None:
            repo.insert_turn(db, turn)
            repo.insert_tool(db, tool_row)
            repo.insert_attempt(db, call_id=self.call_id, attempt_id=stored, wire_attempt_id=attempt.attempt_id,
                                task=attempt.task, tool_call_id=tool_row["tool_call_id"], ordinal=attempt.ordinal,
                                line_id=attempt.line_id, stamp=stamp, prepare_ms=int(limits.PREPARE_PLAYBACK_S * 1000))

        self._submit("attempt created", fn, attempt=stored, kills_attempt=True)

    @staticmethod
    def _bump(st: Dict[str, Any]) -> Callable[[], None]:
        """The attempt revision as the DATABASE has it: it moves only after a job that bumps it committed, so one
        failed write never desynchronizes the later compare-and-updates (they are read at execution time)."""
        def after() -> None:
            st["rev"] += 1
        return after

    def attempt_state(self, attempt: Any) -> None:
        st = self._att.get(attempt.attempt_id)
        if not self.enabled or st is None:
            return
        new = attempt.state
        if new not in ("capturing", "finalizing", "stopping") or _ORDER[new] <= _ORDER[st["state"]]:
            return
        stamp, stored, task = self._stamp(), st["stored"], st["task"]
        st["state"] = new
        window_ms = self._defs[task]["window_ms"]
        self._submit("attempt %s" % new, lambda db: repo.advance_attempt(
            db, stored, st["rev"], new, stamp, window_ms=window_ms,
            stall_ms=int(limits.ANALYSIS_STALLED_S * 1000), finalize_extra_ms=int(limits.FINALIZE_EXTRA_S * 1000)),
            attempt=stored, after=self._bump(st))

    def observation(self, attempt: Any, obs: Any) -> None:
        st = self._att.get(attempt.attempt_id)
        if not self.enabled or st is None:
            return
        self.attempt_state(attempt)
        stamp, stored = self._stamp(), st["stored"]
        event_id = stored_id(self.call_id, obs.event_id)
        body = obs.to_dict()
        self._submit("observation", lambda db: repo.record_observation(
            db, call_id=self.call_id, attempt_id=stored, event_id=event_id, obs=body, expected_revision=st["rev"],
            stamp=stamp, stall_ms=int(limits.ANALYSIS_STALLED_S * 1000)), attempt=stored, check_attempts=True,
            after=self._bump(st))

    def attempt_finished(self, attempt: Any, result: Dict[str, Any], session: Any,
                         values: Optional[Dict[str, float]] = None) -> None:
        st = self._att.get(attempt.attempt_id)
        if not self.enabled or st is None:
            return
        stamp, stored, tool_id = self._stamp(), st["stored"], st["tool_id"]
        st["state"] = "terminal"
        tool = self._tool
        if tool is not None and tool["id"] == tool_id:
            tool["done"] = True
        decision = {"outcome": result["outcome"], "stop_reason": attempt.stop_reason}

        def fn(db: Any) -> None:
            repo.terminalize_attempt(db, call_id=self.call_id, attempt_id=stored, result=result, values=values or None,
                                     expected_revision=st["rev"], tool_call_id=tool_id, stamp=stamp,
                                     decision=decision)

        def close_tool_only(db: Any) -> None:  # the attempt rows failed earlier: at least free the tool
            repo.finish_tool(db, tool_id, repo.TOOL_END[result["outcome"]], {"task_result": result}, stamp)

        self._submit("attempt finished", fn, attempt=stored, check_attempts=True, fallback=close_tool_only)
        self._bump_context(session)

    # ---- transaction 4: resource events -------------------------------------------------------------------------------

    def resource_event(self, ev: Any, session: Any) -> None:
        """An accepted resource.changed. The journal id is '<call>.client_<event id>', the same as Agent 2's
        writer uses, so whichever writes first wins and the other sees an identical event and does nothing."""
        if not self.enabled:
            return
        event_id, payload, stamp = stored_id(self.call_id, "client_" + ev.event_id), ev.to_dict(), self._stamp()
        self._submit("resource event", lambda db: repo.append_event(
            db, self.call_id, event_id, "resource.changed", payload, stamp, source="client",
            device_id=repo.parent_device(db, self.call_id)))
        self._bump_context(session)

    # ---- transaction 7: summary revisions ------------------------------------------------------------------------------

    def summary(self, phase: str, summary: Dict[str, Any], session: Any = None) -> None:
        if not self.enabled or not self.summaries:
            return
        stamp = self._stamp()
        self._submit("summary %s" % phase, lambda db: repo.append_summary(db, self.call_id, phase, summary, stamp))
        if session is not None and phase == "mid_call":
            self._bump_context(session)

    # ---- transaction 8: finish and learn ------------------------------------------------------------------------------

    def finish_call(self, session: Any, summary: Dict[str, Any]) -> None:
        """After the driver returned: the session row is finished, then (once Agent 2 has ended the call) a new
        baseline snapshot is created when the call is eligible."""
        if not self.enabled:
            return
        self._flush_bind()
        self._ctx_rev += 1
        revision, snapshot, stamp = self._ctx_rev, self._context(session), self._stamp()
        self._submit("finish session", lambda db: repo.finish_session(db, self.call_id, revision, snapshot, stamp))
        items = self._learnable(session, summary)
        if items:
            self._learn(items, 0)

    def _learnable(self, session: Any, summary: Dict[str, Any]) -> Dict[str, str]:
        """task -> stored attempt id of the reading that may enter her usual (the v9 rules: not acted, no confirmed
        change, no concern, no outside reading, the task completed with a usable reading), and only for a call whose
        final summary is as_usual or learning (the schema refuses any other member)."""
        if (self.label == "acted" or not session.finished
                or summary.get("status") not in repo.LEARNING_FINAL_STATUS):
            return {}
        update = session.baseline_update()
        if not update:
            return {}
        items: Dict[str, str] = {}
        for task in limits.TASKS:
            record = next((r for r in session.records_for(task) if not r.interrupted and r.usable
                           and r.reading is not None), None)
            if record is None or record.attempt_id not in self._att or record.attempt_id in self._dead:
                continue
            if task == "arms":
                ok = all(key in update for key in limits.ARMS_BASELINE_KEYS.values())
            elif task == "sentence":
                ok = limits.SPEECH_BASELINE_KEY in update
            else:
                ok = record.reading.get("metric_id") in update
            if ok:
                items[task] = self._att[record.attempt_id]["stored"]
        return items

    def _learn(self, items: Dict[str, str], tries: int) -> None:
        def fn(db: Any) -> None:
            try:
                repo.learn_baselines(db, call_id=self.call_id, person_id=self.person_id, label=self.label,
                                     needed=self.needed, items=items, stamp=self._stamp())
            except NotReady:
                self._retry_learn(items, tries + 1)

        self._submit("learn baselines", fn)

    def _retry_learn(self, items: Dict[str, str], tries: int) -> None:
        if tries > self.learn_retries:
            self.failures += 1
            log.warning("recording: the baseline of call %s was not updated (the call never ended in the database)",
                        self.call_id)
            return
        with _PENDING_LOCK:
            _PENDING["n"] += 1

        def again() -> None:
            try:
                self._learn(items, tries)
            finally:
                with _PENDING_LOCK:
                    _PENDING["n"] -= 1

        timer = threading.Timer(self.learn_retry_s, again)
        timer.daemon = True
        timer.start()
