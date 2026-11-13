"""Camera debug for the parent app: run Agent 3's real smile or arms module on frames from the phone, outside a call.

Pure debug, no assistant and no call. The phone opens a run, posts its camera frames on the task's own timeline and
gets back, for the latest analysed frame, what the module saw (face, hands, which side of her face each hand is on),
the module's live observations (quality and reasons) and finally its result. A FrameProbe stands in for
analysis.debug_view.DebugView: the modules call it per frame; it only reports and never changes a reading.

Nothing is stored: no frames, no call record, no baseline update. Her usual is only read, to show whether a reading
would be inside it. Runs live in memory and are dropped after RUN_TTL_S.

What the family would get: the screen's "check" tries of a task form one series, judged with Agent 3's own call rules
(analysis/session/rules.py: confirmation, attempt budget, session status; the repeat rule of agent_tools.needs_another)
and worded with the real family message. A series ends where a call would stop trying; the next check starts a new one.
Her stored usual is often still being learned (then nothing is compared and no change alert is possible), so "teach"
tries can build a practice usual in memory (PRACTICE_NEEDED tries, Agent 3's baseline math). It is never stored.
"""

import asyncio
import dataclasses
import math
import os
import threading
from time import monotonic
from typing import Any
from uuid import uuid4

from .delivery import render
from .runner import ensure_analysis_path

RUN_TTL_S = 10 * 60
MAX_RUNS = 4
FINISH_TIMEOUT_S = 30.0
PRACTICE_NEEDED = 3  # teach tries until the practice usual is ready (a real usual needs config.BASELINE_NEEDED calls)
PURPOSES = ("check", "teach")


class DebugUnavailable(RuntimeError):
    pass


def plain(value: Any) -> Any:
    """JSON-safe: numpy numbers to float, tuples to lists, dataclasses to dicts; non-finite numbers to None."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return plain(dataclasses.asdict(value))
    if isinstance(value, dict):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value
    if isinstance(value, int):
        return value
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    return number if math.isfinite(number) else None


class FrameProbe:
    """Duck-typed debug hook for StreamArms/StreamSmile. Called from the analysis executor thread."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._latest: dict[str, Any] | None = None
        self.frames = 0
        # Arms: per phase, how many frames had the hand on her left, her right, no hand, or no face.
        self._tally: dict[str, dict[str, int]] = {}

    def latest(self) -> dict[str, Any] | None:
        with self._lock:
            return self._latest

    def tally(self) -> dict[str, dict[str, int]]:
        with self._lock:
            return {phase: dict(counts) for phase, counts in self._tally.items()}

    def _keep(self, frame: dict[str, Any]) -> None:
        with self._lock:
            self.frames += 1
            self._latest = frame

    @staticmethod
    def _size(image: Any) -> tuple[int | None, int | None]:
        shape = getattr(image, "shape", None)
        return (int(shape[1]), int(shape[0])) if shape is not None and len(shape) >= 2 else (None, None)

    def arms_frame(self, attempt_id: str, ms: int, expected_side: str, image: Any, face_obs: Any,
                   hands: Any, mirrored: bool) -> None:
        from analysis.debug_view import CATEGORIES, arms_phase_of, frame_category, hand_side_facts

        facts = hand_side_facts(face_obs, hands, expected_side, mirrored)
        width, height = self._size(image)
        with self._lock:
            counts = self._tally.setdefault(arms_phase_of(ms), {c: 0 for c in CATEGORIES})
            counts[frame_category(facts)] += 1
        eyes = facts["eye_points"]
        roll = (math.degrees(math.atan2(eyes[1][1] - eyes[0][1], eyes[1][0] - eyes[0][0]))
                if len(eyes) == 2 else None)
        self._keep(plain({
            "task": "arms", "ms": ms, "expected": expected_side, "mirrored": bool(mirrored),
            "width": width, "height": height,
            "face": facts["face"], "face_small": facts["face_small"], "midline_x": facts["midline_x"],
            "eye_distance_px": facts["unit"], "roll_deg": roll, "eye_points": eyes,
            "hands": [{k: h.get(k) for k in ("side", "eye_widths", "if_mirrored_flipped", "centre", "box")}
                      for h in facts["hands"]],
        }))

    def smile_frame(self, attempt_id: str, ms: int, image: Any, obs: Any, reading: Any, mirrored: bool) -> None:
        width, height = self._size(image)
        found = obs is not None and getattr(obs, "face_found", False)
        points = dict(obs.points) if found else {}
        fields = ("usable", "reason", "smile_strength", "corner_height", "yaw_deg", "pitch_deg", "roll_deg")
        self._keep(plain({
            "task": "smile", "ms": ms, "mirrored": bool(mirrored), "width": width, "height": height,
            "face": bool(found),
            "eye_points": [points[k] for k in ("her_right_eye", "her_left_eye") if k in points],
            "mouth_points": [points[k] for k in ("her_right_mouth", "her_left_mouth") if k in points],
            "reading": {k: getattr(reading, k, None) for k in fields} if reading is not None else None,
        }))


