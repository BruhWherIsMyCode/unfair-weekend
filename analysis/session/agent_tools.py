"""The call driver's tool handlers: ONE set of steps, shared by the Claude loop and the fixed-order fallback.

Every guard lives here, so no driver can skip it. The handlers call CallSession and the adapter; they never touch
readings, confirmation or the family message (those stay in CallSession code).

Tools (exact names): say, ask, record_answer, run_task, recover_resource, finish_call, alert_family.
A refused step returns {"error": "not allowed: <plain reason>", "allowed": [<tool names allowed now>]} and changes
nothing. After hang-up every tool returns {"error": "call ended"} and nothing reaches the client.

What run_task returns (v7, reversing v6's "plain facts only" on the owner's instruction): outcome, quality, reasons,
interruption, attempts left, whether another reading is needed, skip, resource states, plus "reading" (today's
number and her usual range, see facts()) and "baseline_ready". Never anything about whether the family was told.
alert_family(reason, note) asks the code to tell the family; the code decides nothing else from it.

Pure Python + asyncio. No network, no mediapipe.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from analysis.session import limits, rules
from analysis.tasks.stream_base import NOT_IMPLEMENTED

TOOL_NAMES = ("say", "ask", "record_answer", "run_task", "recover_resource", "finish_call", "alert_family")
VIDEO_TASKS = ("smile", "arms")
STOP_REASON = "she_asked_to_stop"
SCRIPT_PATH = Path(__file__).resolve().parents[2] / "contract" / "script.en.json"


def load_script(path: Optional[Path] = None) -> Dict[str, str]:
    """line id -> text, from the published contract/script.en.json."""
    return json.loads(Path(path or SCRIPT_PATH).read_text("utf-8"))


class ToolHandlers:
    def __init__(self, session: Any, adapter: Any, script: Optional[Dict[str, str]] = None) -> None:
        self.session = session
        self.adapter = adapter
        self.script = script if script is not None else load_script()
        self.greeted = False
        self.setup_played = False
        self.finished = False
        self.asks: Dict[str, int] = {q: 0 for q in limits.QUESTIONS}
        self.heard: Dict[str, bool] = {q: False for q in limits.QUESTIONS}
        self.transcripts: Dict[str, str] = {q: "" for q in limits.QUESTIONS}
        self.recorded: Dict[str, bool] = {q: False for q in limits.QUESTIONS}
        self.not_implemented: set = set()
        self.alerts = 0  # alert_family calls accepted in this call

    # ---- state views --------------------------------------------------------------------------------

    @property
    def ended(self) -> bool:
        return self.session.hung_up or self.session.finished or self.finished

    def _module(self, task: str) -> Any:
        return self.session.modules.get(task)

    def _unimplemented(self, task: str) -> bool:
        """No metric defined (a stub) or no module registered: the task can never give a reading."""
        module = self._module(task)
        if module is None or task in self.not_implemented:
            return True
        try:
            return module.definition().get("metric_id") is None
        except Exception:
            return False

    def _resource_state(self, task: str) -> str:
        return self.session.resources[limits.TASK_RESOURCE[task]].state

    def resource_states(self) -> Dict[str, str]:
        return {r: info.state for r, info in self.session.resources.items()}

    def pending_recoveries(self) -> List[str]:
        return [r for r in limits.RESOURCES if self.session.recovery_pending(r)]

    def needs_another(self, task: str) -> bool:
        """The rules say a further usable reading is needed (none yet, or a first outside reading is not yet
        settled by a second one) and the attempt budget allows it."""
        if self.ended or self._unimplemented(task):
            return False
        records = self.session.records_for(task)
        if rules.attempt_budget(records) is not None:
            return False
        usable = [r for r in records if r.usable and r.reading is not None]
        if not usable:
            return True
        if rules.evaluate_sign(records).confirmed:
            return False
        last = usable[-1]
        return len(usable) < 2 and bool(last.baseline_ready) and rules.reading_direction(last.reading) != 0

    def has_result(self, task: str) -> bool:
        """A usable reading exists and nothing more is needed."""
        records = self.session.records_for(task)
        return any(r.usable and r.reading is not None for r in records) and not self.needs_another(task)

    def skip(self, task: str) -> bool:
        """No budget left, the required resource is unavailable after its recovery, the task is not
        implemented, or the call time is up."""
        if self.ended or self._unimplemented(task):
            return True
        if rules.attempt_budget(self.session.records_for(task)) is not None:
            return True
        if self.session.time_left_s() <= 0:
            return True
        resource = limits.TASK_RESOURCE[task]
        return self._resource_state(task) != "available" and not self.session.recovery_pending(resource)

    def attempts_left(self, task: str) -> int:
        records = self.session.records_for(task)
        if rules.attempt_budget(records) is not None:
            return 0
        return max(0, limits.MAX_ATTEMPTS_PER_TASK - len(records))

    def task_open(self, task: str) -> bool:
        """A required task that still has budget, an available resource and no settled result."""
        return (not self._unimplemented(task) and self.needs_another(task)
                and self._resource_state(task) == "available" and self.session.time_left_s() > 0)

    def _task_block_reason(self, task: str) -> Optional[str]:
        if self._module(task) is None:
            return None  # the not-implemented path answers this one
        if task in self.not_implemented:
            return "this check is not available in this version"
        if self.has_result(task):
            return "this task already has its result"
        budget = rules.attempt_budget(self.session.records_for(task))
        if budget == "usable_readings_complete":
            return "this task already has its result"
        if budget is not None:
            return "no attempts are left for this task"
        resource = limits.TASK_RESOURCE[task]
        if self._resource_state(task) != "available":
            if self.session.recovery_pending(resource):
                return "the %s is not available; recover_resource(\"%s\") first" % (resource, resource)
            return "the %s is not available" % resource
        if self.session.active_attempt_id is not None:
            return "another task is running"
        if self.session.time_left_s() <= 0:
            return "the call time is up"
        return None

    def finish_refusal(self, reason: Optional[str]) -> Optional[str]:
        if reason == STOP_REASON:
            return None
        if self.session.time_left_s() < limits.FINISH_GUARD_S:
            return None
        open_tasks = [t for t in self.session.required_tasks if t in limits.TASKS and self.task_open(t)]
        if open_tasks:
            return "these checks are not finished yet: %s" % ", ".join(open_tasks)
        return None

    def allowed_now(self) -> List[str]:
        """The tool names a step could use right now (a name may still be refused for particular inputs)."""
        if self.ended:
            return []
        if not self.greeted:
            return ["say"]
        out: List[str] = []
        if not self.setup_played:
            out.append("say")
        if any(self.asks[q] < limits.MAX_ASKS_PER_QUESTION and not self.heard[q] for q in limits.QUESTIONS):
            out.append("ask")
        if any(self.asks[q] > 0 for q in limits.QUESTIONS):
            out.append("record_answer")
        if any(self._task_block_reason(t) is None for t in limits.TASKS if self._module(t) is not None):
            out.append("run_task")
        if self.pending_recoveries():
            out.append("recover_resource")
        if self.finish_refusal(None) is None:
            out.append("finish_call")
        if self.alerts < limits.ALERT_MAX_PER_CALL:
            out.append("alert_family")
        return out

    def plain_state(self) -> Dict[str, Any]:
        """The current state in plain facts (no readings), for a stale answer."""
        return {"call_ended": self.ended, "resources": self.resource_states(),
                "recoverable_resources": self.pending_recoveries(),
                "tasks": {t: {"attempts_left": self.attempts_left(t), "needs_another_reading": self.needs_another(t),
                              "skip": self.skip(t)} for t in limits.TASKS if self._module(t) is not None},
                "allowed": self.allowed_now()}

    def snapshot(self) -> Dict[str, Any]:
        """Everything the fixed-order driver's next_step needs, as plain data."""
        tasks = {}
        for t in limits.TASKS:
            tasks[t] = {"done": self.has_result(t), "skip": self.skip(t), "needs_another": self.needs_another(t),
                        "unimplemented": self._unimplemented(t), "resource": limits.TASK_RESOURCE[t],
                        "recovery_pending": self.session.recovery_pending(limits.TASK_RESOURCE[t])}
        return {"ended": self.ended, "greeted": self.greeted,
                "questions": {q: {"asks": self.asks[q], "heard": self.heard[q], "recorded": self.recorded[q],
                                  "transcript": self.transcripts[q]} for q in limits.QUESTIONS},
                "tasks": tasks}

    # ---- tool definitions for the Anthropic Messages API -------------------------------------------------

    def tool_definitions(self) -> List[Dict[str, Any]]:
        return [
            {"name": "say", "description": "Play a fixed line for her. Only the greeting (as the first step, once) "
             "and the phone set-up line (once) can be played this way.",
             "input_schema": {"type": "object", "properties": {"line_id": {"type": "string",
                                                                          "enum": list(limits.SAYABLE_LINES)}},
                              "required": ["line_id"], "additionalProperties": False}},
            {"name": "ask", "description": "Play one of the three questions and listen for her answer. At most two "
             "times per question. Returns whether an answer was heard and what she said.",
             "input_schema": {"type": "object", "properties": {"question_id": {"type": "string",
                                                                              "enum": list(limits.QUESTIONS)}},
                              "required": ["question_id"], "additionalProperties": False}},
            {"name": "record_answer", "description": "Record her answer to a question you asked. For q_medicine the "
             "value is exactly yes, no or unclear. For the others a short summary in her own words.",
             "input_schema": {"type": "object", "properties": {
                 "question_id": {"type": "string", "enum": list(limits.QUESTIONS)},
                 "value": {"type": "string", "maxLength": limits.ANSWER_MAX_CHARS}},
                 "required": ["question_id", "value"], "additionalProperties": False}},
            {"name": "run_task", "description": "Run one check: the code plays the instruction lines, measures, and "
             "returns plain facts about how the attempt went (never a result).",
             "input_schema": {"type": "object", "properties": {"task": {"type": "string", "enum": list(limits.TASKS)}},
                              "required": ["task"], "additionalProperties": False}},
            {"name": "recover_resource", "description": "After an interruption, ask her to fix the camera or "
             "microphone and wait a short time. Once per interruption. Returns restored or unavailable.",
             "input_schema": {"type": "object", "properties": {"resource": {"type": "string",
                                                                           "enum": list(limits.RESOURCES)}},
                              "required": ["resource"], "additionalProperties": False}},
            {"name": "finish_call", "description": "End the call. The code says goodbye. Refused while a required "
             "check still has attempts and no settled result. If she asked to stop, pass reason she_asked_to_stop.",
             "input_schema": {"type": "object", "properties": {"reason": {"type": "string", "enum": [STOP_REASON]}},
                              "additionalProperties": False}},
            {"name": "alert_family", "description": "Tell her family something worries you, now. At most %d times "
             "per call, after the greeting. Give a short plain note (at most 20 words) of what you noticed. The code "
             "sends it; you carry on with the call." % limits.ALERT_MAX_PER_CALL,
             "input_schema": {"type": "object", "properties": {
                 "reason": {"type": "string", "enum": list(limits.ALERT_REASONS)},
                 "note": {"type": "string", "maxLength": limits.ALERT_NOTE_MAX_CHARS}},
                 "required": ["reason", "note"], "additionalProperties": False}},
        ]

    # ---- validation and guards --------------------------------------------------------------------------

    def validate(self, name: Any, args: Any) -> Optional[str]:
        """A schema error as plain text, or None. Does not look at the call state."""
        if name not in TOOL_NAMES:
            return "unknown tool"
        if not isinstance(args, dict):
            return "input must be an object"
        allowed_keys = {"say": {"line_id"}, "ask": {"question_id"}, "record_answer": {"question_id", "value"},
                        "run_task": {"task"}, "recover_resource": {"resource"}, "finish_call": {"reason"},
                        "alert_family": {"reason", "note"}}[name]
        if set(args) - allowed_keys:
            return "unexpected fields"
        if name == "say" and args.get("line_id") not in limits.SAYABLE_LINES:
            return "line_id must be greet or setup_phone"
        if name in ("ask", "record_answer") and args.get("question_id") not in limits.QUESTIONS:
            return "question_id must be one of %s" % ", ".join(limits.QUESTIONS)
        if name == "record_answer":
            value = args.get("value")
            if not isinstance(value, str):
                return "value must be a string"
            if len(value) > limits.ANSWER_MAX_CHARS:
                return "value must be at most %d characters" % limits.ANSWER_MAX_CHARS
            if args["question_id"] == "q_medicine" and value not in ("yes", "no", "unclear"):
                return "for q_medicine the value must be yes, no or unclear"
        if name == "run_task" and args.get("task") not in limits.TASKS:
            return "task must be one of %s" % ", ".join(limits.TASKS)
        if name == "recover_resource" and args.get("resource") not in limits.RESOURCES:
            return "resource must be camera or microphone"
        if name == "finish_call" and "reason" in args and args["reason"] != STOP_REASON:
            return "reason, when given, must be %s" % STOP_REASON
        if name == "alert_family":
            if args.get("reason") not in limits.ALERT_REASONS:
                return "reason must be one of %s" % ", ".join(limits.ALERT_REASONS)
            if "note" in args and not isinstance(args["note"], str):
                return "note must be a string"
        return None

    def refusal(self, name: str, args: Dict[str, Any]) -> Optional[str]:
        """A plain reason the step is not allowed in the current state, or None."""
        if name == "say" and args["line_id"] == "greet":
            return "the greeting was already played" if self.greeted else None
        if not self.greeted:
            return "play the greeting first: say(\"greet\")"
        if name == "say":  # setup_phone
            return "the set-up line was already played" if self.setup_played else None
        if name == "ask":
            q = args["question_id"]
            if self.asks[q] >= limits.MAX_ASKS_PER_QUESTION:
                return "this question was already asked %d times" % limits.MAX_ASKS_PER_QUESTION
            if self.heard[q]:
                return "her answer to this question was already heard; record it"
            return None
        if name == "record_answer":
            return None if self.asks[args["question_id"]] > 0 else "ask this question first"
        if name == "run_task":
            return self._task_block_reason(args["task"])
        if name == "recover_resource":
            r = args["resource"]
            return None if self.session.recovery_pending(r) else "there is no interruption to recover from for the %s" % r
        if name == "finish_call":
            return self.finish_refusal(args.get("reason"))
        if name == "alert_family":
            if self.alerts >= limits.ALERT_MAX_PER_CALL:
                return "the family was already alerted %d times in this call" % limits.ALERT_MAX_PER_CALL
            return None
        return None

    # ---- execution ----------------------------------------------------------------------------------------

    async def execute(self, name: Any, args: Any) -> Dict[str, Any]:
        """Run one step through every guard. Never raises for a refused or invalid step."""
        if self.ended:
            return {"error": "call ended"}
        problem = self.validate(name, args)
        if problem is not None:
            return {"error": "invalid input: %s" % problem}
        reason = self.refusal(name, args)
        if reason is not None:
            return {"error": "not allowed: %s" % reason, "allowed": self.allowed_now()}
        try:
            return await getattr(self, "_do_" + name)(args)
        except Exception as exc:  # a failing step must not take the driver down
            self.session.note("tool_error", tool=name, error=type(exc).__name__)
            return {"error": "internal error: the step was not completed"}

    async def _play(self, line_id: str) -> bool:
        try:
            await self.adapter.say(line_id)
            return True
        except Exception as exc:
            self.session.note("say_failed", line=line_id, error=type(exc).__name__)
            return False

    async def _do_say(self, args: Dict[str, Any]) -> Dict[str, Any]:
        line = args["line_id"]
        played = await self._play(line)
        if line == "greet":
            self.greeted = True
        else:
            self.setup_played = True
        return {"said": line, "played": played}

    async def _do_ask(self, args: Dict[str, Any]) -> Dict[str, Any]:
        q = args["question_id"]
        self.asks[q] += 1
        text: Optional[str] = None
        if await self._play(q):
            if self.session.hung_up:
                return {"error": "call ended"}
            try:
                text = await self.adapter.listen(q, limits.LISTEN_TIMEOUT_S)
            except Exception as exc:
                self.session.note("listen_failed", question=q, error=type(exc).__name__)
        if self.session.hung_up:
            return {"error": "call ended"}
        heard = isinstance(text, str) and text.strip() != ""
        if heard:
            self.heard[q] = True
            self.transcripts[q] = text.strip()
        return {"question_id": q, "heard": heard,
                "transcript": self.transcripts[q][:limits.TRANSCRIPT_TO_CLAUDE_CHARS] if heard else ""}

    async def _do_record_answer(self, args: Dict[str, Any]) -> Dict[str, Any]:
        q = args["question_id"]
        self.session.record_answer(limits.ANSWER_KEY[q], args["value"].strip())
        self.recorded[q] = True
        return {"recorded": True, "question_id": q}

    async def _do_run_task(self, args: Dict[str, Any]) -> Dict[str, Any]:
        task = args["task"]
        if self._unimplemented(task):  # nothing to measure: no instruction is played for a check that cannot run
            self.not_implemented.add(task)
            reason = NOT_IMPLEMENTED if self._module(task) is not None else "task_unavailable"
            self.session.note("task_not_implemented", task=task, reason=reason)
            return {"task": task, "outcome": "failed", "quality": "unusable", "reasons": [reason],
                    "interruption": None, "attempts_left": 0, "needs_another_reading": False, "skip": True,
                    "resources": self.resource_states(), "reading": None, "baseline_ready": False}
        if task in VIDEO_TASKS and not self.setup_played:
            await self._play("setup_phone")
            self.setup_played = True
            if self.session.hung_up:
                return {"error": "call ended"}
        result = await self.session.run_task(task)
        return self.facts(task, result)

    @staticmethod
    def _reading_facts(reading: Optional[Dict[str, Any]], baseline_ready: bool) -> Optional[Dict[str, Any]]:
        """The numbers Claude sees (v7). distance = how far outside, as a multiple of half her usual range,
        0.0 when inside or while the usual is still being learned."""
        if reading is None:
            return None

        def r3(x: Any) -> Any:
            return round(float(x), 3) if isinstance(x, (int, float)) and not isinstance(x, bool) else None

        value, low, high = reading.get("value"), reading.get("usual_low"), reading.get("usual_high")
        distance = 0.0
        if baseline_ready and reading.get("outside") is True and None not in (value, low, high):
            half = (float(high) - float(low)) / 2.0
            outside_by = max(float(value) - float(high), float(low) - float(value), 0.0)
            if half > 0:
                distance = round(outside_by / half, 2)
        return {"value": r3(value), "usual_low": r3(low), "usual_high": r3(high),
                "outside_usual": reading.get("outside"), "distance": distance, "side": reading.get("side"),
                "unit": reading.get("unit")}

    def facts(self, task: str, result: Dict[str, Any]) -> Dict[str, Any]:
        """A TaskResult dict -> the facts Claude gets, now with today's numbers."""
        i = result.get("interruption")
        ready = bool((result.get("baseline") or {}).get("ready"))
        return {"task": task, "outcome": result["outcome"], "quality": result["quality"]["status"],
                "reasons": list(result["quality"]["reasons"]),
                "interruption": {"code": i["code"], "recoverable": i["recoverable"]} if i else None,
                "attempts_left": self.attempts_left(task), "needs_another_reading": self.needs_another(task),
                "skip": self.skip(task), "resources": self.resource_states(),
                "reading": self._reading_facts(result.get("reading"), ready), "baseline_ready": ready}

    async def _do_alert_family(self, args: Dict[str, Any]) -> Dict[str, Any]:
        note = args.get("note", "")
        if len(note) > limits.ALERT_NOTE_MAX_CHARS:
            self.session.note("alert_note_cut", length=len(note))
            note = note[:limits.ALERT_NOTE_MAX_CHARS]
        self.alerts += 1
        await self.session.raise_concern(args["reason"], note)
        return {"sent": True}

    async def _do_recover_resource(self, args: Dict[str, Any]) -> Dict[str, Any]:
        resource = args["resource"]
        result = await self.session.recover_resource(resource)
        return {"resource": resource, "result": result}

    async def _do_finish_call(self, args: Dict[str, Any]) -> Dict[str, Any]:
        if args.get("reason"):
            self.session.note("finish_reason", reason=args["reason"])
        return await self.force_finish()

    async def force_finish(self) -> Dict[str, Any]:
        """Say goodbye (inside session.finish), publish the final summary, mark the call finished.

        Used by finish_call after its guard, and by the fixed-order driver as the last step of a call."""
        await self.session.finish()
        self.finished = True
        return {"finished": True}
