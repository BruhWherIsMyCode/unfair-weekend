"""The fixed-order call driver: the whole call without any model, and the fallback when Claude fails.

It starts from WHATEVER the current state is (it can take over mid-call) and runs the remaining steps in order:

    greet -> q_medicine, q_feeling, q_unusual (ask once, re-ask once if not heard, then record a deterministic
    value) -> smile -> arms -> sentence (each repeated while another reading is needed; after a recoverable
    resource interruption: recover once, then repeat if restored, else skip) -> finish_call.

`next_step(state)` is pure and needs no asyncio; `FixedOrderDriver.run()` is a small loop around it. Every step
goes through ToolHandlers, so the same guards apply as for Claude.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional, Tuple

from analysis.session import limits

Step = Tuple[Optional[str], Dict[str, Any]]

_YES = {"yes", "yeah", "yep", "yup", "sure", "took", "taken", "did", "have", "ok", "okay"}
_STRONG_YES = {"yes", "yeah", "yep", "yup"}
_NO = {"no", "nope", "not", "never", "didn't", "didnt", "haven't", "havent", "forgot", "forget", "hasn't"}
_UNSURE = ("not sure", "don't know", "dont know", "do not know", "maybe", "i can't remember", "cannot remember",
           "can't remember", "don't remember")


def parse_medicine(transcript: str) -> str:
    """Keyword match: "yes" / "no" / "unclear". A negative word wins over a plain yes word; an explicit yes next
    to a negative word is unclear."""
    text = (transcript or "").lower()
    if not text.strip() or any(p in text for p in _UNSURE):
        return "unclear"
    words = set(re.findall(r"[a-z']+", text))
    has_no, has_yes = bool(words & _NO), bool(words & _YES)
    if has_no:
        return "unclear" if words & _STRONG_YES else "no"
    return "yes" if has_yes else "unclear"


def answer_value(question_id: str, transcript: str) -> str:
    """The deterministic value the fallback records for an answer."""
    if question_id == "q_medicine":
        return parse_medicine(transcript)
    return (transcript or "")[:limits.ANSWER_MAX_CHARS]


def step_key(tool: str, args: Dict[str, Any]) -> str:
    return "%s:%s" % (tool, json.dumps(args, sort_keys=True))


def next_step(state: Dict[str, Any]) -> Step:
    """The next (tool, input) from a ToolHandlers.snapshot() dict, or (None, {}) when the call is over.

    state["blocked"] lists step keys that were already refused in this driver run; they are not tried again."""
    if state["ended"]:
        return None, {}
    blocked = set(state.get("blocked", ()))

    def free(tool: str, args: Dict[str, Any]) -> bool:
        return step_key(tool, args) not in blocked

    if not state["greeted"]:
        greet = ("say", {"line_id": "greet"})
        return greet if free(*greet) else ("finish_call", {})

    for q in limits.QUESTIONS:
        s = state["questions"][q]
        if s["recorded"]:
            continue
        # A follow-up asked by Claude (v11) may have heard what the question itself did not: use it, do not re-ask.
        followup = s.get("followup_transcript") or ""
        heard = s["heard"] or bool(followup)
        ask = ("ask", {"question_id": q})
        if (s["asks"] == 0 or (s["asks"] == 1 and not heard)) and free(*ask):
            return ask
        text = s["transcript"] if s["heard"] else followup
        record = ("record_answer", {"question_id": q, "value": answer_value(q, text)})
        if s["asks"] > 0 and free(*record):
            return record

    for task in limits.TASKS:
        t = state["tasks"][task]
        if t["done"] or t["skip"] or t["unimplemented"]:
            continue
        if t["recovery_pending"]:
            recover = ("recover_resource", {"resource": t["resource"]})
            if free(*recover):
                return recover
            continue
        run = ("run_task", {"task": task})
        if free(*run):
            return run
    return "finish_call", {}


class FixedOrderDriver:
    def __init__(self, handlers: Any, max_steps: int = 80) -> None:
        self.handlers = handlers
        self.session = handlers.session
        self.max_steps = max_steps

    async def run(self) -> None:
        handlers = self.handlers
        blocked: set = set()
        for _ in range(self.max_steps):
            state = handlers.snapshot()
            state["blocked"] = sorted(blocked)
            tool, args = next_step(state)
            if tool is None:
                return
            result = await handlers.execute(tool, args)
            failed = "error" in result
            shown = {k: v for k, v in args.items() if k != "value"}
            self.session.note("fallback_step", tool=tool, input=shown, ok=not failed)
            if result.get("error") == "call ended":
                return
            if tool == "finish_call":
                if failed and not handlers.ended:
                    await handlers.force_finish()  # a fixed-order call always ends
                return
            if failed:
                blocked.add(step_key(tool, args))
        if not handlers.ended:
            self.session.note("fallback_step_cap", steps=self.max_steps)
            await handlers.force_finish()