class DebugRun:
    def __init__(self, task: str, module: Any, baseline: dict[str, Any], capture: dict[str, Any],
                 cues: list[dict[str, Any]], probe: FrameProbe, purpose: str = "check",
                 on_result: Any = None) -> None:
        self.run_id = f"dbg_{uuid4().hex[:12]}"
        self.task = task
        self.module = module
        self.purpose = purpose
        self.usual_source = "stored"
        self.verdict: Any = None  # callable returning the task's verdict, set by the desk
        self._on_result = on_result
        self.capture = capture
        self.cues = cues
        self.probe = probe
        self.created = monotonic()
        self.observations: list[dict[str, Any]] = []
        self.result: dict[str, Any] | None = None
        self.error: str | None = None
        self._queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(maxsize=200)
        self._last_sequence = -1
        from analysis.session.models import TaskContext

        self._context = TaskContext(f"call_{self.run_id}", self.run_id, task, baseline)
        self._job = asyncio.create_task(self._run(module))

    async def _run(self, module: Any) -> None:
        async def samples():
            while (item := await self._queue.get()) is not None:
                yield item

        def emit(observation: Any) -> None:
            data = observation.to_dict() if hasattr(observation, "to_dict") else plain(observation)
            self.observations.append(plain(data))

        try:
            result = await module.run(self._context, samples(), emit)
            self.result = plain(result.to_dict() if hasattr(result, "to_dict") else result)
        except Exception as exc:  # a debug run reports its failure instead of raising into the request
            self.error = f"{type(exc).__name__}: {exc}"[:300]
            return
        if self._on_result is not None:
            try:
                self._on_result(self)
            except Exception as exc:  # the verdict is extra; a bug there must not lose the module's result
                self.error = f"verdict: {type(exc).__name__}: {exc}"[:300]

    def add(self, sequence: int, captured_ms: int, data: bytes) -> None:
        if self._job.done() or sequence <= self._last_sequence:
            return
        self._last_sequence = sequence
        try:
            self._queue.put_nowait({"sequence": sequence, "captured_ms": captured_ms, "format": "jpeg", "data": data})
        except asyncio.QueueFull:
            pass  # the analysis is behind; a debug run may skip frames

    async def finish(self) -> None:
        if not self._job.done():
            await self._queue.put(None)
        try:
            await asyncio.wait_for(asyncio.shield(self._job), FINISH_TIMEOUT_S)
        except TimeoutError:
            self.error = self.error or "The analysis did not finish in time."

    def cancel(self) -> None:
        self._context.cancel_event.set()
        self._job.cancel()

    def snapshot(self, since: int = 0) -> dict[str, Any]:
        return {"run_id": self.run_id, "task": self.task, "frames_analysed": self.probe.frames,
                "tally": self.probe.tally(),
                "frame": self.probe.latest(), "observations": self.observations[since:],
                "observation_count": len(self.observations), "result": self.result, "error": self.error,
                "done": self._job.done(), "purpose": self.purpose, "usual_source": self.usual_source,
                "verdict": self.verdict() if self.verdict is not None else None}


