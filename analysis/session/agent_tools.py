"""The call driver's tool handlers: ONE set of steps, shared by the Claude loop and the fixed-order fallback.

Every guard lives here, so no driver can skip it. The handlers call CallSession and the adapter; they never touch
readings, confirmation or the family message (those stay in CallSession code).

Tools (exact names): say, ask, record_answer, run_task, recover_resource, finish_call, alert_family, and (v11,
her own words for the agent) say_own, ask_followup. The two need the adapter capability `say_text`; without it, or
for a text that breaks a guard (empty, over 25 words / 200 characters, a digit or %, a banned word), or while an
attempt runs, after the call ended, past OWN_LINES_MAX, past one follow-up per question, for a question that is
already recorded, or in the last FINISH_GUARD_S seconds, they are refused and nothing is played. The fixed-order
driver never uses them. say_own also refuses common exercise directions: run_task owns those instructions.
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
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from analysis.session import limits, rules
from analysis.tasks.stream_base import NOT_IMPLEMENTED

TOOL_NAMES = ("say", "ask", "record_answer", "run_task", "recover_resource", "finish_call", "alert_family",
              "say_own", "ask_followup")
OWN_TOOLS = ("say_own", "ask_followup")
VIDEO_TASKS = ("smile", "arms")
STOP_REASON = "she_asked_to_stop"
SCRIPT_PATH = Path(__file__).resolve().parents[2] / "contract" / "script.en.json"


def _banned_pattern() -> "re.Pattern[str]":
    forms = set()
    for word in limits.OWN_LINE_BANNED_WORDS:
        stem = word[:-1] if word.endswith("e") else word
        forms.update({word, word + "s", word + "es", word + "d", word + "ed", word + "ion", word + "ions",
                      stem + "ion", stem + "ions", stem + "ing"})
    forms.update({"emergencies", "diagnosis", "safety", "safely"})
    alternatives = "|".join(sorted((re.escape(f) for f in forms), key=len, reverse=True))
    return re.compile(r"(?<![\w-])(?:%s)(?![\w-])" % alternatives, re.IGNORECASE)


_BANNED = _banned_pattern()
# Common English exercise directions belong to run_task. Match imperative clauses,
# not acknowledgments such as "You said you cannot raise your arm"; this is not a
# general language classifier, so the prompt also keeps retry remarks reason-only.
_EXERCISE_DIRECTION = re.compile(
    r"(?:^|[.!;:,]\s*|\b(?:and|then)\s+)(?:(?:please|now|just)\s+)*"
    r"(?:(?:you\s+can|let['’]s)\s+)?(?:"
    r"(?:raise|lift|show|hold|keep|extend)\b[^.!?;:]{0,45}\b(?:hands?|arms?|smile|phone)\b"
    r"|(?:hold|keep)\b[^.!?;:]{0,25}\bface\s+relaxed\b"
    r"|(?:raise|lift|hold|keep)\s+it\b[^.!?;:]{0,25}\b(?:up|higher|beside)\b"
    r"|(?:swap|switch|change)\s+(?:your\s+)?hands?\b"
    r"|give\b[^.!?;:]{0,25}\bsmile\b|smile\b)"
    r"|\b(?:try(?:\s+again)?|again|once\s+more)[,\s]+with\s+your\s+"
    r"(?:(?:left|right)\s+)?(?:hands?|arms?)\s+up\b",
    re.IGNORECASE,
)


def own_text_problem(text: Any) -> Optional[str]:
    """Why a text may not be spoken as the agent's own words (a plain reason), or None when it is fine."""
    if not isinstance(text, str) or not text.strip():
        return "the text is empty"
    if len(text) > limits.OWN_LINE_MAX_CHARS:
        return "the text is longer than %d characters" % limits.OWN_LINE_MAX_CHARS
    if len(text.split()) > limits.OWN_LINE_MAX_WORDS:
        return "the text is longer than %d words" % limits.OWN_LINE_MAX_WORDS
    if re.search(r"[\d%]", text):
        return "the text must not contain digits or a percent sign"
    found = _BANNED.search(text)
    if found:
        return "the word %r must not be used" % found.group(0).lower()
    return None


# retry_hint slug -> plain phrase Claude can rephrase
RETRY_HINT_TEXT = {
    "face_not_in_view": "her face was not in view",
    "already_smiling": "she was already smiling before the cue",
    "no_smile_seen": "no smile was seen",
    "hand_not_seen": "her hand was not seen",
    "hand_too_late": "her hand came up too late",
    "camera_lost": "the picture was lost",
    "too_dark": "it was too dark to see her",
    "other": "it did not work out that time",
}
_LOST_CODES = ("camera_unavailable", "microphone_unavailable", "no_samples", "capture_failed", "analysis_stalled",
               "camera_lost", "image_could_not_be_decoded", "cancelled", "timeout", "call_ended")


