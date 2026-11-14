"""One model decision up to the next answer or task result; existing handlers execute each step in order.

The model may record the answer it already heard, note a concern, acknowledge it and choose the next step in
one request. A plan has exactly one action that gathers new information, at its end. Never plan past it.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from analysis.session import limits

TOOL_NAME = "advance_call"
NEXT_TOOLS = ("ask", "ask_followup", "run_task", "recover_resource", "finish_call")
PREFIX_TOOLS = {"answer": "record_answer", "concern": "alert_family"}
Action = Tuple[str, Dict[str, Any]]


def tool_definition(tools: List[Dict[str, Any]]) -> Dict[str, Any]:
    definitions = {t["name"]: t for t in tools}
    return {
        "name": TOOL_NAME,
        "description": (
            "Choose the whole next turn in one decision: optionally record the answer already heard, record a "
            "concern, and say a short reaction, then ask/listen, run a check, recover a resource or finish. "
            "Only next gathers new information; wait for its result before deciding anything further. "
            "When next is a follow-up, leave that question's answer unrecorded. On the first turn use greet=true "
            "and next=ask(q_medicine), without other fields."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "greet": {"type": "boolean"},
                "answer": definitions["record_answer"]["input_schema"],
                "concern": definitions["alert_family"]["input_schema"],
                "reaction": {"type": "string", "maxLength": limits.OWN_LINE_MAX_CHARS},
                "next": {
                    "oneOf": [
                        {"type": "object", "properties": {
                            "tool": {"type": "string", "enum": [name]},
                            "input": definitions[name]["input_schema"],
                        }, "required": ["tool", "input"], "additionalProperties": False}
                        for name in NEXT_TOOLS
                    ],
                },
            },
            "required": ["next"],
            "additionalProperties": False,
        },
    }


def actions(args: Any, handlers: Any) -> Tuple[List[Action], Optional[str]]:
    """Validate the complete plan before any side effect; state guards run again before each action."""
    if not isinstance(args, dict):
        return [], "input must be an object"
    if set(args) - {"greet", "answer", "concern", "reaction", "next"}:
        return [], "unexpected fields"
    if "greet" in args and not isinstance(args["greet"], bool):
        return [], "greet must be a boolean"
    next_ = args.get("next")
    if not isinstance(next_, dict) or set(next_) != {"tool", "input"}:
        return [], "next must contain tool and input"
    name = next_["tool"]
    if name not in NEXT_TOOLS:
        return [], "next.tool must be one of %s" % ", ".join(NEXT_TOOLS)
    out: List[Action] = []
    if args.get("greet"):
        if set(args) - {"greet", "next"} or name != "ask" or next_["input"] != {"question_id": "q_medicine"}:
            return [], "greet must be followed only by ask(q_medicine)"
        out.append(("say", {"line_id": "greet"}))
    # A concern is kept even if a later playback or resource change stops the plan.
    for field in ("concern", "answer"):
        if field in args:
            out.append((PREFIX_TOOLS[field], args[field]))
    if "reaction" in args:
        out.append(("say_own", {"text": args["reaction"]}))
    out.append((name, next_["input"]))
    for tool, input_ in out:
        problem = handlers.validate(tool, input_)
        if problem is not None:
            return [], "%s: %s" % (tool, problem)
    if name == "ask_followup" and args.get("answer", {}).get("question_id") == next_["input"]["question_id"]:
        return [], "leave the answer unrecorded while asking its follow-up"
    return out, None


def action_uses(tool_id: str, steps: List[Action]) -> List[Dict[str, str]]:
    """Stable child ids let several executed actions belong to their one real model request."""
    return [{"id": "%s.step%d" % (tool_id, i + 1), "name": name} for i, (name, _) in enumerate(steps)]