class Series:
    """One task's tries on the debug screen: the check tries of one pretend call and the practice usual's values."""

    def __init__(self) -> None:
        self.checks: list[Any] = []  # rules.FinalRecord, in order
        self.notes: list[dict[str, Any]] = []  # per check try: quality reasons, and both hands' values (arms)
        self.practice: list[dict[str, float]] = []  # teach tries' values


class DebugDesk:
    def __init__(self, person_id: str, person: Any = None) -> None:
        self.person_id = person_id
        self.person = person
        self.runs: dict[str, DebugRun] = {}
        self.series: dict[str, Series] = {"smile": Series(), "arms": Series()}

    def start(self, task: str, mirrored: bool | None, purpose: str = "check") -> DebugRun:
        if task not in ("smile", "arms"):
            raise ValueError("task must be smile or arms")
        if purpose not in PURPOSES:
            raise ValueError("purpose must be check or teach")
        self._expire()
        ensure_analysis_path()
        probe = FrameProbe()
        module, baseline = self.build(task, mirrored, probe)
        source = "stored"
        practice = self.practice_usual(task)
        if purpose == "check" and not baseline.get("ready") and practice is not None and practice.get("ready"):
            baseline, source = practice, "practice"
        if purpose == "check" and not needs_another(self.series[task].checks):
            self._new_series(task)  # a call would have stopped trying: this try starts the next pretend call
        definition = module.definition()
        run = DebugRun(task, module, baseline, definition["capture"], definition.get("cues") or [], probe,
                       purpose, self._conclude)
        run.usual_source = source
        run.verdict = lambda: self.verdict(task)
        self.runs[run.run_id] = run
        return run

    def build(self, task: str, mirrored: bool | None, probe: FrameProbe) -> tuple[Any, dict[str, Any]]:
        """Agent 3's production module for `task` with `probe` as its debug hook, and her usual for that task."""
        ensure_analysis_path()
        try:
            from analysis.backend_entry import Settings, make_baseline_source
            from analysis.tasks.stream_arms import StreamArms
            from analysis.tasks.stream_smile import StreamSmile
        except ImportError as exc:
            raise DebugUnavailable(f"Agent 3's analysis is not installed here ({exc.name}).") from None
        settings = Settings(os.environ, self.person_id)
        mirror = settings.mirrored if mirrored is None else mirrored
        module = (StreamSmile if task == "smile" else StreamArms)(mirrored=mirror, debug=probe)
        source = make_baseline_source(settings.person_key, settings.data_dir, settings.baseline_needed)
        return module, source.load_baselines().get(task, {})

    # ---- the pretend call's verdict ------------------------------------------------------------------------------

    def reset(self, task: str) -> None:
        self.series[task] = Series()

    def _new_series(self, task: str) -> None:
        self.series[task].checks.clear()
        self.series[task].notes.clear()

    def practice_usual(self, task: str) -> dict[str, Any] | None:
        """Agent 3's baseline shape built from the teach tries, or None before the first one."""
        values = self.series[task].practice
        if not values:
            return None
        from analysis.baseline import make_baseline

        if task == "smile":
            usual = make_baseline([v["value"] for v in values], metric="corner_height", needed=PRACTICE_NEEDED)
            usual["snapshot_id"] = f"practice_smile_{len(values)}"
            return usual
        hands = {side: make_baseline([v[side] for v in values], metric="hand_sink", needed=PRACTICE_NEEDED)
                 for side in ("left", "right")}
        return {"snapshot_id": f"practice_arms_{len(values)}", "sessions": len(values), "needed": PRACTICE_NEEDED,
                "ready": all(h["ready"] for h in hands.values()), "hand_left": hands["left"],
                "hand_right": hands["right"]}

    def _conclude(self, run: DebugRun) -> None:
        """A finished run: a teach try feeds the practice usual, a check try joins the pretend call's tries."""
        result = run.result if isinstance(run.result, dict) else {}
        if not result.get("outcome"):
            return
        series = self.series[run.task]
        quality = result.get("quality") or {}
        reading = result.get("reading")
        usable = result["outcome"] == "completed" and quality.get("status") == "usable" and bool(reading)
        per_hand = getattr(run.module, "attempt_values", None)
        hands = per_hand(run._context.attempt_id) if callable(per_hand) and run.task == "arms" else None
        if run.purpose == "teach":
            values = hands if run.task == "arms" else ({"value": reading["value"]} if usable else None)
            if usable and values:
                series.practice.append({k: float(v) for k, v in values.items()})
                self._new_series(run.task)  # checks against the older usual cannot confirm with later ones
            return
        from analysis.session.rules import FinalRecord

        baseline = result.get("baseline") or {}
        record = FinalRecord(run.task, run._context.attempt_id, result["outcome"], usable,
                             reading if usable else None, str(baseline.get("snapshot_id") or ""),
                             bool(baseline.get("ready")))
        series.checks.append(record)
        series.notes.append({"reasons": list(quality.get("reasons") or []), "hands": dict(hands) if hands else None})

    def verdict(self, task: str) -> dict[str, Any]:
        series = self.series[task]
        usual = self._usual_view(task)
        tries = [_try_view(i + 1, r, n) for i, (r, n) in enumerate(zip(series.checks, series.notes))]
        last_reasons = series.notes[-1]["reasons"] if series.notes else []
        return {"task": task, "usual": usual, "tries": tries, "practice_tries": len(series.practice),
                "practice_needed": PRACTICE_NEEDED,
                **judge(task, series.checks, last_reasons, self.person, usual)}

    def _usual_view(self, task: str) -> dict[str, Any]:
        stored: dict[str, Any] = {}
        try:
            ensure_analysis_path()
            from analysis.backend_entry import Settings, make_baseline_source

            settings = Settings(os.environ, self.person_id)
            source = make_baseline_source(settings.person_key, settings.data_dir, settings.baseline_needed)
            stored = source.load_baselines().get(task, {})
        except Exception:  # without the analysis there is no stored usual to show
            stored = {}
        practice = self.practice_usual(task)
        if stored.get("ready"):
            chosen, source_name = stored, "stored"
        elif practice is not None and practice.get("ready"):
            chosen, source_name = practice, "practice"
        else:
            chosen, source_name = None, "none"
        return {"source": source_name, "ready": chosen is not None,
                "stored_sessions": stored.get("sessions"), "stored_needed": stored.get("needed"),
                "ranges": _ranges(task, chosen) if chosen is not None else None}

    def get(self, run_id: str) -> DebugRun | None:
        self._expire()
        return self.runs.get(run_id)

    def _expire(self) -> None:
        now = monotonic()
        stale = [r for r in self.runs.values() if now - r.created > RUN_TTL_S]
        overflow = sorted(self.runs.values(), key=lambda r: r.created)[:max(0, len(self.runs) - MAX_RUNS + 1)]
        for run in {r.run_id: r for r in stale + overflow}.values():
            run.cancel()
            self.runs.pop(run.run_id, None)

    def close(self) -> None:
        for run in self.runs.values():
            run.cancel()
        self.runs.clear()


