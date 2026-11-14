"""ClaudeAgent: one bounded plan per model request; the code guards each action in that plan.

One advance_call per turn groups answer recording, a concern and a reaction with the next question/check.
That final action's result returns to the model before another plan is chosen. Legacy individual tool calls
remain executable. Every assistant tool_use gets exactly one matching user tool_result.

Stale decisions. CallSession.event_version counts resource changes, interruptions and hang-up. It is recorded
before each request; when it moved by the time the answer arrives, the answer was decided on an old state: its
tool_use is answered with {"stale": true, "state": ...} and NOT executed, then a new request is made. Hang-up
stops the loop at once with no further request.

Fixed-order fallback, for the REST of the call (never switched back): the client could not be built; a request
failed or timed out; a response without any tool_use (or malformed); a tool call failing its schema twice in a
row (unknown tool counts); three refused steps in a row; more than CLAUDE_MAX_TURNS requests. With
allow_fallback=False these failures log agent_failed and raise ClaudeFailed for the caller to finalize.

The agent never reads or changes readings, confirmation or the family message: those stay in CallSession code, so
a confirmed change is published even if Claude fails right after. Every decision goes to session.log: kinds
agent_request, agent_tool, agent_refused, agent_stale, agent_fallback. The API key and the system prompt are never
logged; a free-text answer is never logged.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from analysis.session import limits, turn_plan
from analysis.session.fallback import FixedOrderDriver

PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "agent_system.md"
FIRST_MESSAGE = "Call connected. Nothing done yet."
# The lines Claude can cause (directly or through a tool); the code plays all of them.
CAUSABLE_LINES = ("greet", "q_medicine", "q_feeling", "q_unusual", "setup_phone", "task_smile", "task_arms",
                  "task_sentence", "repeat_smile", "repeat_arms", "repeat_sentence", "smile_now", "swap_hands",
                  "camera_unavailable", "microphone_unavailable", "bye")


class _RequestTimeout(Exception):
    pass


class ClaudeFailed(RuntimeError):
    """Claude could not continue and the operator disabled the fixed-script fallback."""

    code = "claude_failed"

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__("Claude stopped: %s; fixed-script fallback is disabled" % reason)


PRONOUN_WORDS = {"she": ("she", "her", "her"), "he": ("he", "his", "him")}  # {she} {her} {her_obj}


def build_system_prompt(name: str, script: Dict[str, str], prompt_path: Optional[Path] = None,
                        checks: Sequence[str] = ("smile", "arms", "sentence"), pronoun: str = "she",
                        age: Optional[int] = None, about: Sequence[str] = ()) -> str:
    """agent_system.md with its placeholders filled by str.replace (the prompt has JSON-like braces, never
    str.format): {lines}, {checks} (the checks of THIS call, in order: on the phone the sentence is not one, and
    announcing it cost the call a line and a step), {about} (one "- " line per fact), {she} {her} {her_obj}
    (she/her/her or he/his/him), then {name}."""
    template = Path(prompt_path or PROMPT_PATH).read_text("utf-8")
    lines = "\n".join('- %s: "%s"' % (line_id, script[line_id]) for line_id in CAUSABLE_LINES if line_id in script)
    order = ", then ".join(checks) if checks else "none"
    facts = []
    if age:
        facts.append("- Age: %d." % age)
    facts.extend("- %s" % str(a).strip() for a in (about or ()) if str(a).strip())
    about_text = "\n".join(facts) if facts else "- Nothing else is known."
    she, her, her_obj = PRONOUN_WORDS.get(pronoun, PRONOUN_WORDS["she"])
    return (template.replace("{lines}", lines).replace("{checks}", order).replace("{about}", about_text)
            .replace("{her_obj}", her_obj).replace("{her}", her).replace("{she}", she).replace("{name}", name))


def _loggable(name: Any, args: Any) -> Dict[str, Any]:
    """The tool input without anything she said: a free-text answer is omitted."""
    if not isinstance(args, dict):
        return {}
    out: Dict[str, Any] = {}
    if name == turn_plan.TOOL_NAME:
        for field, tool in turn_plan.PREFIX_TOOLS.items():
            if field in args:
                out[field] = _loggable(tool, args[field])
        if "greet" in args:
            out["greet"] = args["greet"] is True
        if "reaction" in args:
            out["reaction"] = _loggable("say_own", {"text": args["reaction"]}).get("text")
        next_ = args.get("next")
        if isinstance(next_, dict) and next_.get("tool") in turn_plan.NEXT_TOOLS:
            out["next"] = {"tool": next_["tool"], "input": _loggable(next_["tool"], next_.get("input"))}
        return out
    for key, value in args.items():
        if name == "alert_family" and key == "note":
            out[key] = "<omitted>"  # the note may repeat her words
        elif name in ("say_own", "ask_followup") and key == "text":
            out[key] = value[:200] if isinstance(value, str) else "<omitted>"  # the agent's own words, not hers
        elif name == "record_answer" and key == "value":
            medicine = args.get("question_id") == "q_medicine" and value in ("yes", "no", "unclear")
            out[key] = value if medicine else "<omitted>"
        elif isinstance(value, (str, int, float, bool)) or value is None:
            out[key] = value[:40] if isinstance(value, str) else value
        else:
            out[key] = "<omitted>"
    return out


class ClaudeAgent:
    def __init__(self, session: Any, handlers: Any, client: Any, clock: Any = None,
                 prompt_path: Optional[Path] = None, unavailable_reason: Optional[str] = None,
                 max_turns: int = limits.CLAUDE_MAX_TURNS, timeout_s: float = limits.CLAUDE_TIMEOUT_S,
                 recorder: Any = None, allow_fallback: bool = True) -> None:
        self.session = session
        # Optional database recorder: defaults to the session's. None = no recording. Hooks never raise.
        self.recorder = recorder if recorder is not None else getattr(session, "recorder", None)
        self.handlers = handlers
        self.client = client  # None when it could not be built
        self.clock = clock or session.clock
        self.prompt_path = prompt_path
        self.unavailable_reason = unavailable_reason
        self.max_turns = max_turns
        self.timeout_s = timeout_s
        self.turns = 0
        self.using_fallback = False
        self.allow_fallback = allow_fallback

    # ---- the loop ---------------------------------------------------------------------------------------

    async def run(self) -> None:
        """Drive the call to its end. With fallback disabled, model failure raises ClaudeFailed."""
        if self.client is None:
            await self._fallback(self.unavailable_reason or "no_client")
            return
        checks = [t for t in limits.TASKS if t in self.session.required_tasks]
        system = build_system_prompt(self.session.person_name, self.handlers.script, self.prompt_path, checks,
                                     getattr(self.session, "person_pronoun", "she"),
                                     getattr(self.session, "person_age", None),
                                     getattr(self.session, "person_about", ()))
        tools = [turn_plan.tool_definition(self.handlers.tool_definitions())]
        messages: List[Dict[str, Any]] = [{"role": "user", "content": FIRST_MESSAGE}]
        schema_streak = 0
        refused_streak = 0
        while True:
            if self.handlers.ended:
                return
            if self.turns >= self.max_turns:
                await self._fallback("turn_cap")
                return
            self.turns += 1
            version = self.session.event_version
            started = self.clock.now()
            self._rec("turn_started")
            try:
                response = await self._request(system, messages, tools)
            except _RequestTimeout:
                self._record_turn("failed", messages, error_code="request_timeout")
                await self._fallback("request_timeout")
                return
            except Exception as exc:
                self._record_turn("failed", messages, error_code="request_error_%s" % type(exc).__name__)
                await self._fallback("request_error_%s" % type(exc).__name__)
                return
            self.session.note("agent_request", turn=self.turns, latency_s=round(self.clock.now() - started, 3))
            if self.handlers.ended:  # hang-up during the request: stop, no further request
                self._record_turn("cancelled", messages, error_code="call_ended")
                return
            parsed = self._parse(response)
            if parsed is None:
                self._record_turn("failed", messages, error_code="malformed_response")
                await self._fallback("malformed_response")
                return
            content, uses = parsed
            stop_reason = response.get("stop_reason") if isinstance(response, dict) else None
            if not uses:
                self._record_turn("completed", messages, content, stop_reason)
                await self._fallback("no_tool_use")
                return
            stale = self.session.event_version != version
            first = uses[0]
            name, args = first["name"], first["raw_input"]
            steps, plan_problem = None, None
            if name == turn_plan.TOOL_NAME and not stale:
                steps, plan_problem = turn_plan.actions(args, self.handlers)
            self._record_turn("stale" if stale else "completed", messages, content, stop_reason,
                              first=None if stale else first,
                              steps=steps if plan_problem is None else None)
            messages.append({"role": "assistant", "content": content})

            fallback_reason: Optional[str] = None
            if stale:
                self.session.note("agent_stale", turn=self.turns, tool=name)
                payload: Dict[str, Any] = {"stale": True, "state": self.handlers.plain_state()}
            else:
                problem = plan_problem if name == turn_plan.TOOL_NAME else self.handlers.validate(name, args)
                if problem is not None:
                    schema_streak += 1
                    self.session.note("agent_refused", turn=self.turns, tool=name, reason="invalid input: %s" % problem)
                    payload = {"error": "invalid input: %s" % problem}
                    self._rec("tool_rejected", name, args, payload)
                    if schema_streak >= 2:
                        fallback_reason = "bad_tool_input_twice"
                else:
                    schema_streak = 0
                    self.session.note("agent_tool", turn=self.turns, name=name, input=_loggable(name, args))
                    if name == turn_plan.TOOL_NAME:
                        payload = await self._execute_plan(steps or [])
                    else:
                        payload = await self.handlers.execute(name, args)
                    if self.session.hung_up or payload.get("error") == "call ended":
                        return
                    error = payload.get("error")
                    if error is not None:
                        refused_streak += 1
                        self.session.note("agent_refused", turn=self.turns, tool=name, reason=str(error)[:120])
                        if refused_streak >= 3:
                            fallback_reason = "refused_3x"
                    else:
                        refused_streak = 0
            results = [self._tool_result(first["id"], payload)]
            for other in uses[1:]:
                results.append(self._tool_result(other["id"], {"error": "one action at a time, not executed"}))
            messages.append({"role": "user", "content": results})
            if fallback_reason is not None:
                await self._fallback(fallback_reason)
                return
            if self.handlers.ended:
                return

    # ---- plan execution --------------------------------------------------------------------------------

    async def _execute_plan(self, steps: List[turn_plan.Action]) -> Dict[str, Any]:
        """Run in order, stopping at failure, hang-up or a changed resource state. Never run past new input."""
        version = self.session.event_version
        completed: List[str] = []
        payload: Dict[str, Any] = {}
        try:
            for name, args in steps:
                if self.handlers.ended:
                    return {"error": "call ended", "completed_steps": completed}
                if self.session.event_version != version:
                    self.session.note("agent_plan_stopped", reason="state_changed", completed_steps=completed)
                    return {"stale": True, "state": self.handlers.plain_state(), "completed_steps": completed}
                self.session.note("agent_plan_step", turn=self.turns, name=name, input=_loggable(name, args))
                payload = await self.handlers.execute(name, args)
                if "error" in payload or payload.get("played") is False:
                    return {**payload, "error": payload.get("error", "playback failed"),
                            "failed_step": name, "completed_steps": completed}
                completed.append(name)
            # Keep the final result (including the new transcript or task facts) at the top level.
            return {**payload, "completed_steps": completed}
        finally:
            self._rec("plan_finished")

    # ---- recording (optional; never raises) -------------------------------------------------------------

    def _rec(self, hook: str, *args: Any, **kwargs: Any) -> None:
        if self.recorder is None:
            return
        try:
            getattr(self.recorder, hook)(*args, **kwargs)
        except Exception:
            pass

    def _record_turn(self, state: str, messages: List[Dict[str, Any]], content: Optional[List[Dict[str, Any]]] = None,
                     stop_reason: Optional[str] = None, error_code: Optional[str] = None,
                     first: Optional[Dict[str, Any]] = None,
                     steps: Optional[List[turn_plan.Action]] = None) -> None:
        """One finished model request for the database: the output with free text left out (`_loggable`)."""
        if self.recorder is None:
            return
        blocks: Optional[List[Dict[str, Any]]] = None
        if content is not None:
            blocks = []
            for block in content:
                if block.get("type") == "tool_use":
                    blocks.append({"type": "tool_use", "id": block["id"], "name": block["name"],
                                   "input": _loggable(block["name"], block.get("input"))})
                elif block.get("type") == "text":
                    blocks.append({"type": "text", "text": str(block.get("text", ""))[:300]})
        model_id = getattr(self.client, "model", None) or ("fake-claude" if self.client is not None else "none")
        binding: Dict[str, Any] = {
            "tool_use": {"id": first["id"], "name": first["name"]} if first is not None else None}
        if steps is not None and first is not None:
            binding["tool_uses"] = turn_plan.action_uses(first["id"], steps)
        self._rec("model_turn", state=state, messages=messages, content=blocks, stop_reason=stop_reason,
                  error_code=error_code, model_id=str(model_id), **binding)

    # ---- pieces -----------------------------------------------------------------------------------------

    async def _request(self, system: str, messages: List[Dict[str, Any]], tools: List[Dict[str, Any]]) -> Any:
        """One model request with a timeout measured on the injected clock."""
        req = asyncio.ensure_future(self.client.create(system, messages, tools))
        sleeper = asyncio.ensure_future(self.clock.sleep(self.timeout_s))
        try:
            await asyncio.wait({req, sleeper}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            sleeper.cancel()
            if not req.done():
                req.cancel()
            await asyncio.gather(sleeper, req, return_exceptions=True)
        if req.cancelled():
            raise _RequestTimeout()
        return req.result()

    @staticmethod
    def _parse(response: Any) -> Optional[Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]]:
        """(clean assistant content, tool_use list) or None for a malformed response."""
        content = response.get("content") if isinstance(response, dict) else None
        if not isinstance(content, list):
            return None
        clean: List[Dict[str, Any]] = []
        uses: List[Dict[str, Any]] = []
        for block in content:
            if not isinstance(block, dict):
                return None
            kind = block.get("type")
            if kind == "text":
                text = block.get("text")
                if isinstance(text, str) and text:
                    clean.append({"type": "text", "text": text})
            elif kind == "tool_use":
                tool_id, name = block.get("id"), block.get("name")
                if not isinstance(tool_id, str) or not tool_id or not isinstance(name, str):
                    return None
                raw = block.get("input")
                clean.append({"type": "tool_use", "id": tool_id, "name": name,
                              "input": raw if isinstance(raw, dict) else {}})
                uses.append({"id": tool_id, "name": name, "raw_input": raw})
        return clean, uses

    @staticmethod
    def _tool_result(tool_use_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        block: Dict[str, Any] = {"type": "tool_result", "tool_use_id": tool_use_id,
                                 "content": json.dumps(payload, sort_keys=True)}
        if "error" in payload:
            block["is_error"] = True
        return block

    async def _fallback(self, reason: str) -> None:
        if not self.allow_fallback:
            self.session.note("agent_failed", reason=reason, turn=self.turns)
            raise ClaudeFailed(reason)
        self.using_fallback = True
        self.session.note("agent_fallback", reason=reason, turn=self.turns)
        await FixedOrderDriver(self.handlers).run()
