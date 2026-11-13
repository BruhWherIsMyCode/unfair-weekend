"""What the PC server and the backend entry share about one finished call: the live log lines, the call record JSON
and where it is written.

- format_log_line(entry, session): one plain line for the interesting session-log entries (None for the rest).
  Every attempt stop, interruption and unusable result carries its reason(s) so a terminal reader can tell why.
- LiveLog: a `session.log` list that also shows each appended entry through a callback (printing never breaks a call).
- build_record / write_record: `<runs>/<call_id>.json`, the same shape for both callers.

No I/O at import time and no model code. Pure Python.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional


class LiveLog(list):
    """session.log that also shows a compact line for the interesting entries."""

    def __init__(self, show: Callable[[Dict[str, Any]], None]) -> None:
        super().__init__()
        self._show = show

    def append(self, entry: Dict[str, Any]) -> None:  # type: ignore[override]
        super().append(entry)
        try:
            self._show(entry)
        except Exception:  # printing must never break a call
            pass


def format_numbers(reading: Optional[Dict[str, Any]]) -> str:
    if not reading:
        return "no reading"
    low, high = reading.get("usual_low"), reading.get("usual_high")
    usual = "usual %.3f..%.3f" % (low, high) if low is not None and high is not None else "usual still learning"
    outside = {True: "OUTSIDE", False: "inside", None: "-"}[reading.get("outside")]
    return "value %.3f %s %s side=%s" % (reading["value"], usual, outside, reading.get("side"))


def format_speech(session: Any, attempt_id: Optional[str]) -> str:
    """The numbers of a sentence attempt for the terminal line."""
    details = getattr(session.modules.get("sentence"), "attempt_details", None)
    d = details(attempt_id) if callable(details) and attempt_id else None
    if not d:
        return "nothing usable heard"
    return "heard %r | word error rate %s | longest pause %s s | %s words/min | missing %s changed %s extra %s" % (
        d["heard"][:60], d["word_error_rate"], d["longest_pause_s"], d["words_per_min"], d["missing_words"],
        [c["heard"] for c in d["changed_words"]], d["extra_words"])


def arms_phase_name(elapsed_ms: int) -> str:
    """Which part of the 12 s arms window a capture time falls in, e.g. 'hold_left (left hand)'."""
    from analysis.session import limits

    for name, start, end, side in limits.ARMS_PHASES:
        if start <= elapsed_ms < end:
            return "%s (%s hand)" % (name, side) if side else name
    return "past the window"


def describe_stop(entry: Dict[str, Any], session: Any) -> str:
    """Why an attempt stopped: the stop kind, the interruption cause, the last thing the task said (its quality
    reasons and signal), how far into the capture it was and, for arms, which hand phase that is."""
    attempt = getattr(session, "attempts", {}).get(entry.get("attempt_id"))
    parts = ["%s" % entry.get("stop_kind"), "code=%s" % entry.get("code"), "why: %s" % entry.get("why")]
    if attempt is None:
        return " | ".join(parts)
    obs = attempt.latest_observation
    if obs is not None:
        parts.append("task=%s at %.1f s of the capture" % (attempt.task, obs.elapsed_ms / 1000.0))
        if attempt.task == "arms":
            parts.append("phase %s" % arms_phase_name(obs.elapsed_ms))
        reasons = ",".join(obs.quality.reasons) or "-"
        parts.append("last observation: quality=%s reasons=%s signal=%s" % (obs.quality.status, reasons, obs.signal))
    else:
        parts.append("task=%s, no observation yet (samples received: %d)" % (attempt.task, attempt.samples_received))
    return " | ".join(parts)


def format_log_line(entry: Dict[str, Any], session: Any) -> Optional[str]:
    kind = entry["kind"]
    rest = {k: v for k, v in entry.items() if k not in ("t", "kind")}
    if kind == "attempt_finished":
        last = session.results[-1] if session.results else {}
        line = "task %s %s: %s | %s" % (last.get("task"), rest.get("attempt_id"), rest.get("outcome"),
                                      format_numbers(last.get("reading")))
        quality = last.get("quality") or {}
        interruption = last.get("interruption") or {}
        if interruption:
            line += " | INTERRUPTED: %s" % interruption.get("code")
        if quality.get("status") == "unusable" or interruption:
            line += " | reasons: %s" % (",".join(quality.get("reasons") or []) or "-")
        if last.get("task") == "sentence":
            line += " | " + format_speech(session, rest.get("attempt_id"))
        return line
    if kind == "attempt_stopping":
        return "%6.1fs %-26s attempt %s | %s" % (entry["t"], "attempt stopping", rest.get("attempt_id"),
                                                  describe_stop(entry, session))
    shown = {"required_tasks": "required tasks", "call_driver": "driver", "agent_tool": "claude tool", "agent_fallback": "FALLBACK to fixed order",
             "fallback_step": "fixed step", "agent_refused": "step refused", "change_confirmed": "CHANGE CONFIRMED",
             "published": "family message", "concern_raised": "CONCERN RAISED", "resource_changed": "resource",
             "floor_off_suppressed": "FLOOR OFF - would have published", "task_refused": "task refused",
             "say": "say", "hang_up": "client hung up", "driver_crashed": "driver crashed",
             "cue": "cue", "recovery": "recovery"}
    if kind not in shown:
        return None
    return "%6.1fs %-26s %s" % (entry["t"], shown[kind], json.dumps(rest, sort_keys=True) if rest else "")


def format_terminal_line(entry: Dict[str, Any], session: Any) -> Optional[str]:
    """One plain line per step for the terminal of the phone path (v11). Unlike format_log_line it never shows a
    transcript, a number of a reading or a key: lines said (id, or `own: <text>` for the agent's own words), whether an
    answer was heard (yes/no), the end of every attempt (task, attempt id, outcome, reasons, retry_hint), the switch to
    the fixed order with its reason, and the final status. Every other entry is formatted as in format_log_line."""
    kind = entry["kind"]
    stamp = "%6.1fs " % entry["t"]
    if kind == "line_said":
        return stamp + "said: %s" % entry.get("line")
    if kind == "own_line":
        return stamp + "said: own: %s" % entry.get("text")
    if kind == "answer_heard":
        return stamp + "answer heard (%s): %s%s" % (entry.get("question"), "yes" if entry.get("heard") else "no",
                                                    " (follow-up)" if entry.get("followup") else "")
    if kind == "attempt_finished":
        last = session.results[-1] if session.results else {}
        quality = last.get("quality") or {}
        interruption = last.get("interruption") or {}
        measured = (last.get("outcome") == "completed" and quality.get("status") == "usable"
                    and last.get("reading") is not None)
        line = "attempt %s of %s ended: %s" % (entry.get("attempt_id"), last.get("task"), last.get("outcome"))
        if interruption:
            line += " | interrupted: %s" % interruption.get("code")
        reasons = ",".join(quality.get("reasons") or [])
        if reasons:
            line += " | reasons: %s" % reasons
        if not measured and last:
            from analysis.session.agent_tools import retry_hint_for

            line += " | retry_hint: %s" % retry_hint_for(str(last.get("task")), last)
        return stamp + line
    if kind == "agent_fallback":
        return stamp + "claude gave way to the fixed order: %s" % entry.get("reason")
    if kind == "call_finished":
        return stamp + "call finished, status: %s" % entry.get("status")
    return format_log_line(entry, session)


def build_record(call_id: str, person: str, label: str, claude: str, safety_floor: bool, summary: Dict[str, Any],
                 family_text: str, session_log: Any, started_wall: float) -> Dict[str, Any]:
    return {"call_id": call_id, "person": person, "label": label, "claude": claude,
            "safety_floor": safety_floor, "summary": summary, "family_text": family_text,
            "session_log": list(session_log),
            "timings": {"started_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(started_wall)),
                        "duration_s": round(time.time() - started_wall, 1),
                        "session_duration_s": summary.get("duration_s")}}


def write_record(runs_dir: Path, call_id: str, record: Dict[str, Any], out: Callable[[str], Any] = print) -> bool:
    """Write <runs_dir>/<call_id>.json. False (and one message through `out`) when the disk refuses."""
    try:
        runs_dir = Path(runs_dir)
        runs_dir.mkdir(parents=True, exist_ok=True)
        (runs_dir / ("%s.json" % call_id)).write_text(json.dumps(record, indent=2, default=str), "utf-8")
        return True
    except OSError as exc:
        out("could not write the call record: %s" % type(exc).__name__)
        return False