def retry_hint_for(task: str, result: Dict[str, Any]) -> str:
    """One slug of limits.RETRY_HINTS for a not-measured attempt, from its quality reasons and interruption."""
    quality = result.get("quality") or {}
    reasons = [str(r) for r in (quality.get("reasons") or [])]
    interruption = result.get("interruption") or {}
    if interruption.get("code"):
        reasons.append(str(interruption["code"]))
    default_slug = "hand_not_seen" if task == "arms" else "face_not_in_view"
    for r in reasons:
        if r in limits.RETRY_HINTS and r != "other":
            return r
    for r in reasons:
        if "dark" in r or "light" in r:
            return "too_dark"
        if "too_late" in r:
            return "hand_too_late"
        if "hand" in r and "not_seen" in r or "hand_not_found" in r or "no_hand" in r:
            return "hand_not_seen"
        if r in ("framing_unusable", "too_few_usable_frames", "task_unperformable"):
            return default_slug if r != "task_unperformable" or task == "arms" else "no_smile_seen"
        if r.startswith("face_") or r.startswith("head_") or r == "eyes_at_the_same_spot":
            return "face_not_in_view"
        if r in _LOST_CODES:
            return "camera_lost"
    return "other"


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
        self.own_lines = 0  # say_own + ask_followup accepted in this call
        self.followups: Dict[str, int] = {q: 0 for q in limits.QUESTIONS}
        self.followup_heard: Dict[str, bool] = {q: False for q in limits.QUESTIONS}
        self.followup_transcripts: Dict[str, str] = {q: "" for q in limits.QUESTIONS}
        self.finishing = False  # finish_call is running

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
        if limits.CONFIRM_FROM_ONE_READING:
            return False  # v12b: one usable reading settles the task (an outside one has already told the family)
        evals = rules.evaluate_all({t: self.session.records_for(t) for t in limits.TASKS})
        if evals[task].confirmed:
            return False
        last = usable[-1]
        if not (len(usable) < 2 and bool(last.baseline_ready) and rules.reading_direction(last.reading) != 0):
            return False
        # v12: a first outside face or arm reading waits for the other task, which may confirm it with no repeat.
        partner = rules.cross_partner(task)
        return partner is None or not self._awaits_first_reading(partner)

    def _awaits_first_reading(self, task: str) -> bool:
        """A required task that has no usable reading yet and can still get one in this call."""
        if task not in self.session.required_tasks or self._unimplemented(task) or self.skip(task):
            return False
        return not any(r.usable and r.reading is not None for r in self.session.records_for(task))

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
        return rules.attempts_left(self.session.records_for(task))

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
        if self._own_refusal() is None:
            out.append("say_own")
            if any(self._followup_refusal(q) is None for q in limits.QUESTIONS):
                out.append("ask_followup")
        return out

    # ---- own words (v11) -------------------------------------------------------------------------------

    def _own_refusal(self) -> Optional[str]:
        """Why NO own line is possible right now (independent of the text), or None."""
        if getattr(self.adapter, "say_text", None) is None:
            return "your own words are not available on this connection"
        if self.ended or self.finishing:
            return "the call is ending"
        if self.session.active_attempt_id is not None:
            return "a check is running"
        if self.own_lines >= limits.OWN_LINES_MAX:
            return "you already used %d own lines in this call" % limits.OWN_LINES_MAX
        if self.session.time_left_s() < limits.FINISH_GUARD_S:
            return "the call is almost over"
        return None

    def _followup_refusal(self, q: str) -> Optional[str]:
        if self.asks[q] == 0:
            return "ask this question first"
        if self.recorded[q]:
            return "her answer to this question is already recorded"
        if self.followups[q] >= limits.FOLLOWUPS_PER_QUESTION:
            return "this question already had its follow-up"
        return None

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
                                  "transcript": self.transcripts[q], "followups": self.followups[q],
                                  "followup_transcript": self.followup_transcripts[q]}
                              for q in limits.QUESTIONS},
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
             "returns facts about how the attempt went, with today's number and her usual range. For the sentence "
             "check it also returns `speech`: what was heard, word_error_rate, missing_words, changed_words, "
             "extra_words, longest_pause_s and words_per_min (null when nothing usable was heard).",
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
            {"name": "alert_family", "description": "Record something that worries you. The family is told when the "
             "call finishes, not now. At most %d times per call, after the greeting. Give a short plain note (at most "
             "20 words) of what you noticed. The code keeps it; you carry on with the call." % limits.ALERT_MAX_PER_CALL
             if limits.CONCERN_ALERT_AT_END else
             "Tell her family something worries you, now. At most %d times "
             "per call, after the greeting. Give a short plain note (at most 20 words) of what you noticed. The code "
             "sends it; you carry on with the call." % limits.ALERT_MAX_PER_CALL,
             "input_schema": {"type": "object", "properties": {
                 "reason": {"type": "string", "enum": list(limits.ALERT_REASONS)},
                 "note": {"type": "string", "maxLength": limits.ALERT_NOTE_MAX_CHARS}},
                 "required": ["reason", "note"], "additionalProperties": False}},
            {"name": "say_own", "description": "Say one or two short sentences of your own to her: react to what she "
             "just said or to what just happened, or tell her in a few words why a check is repeated (use the "
             "result's retry_hint, e.g. 'I couldn't see your hand that time.'). Never give exercise or phone set-up "
             "directions: run_task plays those instructions. Never a question. At most %d words, no digits, "
             "none of these words: %s. At most %d own lines in the "
             "call (say_own and ask_followup together); not while a check runs."
             % (limits.OWN_LINE_MAX_WORDS, ", ".join(limits.OWN_LINE_BANNED_WORDS), limits.OWN_LINES_MAX),
             "input_schema": {"type": "object", "properties": {"text": {"type": "string",
                                                                       "maxLength": limits.OWN_LINE_MAX_CHARS}},
                              "required": ["text"], "additionalProperties": False}},
            {"name": "ask_followup", "description": "Ask her ONE short clarifying question about her answer to a "
             "question you asked, and listen. At most one follow-up per question, only before you record that "
             "answer. Same length, word, digit and banned-word limits as say_own. Returns heard (true/false) and "
             "transcript (what she said, "
             "or null).",
             "input_schema": {"type": "object", "properties": {
                 "question_id": {"type": "string", "enum": list(limits.QUESTIONS)},
                 "text": {"type": "string", "maxLength": limits.OWN_LINE_MAX_CHARS}},
                 "required": ["question_id", "text"], "additionalProperties": False}},
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
                        "alert_family": {"reason", "note"}, "say_own": {"text"},
                        "ask_followup": {"question_id", "text"}}[name]
        if set(args) - allowed_keys:
            return "unexpected fields"
        if name in OWN_TOOLS:
            if not isinstance(args.get("text"), str):
                return "text must be a string"
            if name == "ask_followup" and args.get("question_id") not in limits.QUESTIONS:
                return "question_id must be one of %s" % ", ".join(limits.QUESTIONS)
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
            if self.setup_played:
                return "the set-up line was already played"
            if not all(self.recorded.values()):
                # v12b: played before the questions it cost the call ~10 s; run_task plays it before the first check
                return "the set-up line comes right before the first check: run_task() plays it, ask the questions first"
            return None
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
        if name in OWN_TOOLS:
            reason = self._own_refusal()
            if reason is None and name == "ask_followup":
                reason = self._followup_refusal(args["question_id"])
            if reason is None and name == "say_own" and "?" in str(args["text"]):
                # v12b: its reactions asked the next question in its own words, then ask() asked it again
                reason = "say_own never asks a question: the questions are ask() and ask_followup()"
            if reason is None and name == "say_own" and _EXERCISE_DIRECTION.search(args["text"].strip()):
                reason = "run_task plays the exercise instructions; say_own may explain only why a retry is needed"
            return reason or own_text_problem(args["text"])
        return None

    # ---- execution ----------------------------------------------------------------------------------------

    async def execute(self, name: Any, args: Any) -> Dict[str, Any]:
        """Run one step through every guard. Never raises for a refused or invalid step."""
        if self.ended:
            return {"error": "call ended"}
        problem = self.validate(name, args)
        if problem is not None:
            out = {"error": "invalid input: %s" % problem}
            self._rec("tool_rejected", name, args, out)
            return out
        reason = self.refusal(name, args)
        if reason is not None:
            out = {"error": "not allowed: %s" % reason, "allowed": self.allowed_now()}
            self._rec("tool_rejected", name, args, out)
            return out
        self._rec("tool_started", name, args)
        try:
            out = await getattr(self, "_do_" + name)(args)
        except Exception as exc:  # a failing step must not take the driver down
            self.session.note("tool_error", tool=name, error=type(exc).__name__)
            out = {"error": "internal error: the step was not completed"}
        except BaseException:  # cancelled with the call: the tool row must not stay running
            self._rec("tool_finished", {"error": "call ended"})
            raise
        self._rec("tool_finished", out)
        return out

    def _rec(self, hook: str, *args: Any) -> None:
        """One recorder hook (the session's optional database recorder). Never raises."""
        recorder = getattr(self.session, "recorder", None)
        if recorder is None:
            return
        try:
            getattr(recorder, hook)(*args)
        except Exception:
            pass

    async def _play(self, line_id: str) -> bool:
        self.session.note("line_said", line=line_id)
        try:
            await self.adapter.say(line_id)
            return True
        except Exception as exc:
            self.session.note("say_failed", line=line_id, error=type(exc).__name__)
            return False

    async def _play_own(self, text: str, tool: str, question: Optional[str] = None) -> bool:
        """Play the agent's own words through the adapter's optional say_text; counted as one own line."""
        self.own_lines += 1
        fields: Dict[str, Any] = {"tool": tool, "text": text}
        if question is not None:
            fields["question"] = question
        self.session.note("own_line", **fields)
        try:
            await self.adapter.say_text(text)
            return True
        except Exception as exc:
            self.session.note("say_failed", line="agent_remark", error=type(exc).__name__)
            return False

    async def _do_say_own(self, args: Dict[str, Any]) -> Dict[str, Any]:
        played = await self._play_own(args["text"].strip(), "say_own")
        if self.session.hung_up:
            return {"error": "call ended"}
        return {"said": "own", "played": played}

    async def _do_ask_followup(self, args: Dict[str, Any]) -> Dict[str, Any]:
        q = args["question_id"]
        self.followups[q] += 1
        text: Optional[str] = None
        if await self._play_own(args["text"].strip(), "ask_followup", q):
            if self.session.hung_up:
                return {"error": "call ended"}
            try:
                text = await self.adapter.listen(q, limits.LISTEN_TIMEOUT_S)
            except Exception as exc:
                self.session.note("listen_failed", question=q, error=type(exc).__name__)
        if self.session.hung_up:
            return {"error": "call ended"}
        heard = isinstance(text, str) and text.strip() != ""
        self.followup_heard[q] = heard
        if heard:
            self.followup_transcripts[q] = text.strip()
        self.session.note("answer_heard", question=q, heard=heard, followup=True)
        return {"heard": heard,
                "transcript": self.followup_transcripts[q][:limits.TRANSCRIPT_TO_CLAUDE_CHARS] if heard else None}

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
        available = False  # the question was played and listening worked (else the answer is "unavailable")
        if await self._play(q):
            if self.session.hung_up:
                self._rec("answer_heard", q, "interrupted", None)
                return {"error": "call ended"}
            try:
                text = await self.adapter.listen(q, limits.LISTEN_TIMEOUT_S)
                available = True
            except Exception as exc:
                self.session.note("listen_failed", question=q, error=type(exc).__name__)
        if self.session.hung_up:
            self._rec("answer_heard", q, "interrupted", None)
            return {"error": "call ended"}
        heard = isinstance(text, str) and text.strip() != ""
        self.session.note("answer_heard", question=q, heard=heard)
        if heard:
            self.heard[q] = True
            self.transcripts[q] = text.strip()
        self._rec("answer_heard", q, "heard" if heard else ("no_speech" if available else "unavailable"),
                  self.transcripts[q] if heard else None)
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
                    "resources": self.resource_states(), "reading": None, "baseline_ready": False,
                    "retry_hint": None, "retry_hint_text": None}
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
        out = {"task": task, "outcome": result["outcome"], "quality": result["quality"]["status"],
               "reasons": list(result["quality"]["reasons"]),
               "interruption": {"code": i["code"], "recoverable": i["recoverable"]} if i else None,
               "attempts_left": self.attempts_left(task), "needs_another_reading": self.needs_another(task),
               "skip": self.skip(task), "resources": self.resource_states(),
               "reading": self._reading_facts(result.get("reading"), ready), "baseline_ready": ready}
        # retry_hint: only for a repeat of a NOT-measured attempt (a confirmation repeat after a usable reading has none)
        measured = (result["outcome"] == "completed" and result["quality"]["status"] == "usable"
                    and result.get("reading") is not None)
        hint = retry_hint_for(task, result) if (out["needs_another_reading"] and not measured) else None
        out["retry_hint"] = hint
        out["retry_hint_text"] = RETRY_HINT_TEXT[hint] if hint else None
        if task == "sentence":  # v8: the speech numbers (Claude cannot hear; the code turned the word timings into numbers)
            details = getattr(self._module("sentence"), "attempt_details", None)
            out["speech"] = details(result.get("attempt_id")) if callable(details) else None
        return out

    async def _do_alert_family(self, args: Dict[str, Any]) -> Dict[str, Any]:
        note = args.get("note", "")
        if len(note) > limits.ALERT_NOTE_MAX_CHARS:
            self.session.note("alert_note_cut", length=len(note))
            note = note[:limits.ALERT_NOTE_MAX_CHARS]
        self.alerts += 1
        await self.session.raise_concern(args["reason"], note)
        if limits.CONCERN_ALERT_AT_END:
            return {"recorded": True, "family_told": "when_the_call_finishes"}
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
        self.finishing = True
        await self.session.finish()
        self.finished = True
        return {"finished": True}
