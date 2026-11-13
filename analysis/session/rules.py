"""Pure decision rules of the call session. No I/O, no clock reads: callers pass the times in.

- decide_interrupt: should the active attempt stop, and why (plan section 5).
- attempt_budget: three attempts per task, at most two usable, at most two not measured (one repeat).
- evaluate_sign / session_status: deterministic confirmation and session status (plan "Attempt and session rules").
- baseline_values_to_store: which finished attempts may enter the baseline.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

from analysis.session import limits


# ---- interrupt decision ---------------------------------------------------------------------

@dataclass
class ResourceInfo:
    state: str = "unknown"  # available / unavailable / unknown
    reason: Optional[str] = None
    event_id: Optional[str] = None


@dataclass
class WatchView:
    """Everything decide_interrupt needs about one active attempt. Times are session-clock seconds."""

    state: str  # preparing / capturing / finalizing
    task: str
    now: float
    created_at: float
    capture_command_at: Optional[float]
    capture_started_at: Optional[float]
    first_sample_at: Optional[float]
    last_sample_at: Optional[float]
    samples_received: int
    last_analyzer_at: Optional[float]
    framing_bad_since: Optional[float]
    framing_bad_event_id: Optional[str]
    unperformable_event_id: Optional[str]
    input_closed: bool
    window_s: float


@dataclass
class Decision:
    code: str  # a TaskInterrupt code
    source_event_id: Optional[str]  # None = server timeout
    recoverable: bool
    detail: str


def resource_loss_decision(task: str, resource: str, info: ResourceInfo) -> Optional[Decision]:
    """Immediate rule for a lost resource that the task needs."""
    if resource != limits.TASK_RESOURCE.get(task) or info.state != "unavailable":
        return None
    return Decision("%s_unavailable" % resource, info.event_id, info.reason != "permission_denied",
                    "%s unavailable (%s)" % (resource, info.reason))


def decide_interrupt(view: WatchView, resources: Dict[str, ResourceInfo]) -> Optional[Decision]:
    """The first interrupt rule that is met, or None. Order: resource, unperformable, framing, input, analyzer."""
    now = view.now
    needed = limits.TASK_RESOURCE.get(view.task)
    if needed is not None and needed in resources:
        d = resource_loss_decision(view.task, needed, resources[needed])
        if d is not None:
            return d

    if view.unperformable_event_id is not None:
        return Decision("task_unperformable", view.unperformable_event_id, True, "module reported task_unperformable")

    if view.framing_bad_since is not None and now - view.framing_bad_since >= limits.FRAMING_UNUSABLE_S:
        return Decision("framing_unusable", view.framing_bad_event_id, True,
                        "framing unusable for %.1f s" % (now - view.framing_bad_since))

    start_candidates = [t for t in (view.capture_started_at, view.first_sample_at) if t is not None]
    start = min(start_candidates) if start_candidates else None

    if view.state == "preparing" and view.capture_command_at is None:
        if now - view.created_at >= limits.PREPARE_PLAYBACK_S:
            return Decision("capture_failed", None, True, "preparation took longer than %.0f s" % limits.PREPARE_PLAYBACK_S)
        return None

    if start is None:
        if view.capture_command_at is not None and now - view.capture_command_at >= limits.NO_ACK_S:
            return Decision("no_samples", None, True, "no capture.started or sample within %.0f s" % limits.NO_ACK_S)
        return None

    if not view.input_closed:
        if view.samples_received == 0:
            if now - start >= limits.FIRST_SAMPLE_S:
                return Decision("no_samples", None, True, "no first sample within %.0f s" % limits.FIRST_SAMPLE_S)
        elif view.last_sample_at is not None and now - view.last_sample_at >= limits.SAMPLE_GAP_S:
            return Decision("no_samples", None, True, "sample gap of %.1f s" % (now - view.last_sample_at))
        else:
            analyzer_at = view.last_analyzer_at if view.last_analyzer_at is not None else start
            if now - max(analyzer_at, start) >= limits.ANALYSIS_STALLED_S:
                return Decision("analysis_stalled", None, True,
                                "no observation for %.1f s while samples arrive" % (now - max(analyzer_at, start)))

    if now - start >= view.window_s + limits.FINALIZE_EXTRA_S:
        code = "analysis_stalled" if view.input_closed else "capture_failed"
        return Decision(code, None, True, "finalize deadline (window + %.0f s) passed" % limits.FINALIZE_EXTRA_S)
    return None


# ---- budget ---------------------------------------------------------------------------------

@dataclass
class FinalRecord:
    """One finished attempt, as the session remembers it."""

    task: str
    attempt_id: str
    outcome: str  # completed / could_not_perform / failed / cancelled / interrupted
    usable: bool  # completed + usable quality + a final reading
    reading: Optional[Dict[str, Any]]  # the Reading dict, or None
    snapshot_id: str
    baseline_ready: bool

    @property
    def interrupted(self) -> bool:
        return self.outcome == "interrupted"


def attempt_budget(records: Sequence[FinalRecord]) -> Optional[str]:
    """None when another attempt of this task is allowed, else the reason (a slug).

    Three attempts in total, at most two usable, and at most two NOT-measured ones (unusable, could_not_perform,
    interrupted, failed): one repeat for a not-measured attempt, then the task is skipped."""
    if len(records) >= limits.MAX_ATTEMPTS_PER_TASK:
        return "attempt_budget_exhausted"
    if sum(1 for r in records if r.usable) >= limits.MAX_USABLE_PER_TASK:
        return "usable_readings_complete"
    if sum(1 for r in records if not r.usable) >= limits.MAX_NOT_MEASURED_PER_TASK:
        return "attempt_budget_exhausted"
    return None


def attempts_left(records: Sequence[FinalRecord]) -> int:
    """How many more attempts of this task the budget allows (0 when attempt_budget gives a reason)."""
    if attempt_budget(records) is not None:
        return 0
    not_measured = sum(1 for r in records if not r.usable)
    return max(0, min(limits.MAX_ATTEMPTS_PER_TASK - len(records), limits.MAX_NOT_MEASURED_PER_TASK - not_measured))


# ---- confirmation and status ----------------------------------------------------------------

def reading_direction(reading: Dict[str, Any]) -> int:
    """+1 above her usual range, -1 below, 0 inside or unknown. The direction is the 'same sign' of the rule."""
    value, low, high = reading.get("value"), reading.get("usual_low"), reading.get("usual_high")
    if reading.get("outside") is not True or value is None or low is None or high is None:
        return 0
    if value > high:
        return 1
    if value < low:
        return -1
    return 0


def sides_match(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    """Two readings agree on the side when both name one and it is the same (a missing side does not disagree).

    Left hand outside, then right hand outside is NOT a confirmation: they are different findings."""
    sa, sb = a.get("side"), b.get("side")
    return sa is None or sb is None or sa == sb


@dataclass
class SignEvaluation:
    attempts: List[Dict[str, Any]]  # usable readings, at most two (the confirming pair when confirmed)
    confirmed: bool
    side: Optional[str]
    latest_outside: Optional[bool]  # outside flag of the latest usable reading, None when there is none
    baseline_ready: Optional[bool]  # of the latest usable reading


def evaluate_sign(records: Sequence[FinalRecord]) -> SignEvaluation:
    """Confirmation: two independent usable final readings outside on the same sign (direction) and the same
    side (compared when both name one), the same metric version and the same frozen baseline.
    Interrupted/partial attempts never count."""
    usable = [r for r in records if r.usable and r.reading is not None]
    if not usable:
        return SignEvaluation([], False, None, None, None)
    outside = [r for r in usable if r.baseline_ready and reading_direction(r.reading) != 0]
    for i in range(len(outside)):
        for j in range(i + 1, len(outside)):
            a, b = outside[i], outside[j]
            if (reading_direction(a.reading) == reading_direction(b.reading)
                    and sides_match(a.reading, b.reading)
                    and a.reading["metric_id"] == b.reading["metric_id"]
                    and a.snapshot_id == b.snapshot_id):
                side = a.reading.get("side") or b.reading.get("side")
                return SignEvaluation([a.reading, b.reading], True, side,
                                      usable[-1].reading.get("outside"), usable[-1].baseline_ready)
    last = usable[-1]
    return SignEvaluation([r.reading for r in usable[:2]], False, None, last.reading.get("outside"),
                          last.baseline_ready)


# v12 (owner, after the acted phone run): the face and an arm outside on the SAME side in one call confirm each
# other, so neither task is repeated for confirmation (a repeat of the arms was the slowest part of the call).
# A first outside face or arm reading waits for the other task before its own confirming repeat.
CROSS_TASKS = ("smile", "arms")


def cross_partner(task: str) -> Optional[str]:
    """The task whose outside reading on the same side would confirm this one, or None."""
    if task not in CROSS_TASKS:
        return None
    return CROSS_TASKS[1] if task == CROSS_TASKS[0] else CROSS_TASKS[0]


def points_to_weakness(task: str, reading: Dict[str, Any]) -> bool:
    """An outside reading in the direction of weakness: a sinking hand, a lopsided smile (either side), a slower
    sentence. A hand held higher than usual is outside but not a sign."""
    direction = reading_direction(reading)
    if task == "sentence":
        return direction > 0
    return reading_side(task, reading) is not None


def reading_side(task: str, reading: Dict[str, Any]) -> Optional[str]:
    """The side (her left or right) an outside reading points to, or None.

    Arms: the hand the reading names, and only when it sank more than usual (a hand held higher is no weakness).
    Smile: corner_height is below_left - below_right, so above her usual means her LEFT mouth corner sits lower
    than usual, below her usual her RIGHT corner."""
    direction = reading_direction(reading)
    if direction == 0:
        return None
    if task == "arms":
        return reading.get("side") if direction > 0 else None
    if task == "smile":
        return "left" if direction > 0 else "right"
    return None


def cross_confirmation(records_by_task: Dict[str, Sequence[FinalRecord]]
                       ) -> Optional[tuple]:
    """(side, {task: reading}) when a usable face reading and a usable arm reading, each outside her ready usual,
    point to the same side; otherwise None."""
    sides: Dict[str, List[tuple]] = {}
    for task in CROSS_TASKS:
        usable = [r for r in records_by_task.get(task, ()) if r.usable and r.reading is not None and r.baseline_ready]
        sides[task] = [(reading_side(task, r.reading), r.reading) for r in usable]
    for side_a, reading_a in sides[CROSS_TASKS[0]]:
        for side_b, reading_b in sides[CROSS_TASKS[1]]:
            if side_a is not None and side_a == side_b:
                return side_a, {CROSS_TASKS[0]: reading_a, CROSS_TASKS[1]: reading_b}
    return None


def evaluate_all(records_by_task: Dict[str, Sequence[FinalRecord]],
                 one_reading: Optional[bool] = None) -> Dict[str, SignEvaluation]:
    """evaluate_sign per task, then the cross-task rule: a same-side face and arm pair confirms both signs.

    With `one_reading` (default limits.CONFIRM_FROM_ONE_READING) one usable outside reading that points to weakness
    confirms its sign, and an outside reading that does not (a hand held higher) settles the task as not a sign."""
    if one_reading is None:
        one_reading = limits.CONFIRM_FROM_ONE_READING
    evals = {task: evaluate_sign(records) for task, records in records_by_task.items()}
    if one_reading:
        for task, records in records_by_task.items():
            e = evals[task]
            if e.confirmed:
                continue
            usable = [r for r in records if r.usable and r.reading is not None and r.baseline_ready]
            sign = next((r.reading for r in usable if points_to_weakness(task, r.reading)), None)
            if sign is not None:
                evals[task] = SignEvaluation([sign], True, reading_side(task, sign), e.latest_outside,
                                             e.baseline_ready)
            elif e.latest_outside:
                evals[task] = SignEvaluation(e.attempts, False, None, False, e.baseline_ready)
    pair = cross_confirmation(records_by_task)
    if pair is not None:
        side, readings = pair
        for task, reading in readings.items():
            e = evals[task]
            if not e.confirmed:
                evals[task] = SignEvaluation([reading], True, side, e.latest_outside, e.baseline_ready)
    return evals


def session_status(evals: Dict[str, SignEvaluation], required_tasks: Sequence[str], concern: bool = False,
                   floor_on: bool = True) -> str:
    """confirmed change > concern_raised > incomplete (a required task unfinished) > learning (no baseline) > as_usual.

    A required task whose latest usable reading is outside but not confirmed is also unfinished: the repeat
    that would confirm or settle it has not happened.
    `floor_on=False` (experiments only): a confirmed sign does not set change_noticed.
    """
    if floor_on and any(e.confirmed for e in evals.values()):
        return "change_noticed"
    if concern:
        return "concern_raised"
    for task in required_tasks:
        e = evals.get(task)
        if e is None or not e.attempts:
            return "incomplete"
    for task in required_tasks:
        if evals[task].baseline_ready is not True:
            return "learning"
    for task in required_tasks:
        if evals[task].latest_outside is not False:
            return "incomplete"
    return "as_usual"


# ---- baseline eligibility -----------------------------------------------------------------------

def baseline_values_to_store(label: str, records: Sequence[FinalRecord], confirmed_any: bool,
                             concern: bool = False) -> Dict[str, float]:
    """metric_id -> value that may enter the person's baseline after this call.

    Nothing for an acted session, one with a confirmed change, one where the call raised a concern
    (status concern_raised), or one in which ANY usable final reading was outside her usual (confirmed or
    not): such a call is not a normal one. Interrupted attempts and anything without a usable final reading
    never count. The first usable reading per metric is used. Labels live/normal keep entering; acted is excluded.
    """
    if label == "acted" or confirmed_any or concern:
        return {}
    if any(r.usable and r.reading is not None and r.reading.get("outside") is True for r in records):
        return {}
    out: Dict[str, float] = {}
    for r in records:
        if r.interrupted or not r.usable or r.reading is None:
            continue
        out.setdefault(r.reading["metric_id"], float(r.reading["value"]))
    return out
