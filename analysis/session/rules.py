"""Pure decision rules of the call session. No I/O, no clock reads: callers pass the times in.

- decide_interrupt: should the active attempt stop, and why (plan section 5).
- attempt_budget: three attempts per task, at most two usable.
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
    """None when another attempt of this task is allowed, else the reason (a slug)."""
    if len(records) >= limits.MAX_ATTEMPTS_PER_TASK:
        return "attempt_budget_exhausted"
    if sum(1 for r in records if r.usable) >= limits.MAX_USABLE_PER_TASK:
        return "usable_readings_complete"
    return None


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


def session_status(evals: Dict[str, SignEvaluation], required_tasks: Sequence[str]) -> str:
    """confirmed change > incomplete (a required task unfinished) > learning (no baseline) > as_usual.

    A required task whose latest usable reading is outside but not confirmed is also unfinished: the repeat
    that would confirm or settle it has not happened.
    """
    if any(e.confirmed for e in evals.values()):
        return "change_noticed"
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

def baseline_values_to_store(label: str, records: Sequence[FinalRecord], confirmed_any: bool) -> Dict[str, float]:
    """metric_id -> value that may enter the person's baseline after this call.

    Nothing for an acted session or one with a confirmed change. Interrupted attempts and anything without a
    usable final reading never count. The first usable reading per metric is used. Labels live/normal keep
    entering; acted is excluded.
    """
    if label == "acted" or confirmed_any:
        return {}
    out: Dict[str, float] = {}
    for r in records:
        if r.interrupted or not r.usable or r.reading is None:
            continue
        out.setdefault(r.reading["metric_id"], float(r.reading["value"]))
    return out