# ---- the pretend call's rules ------------------------------------------------------------------------------------------

def needs_another(records: list[Any]) -> bool:
    """agent_tools.CallTools.needs_another without a session: another usable reading is needed (none yet, or a first
    outside reading not yet settled by a second one) and the attempt budget allows it. Keep in step with it."""
    from analysis.session import rules

    if rules.attempt_budget(records) is not None:
        return False
    usable = [r for r in records if r.usable and r.reading is not None]
    if not usable:
        return True
    if rules.evaluate_sign(records).confirmed:
        return False
    last = usable[-1]
    return len(usable) < 2 and bool(last.baseline_ready) and rules.reading_direction(last.reading) != 0


def _words(reasons: list[str]) -> str:
    return ", ".join(r.replace("_", " ") for r in reasons) or "no reason given"


def _message(kind: str, person: Any, signs: list[dict[str, Any]] | None = None) -> str | None:
    if person is None:
        return None
    try:
        return render(kind, person, signs)
    except ValueError:
        return None


def _side(reading: dict[str, Any] | None) -> str:
    side = (reading or {}).get("side")
    return f" on her {side} side" if side else ""


def judge(task: str, records: list[Any], last_reasons: list[str], person: Any,
          usual: dict[str, Any]) -> dict[str, Any]:
    """What a real call would do after these tries of one task, and what the family would get."""
    from analysis.session import limits, rules

    def out(state: str, status: str | None, headline: str, lines: list[str], message: str | None = None):
        if state != "no_tries":
            lines = lines + ["Judged for this check alone. A real call also needs the other check, and the call "
                             "assistant can alert the family itself if she says she feels unwell."]
        return {"state": state, "status": status, "headline": headline, "lines": lines, "family_message": message}

    if not records:
        return out("no_tries", None, "No check tries yet",
                   ["Run a check. This box then shows what a real call would do with it and what the family "
                    "would get."])
    evaluation = rules.evaluate_sign(records)
    count, last = len(records), records[-1]
    if evaluation.confirmed:
        signs = [{"sign": limits.TASK_SIGN[task], "confirmed": True, "side": evaluation.side}]
        return out("settled", "change_noticed", "Alert: the family is told to call her now", [
            f"Two measured tries were outside her usual{_side({'side': evaluation.side})}, in the same direction. "
            "That is the confirmation rule.",
            "A real call sends this at once, with the camera frames of both tries."],
            _message("change_noticed", person, signs))
    if needs_another(records):
        left = rules.attempts_left(records)
        if not last.usable:
            return out("try_again", None, "Not measured: the call asks her to try again", [
                f"Try {count} could not be measured: {_words(last_reasons)}.",
                "A try that is not measured never counts towards an alert.",
                f"Tries left in this call: {left}."])
        return out("try_again", None, "Outside her usual once: the call asks for one more try", [
            f"Try {count} was outside her usual{_side(last.reading)}. One try is never enough.",
            "If the next measured try is outside again, in the same direction and on the same side, the family "
            "gets the alert.",
            f"Tries left in this call: {left}."])
    status = rules.session_status({task: evaluation}, [task])
    message = _message(status, person)
    if status == "incomplete":
        if not any(r.usable for r in records):
            lines = [f"None of the {count} tries could be measured (last: {_words(last_reasons)}).",
                     "Not being measured is not read as a change: the family gets the 'could not be finished' "
                     "message, which also asks them to call her."]
            if task == "arms":
                lines.append("So a hand that never comes up into view ends here, not in an arm alert. The arm "
                             "alert needs a hand that is seen and sinks lower than her usual.")
            return out("settled", status, "Not measured twice: the call skips this check", lines, message)
        return out("settled", status, "Outside once, never confirmed: the check is left open", [
            "One measured try was outside her usual, but no second measured try confirmed it.",
            "The family gets the 'could not be finished' message."], message)
    if status == "learning":
        stored = f" ({usual.get('stored_sessions')} of {usual.get('stored_needed')} calls)" \
            if usual.get("stored_needed") else ""
        return out("settled", status, "Measured, but there is no usual to compare with", [
            f"Her usual is still being learned{stored}, so nothing is compared and no change alert can be sent.",
            f"To try the alert here, do {PRACTICE_NEEDED} 'Teach usual' tries with normal movements first."],
            message)
    return out("settled", status, "Inside her usual: no alert",
               ["The measured try was inside her usual range. The call moves on."], message)


def _try_view(number: int, record: Any, note: dict[str, Any]) -> dict[str, Any]:
    reading = record.reading or {}
    return plain({"number": number, "measured": bool(record.usable), "outcome": record.outcome,
                  "reasons": note.get("reasons") or [], "hands": note.get("hands"),
                  "value": reading.get("value"), "usual_low": reading.get("usual_low"),
                  "usual_high": reading.get("usual_high"), "outside": reading.get("outside"),
                  "side": reading.get("side"), "compared": bool(record.baseline_ready)})


def _ranges(task: str, usual: dict[str, Any]) -> dict[str, list[Any]]:
    if task == "smile":
        return plain({"value": [usual.get("usual_low"), usual.get("usual_high")]})
    return plain({side: [(usual.get(f"hand_{side}") or {}).get("usual_low"),
                         (usual.get(f"hand_{side}") or {}).get("usual_high")] for side in ("left", "right")})
