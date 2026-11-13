"""Typed payloads of the shared contract draft 0.2 (contract/mvp.openapi.yaml).

Each dataclass has `validate()` (raises ValueError, enforcing the schema's required fields, enums and
if/then rules) and `to_dict()` (validates, then returns a schema-exact dict). `from_dict()` exists where the
session receives the shape from another module. Pure Python.
"""
from __future__ import annotations

import asyncio
import math
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
REASON_RE = re.compile(r"^[a-z][a-z0-9_]*$")

TASKS = ("smile", "arms", "sentence")
OUTCOMES = ("completed", "could_not_perform", "failed", "cancelled", "interrupted")
PERFORMANCES = ("observed", "not_observed", "uncertain")
INTERRUPT_CODES = ("camera_unavailable", "microphone_unavailable", "framing_unusable", "no_samples",
                   "analysis_stalled", "task_unperformable", "capture_failed")
SIGNALS = ("none", "framing_unusable", "task_unperformable")
PHASES = ("capturing", "finalizing")
RESOURCES = ("camera", "microphone")
RESOURCE_STATES = ("available", "unavailable")
RESOURCE_REASONS = ("permission_denied", "track_ended", "device_missing", "unknown")
SIGNS = ("face", "arm", "speech")
LABELS = ("live", "normal", "acted")
STATUSES = ("as_usual", "change_noticed", "concern_raised", "learning", "incomplete")  # concern_raised: v7
CONCERN_REASONS = ("said_unwell", "asked_for_help", "confused_answers", "reading_far_outside", "other")


def _req(cond: bool, msg: str) -> None:
    if not cond:
        raise ValueError(msg)


def _is_num(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def _is_int(x: Any) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def _check_id(value: Any, name: str) -> None:
    _req(isinstance(value, str) and ID_RE.match(value) is not None, "%s is not a valid Id: %r" % (name, value))


def _check_reasons(reasons: Any) -> None:
    _req(isinstance(reasons, list), "reasons must be a list")
    for r in reasons:
        _req(isinstance(r, str) and REASON_RE.match(r) is not None, "bad reason slug: %r" % (r,))
    _req(len(set(reasons)) == len(reasons), "reasons must be unique")


def _only_keys(d: Dict[str, Any], keys, name: str) -> None:
    _req(isinstance(d, dict), "%s must be an object" % name)
    extra = set(d) - set(keys)
    missing = set(keys) - set(d)
    _req(not extra, "%s has unknown fields: %s" % (name, sorted(extra)))
    _req(not missing, "%s is missing fields: %s" % (name, sorted(missing)))


# ---- small shared pieces -------------------------------------------------------------------

@dataclass
class Quality:
    """Quality (usable/unusable) and ObservationQuality (also pending)."""

    status: str
    reasons: List[str] = field(default_factory=list)

    def validate(self, observation: bool = False) -> None:
        allowed = ("pending", "usable", "unusable") if observation else ("usable", "unusable")
        _req(self.status in allowed, "quality.status %r not in %s" % (self.status, allowed))
        _check_reasons(self.reasons)
        if self.status == "unusable":
            _req(len(self.reasons) >= 1, "unusable quality needs at least one reason")

    def to_dict(self, observation: bool = False) -> Dict[str, Any]:
        self.validate(observation)
        return {"status": self.status, "reasons": list(self.reasons)}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Quality":
        _only_keys(d, ("status", "reasons"), "quality")
        return cls(d["status"], list(d["reasons"]))


@dataclass
class Metric:
    """ProvisionalMetric."""

    metric_id: str
    unit: str
    value: float

    def to_dict(self) -> Dict[str, Any]:
        _req(isinstance(self.metric_id, str) and self.metric_id != "", "metric_id empty")
        _req(isinstance(self.unit, str) and self.unit != "", "unit empty")
        _req(_is_num(self.value), "metric value must be a finite number")
        return {"metric_id": self.metric_id, "unit": self.unit, "value": self.value}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Metric":
        _only_keys(d, ("metric_id", "unit", "value"), "metric")
        return cls(d["metric_id"], d["unit"], d["value"])


@dataclass
class BaselineInfo:
    snapshot_id: str
    sessions: int
    needed: int
    ready: bool

    def to_dict(self) -> Dict[str, Any]:
        _check_id(self.snapshot_id, "baseline.snapshot_id")
        _req(_is_int(self.sessions) and self.sessions >= 0, "baseline.sessions must be an integer >= 0")
        _req(_is_int(self.needed) and self.needed >= 1, "baseline.needed must be an integer >= 1")
        _req(isinstance(self.ready, bool), "baseline.ready must be a boolean")
        return {"snapshot_id": self.snapshot_id, "sessions": self.sessions, "needed": self.needed,
                "ready": self.ready}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "BaselineInfo":
        _only_keys(d, ("snapshot_id", "sessions", "needed", "ready"), "baseline")
        return cls(d["snapshot_id"], d["sessions"], d["needed"], d["ready"])


@dataclass
class Reading:
    metric_id: str
    unit: str
    value: float
    usual_low: Optional[float]
    usual_high: Optional[float]
    outside: Optional[bool]
    side: Optional[str]

    def validate(self) -> None:
        _req(isinstance(self.metric_id, str) and self.metric_id != "", "reading.metric_id empty")
        _req(isinstance(self.unit, str) and self.unit != "", "reading.unit empty")
        _req(_is_num(self.value), "reading.value must be a finite number")
        for name in ("usual_low", "usual_high"):
            v = getattr(self, name)
            _req(v is None or _is_num(v), "reading.%s must be a number or null" % name)
        _req(self.outside is None or isinstance(self.outside, bool), "reading.outside must be a boolean or null")
        _req(self.side in (None, "left", "right"), "reading.side must be left, right or null")

    def to_dict(self) -> Dict[str, Any]:
        self.validate()
        return {"metric_id": self.metric_id, "unit": self.unit, "value": self.value,
                "usual_low": self.usual_low, "usual_high": self.usual_high,
                "outside": self.outside, "side": self.side}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Reading":
        _only_keys(d, ("metric_id", "unit", "value", "usual_low", "usual_high", "outside", "side"), "reading")
        return cls(d["metric_id"], d["unit"], d["value"], d["usual_low"], d["usual_high"], d["outside"],
                   d["side"])


# ---- events --------------------------------------------------------------------------------

@dataclass
class TaskObservation:
    event_id: str
    call_id: str
    attempt_id: str
    task: str
    sequence: int
    elapsed_ms: int
    phase: str
    quality: Quality
    metrics: List[Metric] = field(default_factory=list)
    signal: str = "none"

    def validate(self) -> None:
        _check_id(self.event_id, "event_id")
        _check_id(self.call_id, "call_id")
        _check_id(self.attempt_id, "attempt_id")
        _req(self.task in TASKS, "task %r not in %s" % (self.task, TASKS))
        _req(_is_int(self.sequence) and self.sequence >= 0, "sequence must be an integer >= 0")
        _req(_is_int(self.elapsed_ms) and 0 <= self.elapsed_ms <= 20000, "elapsed_ms must be 0..20000")
        _req(self.phase in PHASES, "phase %r not in %s" % (self.phase, PHASES))
        self.quality.validate(observation=True)
        for m in self.metrics:
            m.to_dict()
        _req(self.signal in SIGNALS, "signal %r not in %s" % (self.signal, SIGNALS))
        if self.signal == "framing_unusable":
            _req(self.quality.status == "unusable", "framing_unusable requires unusable quality")

    def to_dict(self) -> Dict[str, Any]:
        self.validate()
        return {"event_id": self.event_id, "type": "task.observation", "call_id": self.call_id,
                "attempt_id": self.attempt_id, "task": self.task, "sequence": self.sequence,
                "elapsed_ms": self.elapsed_ms, "phase": self.phase,
                "quality": self.quality.to_dict(observation=True),
                "metrics": [m.to_dict() for m in self.metrics], "signal": self.signal}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "TaskObservation":
        _only_keys(d, ("event_id", "type", "call_id", "attempt_id", "task", "sequence", "elapsed_ms", "phase",
                       "quality", "metrics", "signal"), "observation")
        _req(d["type"] == "task.observation", "type must be task.observation")
        obs = cls(d["event_id"], d["call_id"], d["attempt_id"], d["task"], d["sequence"], d["elapsed_ms"],
                  d["phase"], Quality.from_dict(d["quality"]), [Metric.from_dict(m) for m in d["metrics"]],
                  d["signal"])
        obs.validate()
        return obs


@dataclass
class TaskInterrupt:
    event_id: str
    call_id: str
    attempt_id: str
    code: str
    source_event_id: Optional[str]
    recoverable: bool

    def validate(self) -> None:
        _check_id(self.event_id, "event_id")
        _check_id(self.call_id, "call_id")
        _check_id(self.attempt_id, "attempt_id")
        _req(self.code in INTERRUPT_CODES, "interrupt code %r not in %s" % (self.code, INTERRUPT_CODES))
        _req(self.source_event_id is None or ID_RE.match(self.source_event_id) is not None,
             "source_event_id must be an Id or null")
        _req(isinstance(self.recoverable, bool), "recoverable must be a boolean")

    def to_dict(self) -> Dict[str, Any]:
        self.validate()
        return {"event_id": self.event_id, "type": "task.interrupted", "call_id": self.call_id,
                "attempt_id": self.attempt_id, "code": self.code, "source_event_id": self.source_event_id,
                "recoverable": self.recoverable}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "TaskInterrupt":
        _only_keys(d, ("event_id", "type", "call_id", "attempt_id", "code", "source_event_id", "recoverable"),
                   "interruption")
        _req(d["type"] == "task.interrupted", "type must be task.interrupted")
        i = cls(d["event_id"], d["call_id"], d["attempt_id"], d["code"], d["source_event_id"], d["recoverable"])
        i.validate()
        return i


@dataclass
class ResourceEvent:
    """ClientResourceEvent (type resource.changed)."""

    event_id: str
    command_id: Optional[str]
    resource: str
    state: str
    sequence: int
    reason: Optional[str]

    def validate(self) -> None:
        _check_id(self.event_id, "event_id")
        _req(self.command_id is None or ID_RE.match(self.command_id) is not None, "command_id must be an Id or null")
        _req(self.resource in RESOURCES, "resource %r not in %s" % (self.resource, RESOURCES))
        _req(self.state in RESOURCE_STATES, "state %r not in %s" % (self.state, RESOURCE_STATES))
        _req(_is_int(self.sequence) and self.sequence >= 0, "sequence must be an integer >= 0")
        if self.state == "available":
            _req(self.reason is None, "an available resource has a null reason")
        else:
            _req(self.reason in RESOURCE_REASONS, "an unavailable resource needs a reason from %s" % (RESOURCE_REASONS,))

    def to_dict(self) -> Dict[str, Any]:
        self.validate()
        return {"event_id": self.event_id, "type": "resource.changed", "command_id": self.command_id,
                "resource": self.resource, "state": self.state, "sequence": self.sequence, "reason": self.reason}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ResourceEvent":
        _only_keys(d, ("event_id", "type", "command_id", "resource", "state", "sequence", "reason"),
                   "resource event")
        _req(d["type"] == "resource.changed", "type must be resource.changed")
        e = cls(d["event_id"], d["command_id"], d["resource"], d["state"], d["sequence"], d["reason"])
        e.validate()
        return e


# ---- results -------------------------------------------------------------------------------

@dataclass
class TaskResult:
    task: str
    attempt_id: str
    outcome: str
    quality: Quality
    performance: str
    baseline: BaselineInfo
    reading: Optional[Reading] = None
    interruption: Optional[TaskInterrupt] = None

    def validate(self) -> None:
        _req(self.task in TASKS, "task %r not in %s" % (self.task, TASKS))
        _check_id(self.attempt_id, "attempt_id")
        _req(self.outcome in OUTCOMES, "outcome %r not in %s" % (self.outcome, OUTCOMES))
        self.quality.validate()
        _req(self.performance in PERFORMANCES, "performance %r not in %s" % (self.performance, PERFORMANCES))
        self.baseline.to_dict()
        # A reading exists only for a completed, usable, observed attempt.
        has_reading_right = (self.outcome == "completed" and self.quality.status == "usable"
                             and self.performance == "observed")
        if has_reading_right:
            _req(self.reading is not None, "completed + usable + observed requires a reading")
        else:
            _req(self.reading is None, "a reading needs completed + usable + observed")
        if self.reading is not None:
            self.reading.validate()
            if self.baseline.ready:
                _req(_is_num(self.reading.usual_low) and _is_num(self.reading.usual_high)
                     and isinstance(self.reading.outside, bool),
                     "a ready baseline needs numeric usual range and boolean outside")
            else:
                _req(self.reading.usual_low is None and self.reading.usual_high is None
                     and self.reading.outside is None,
                     "a baseline that is not ready gives null comparison fields")
        if self.outcome == "interrupted":
            _req(self.interruption is not None, "interrupted requires an interruption")
            _req(self.quality.status == "unusable", "interrupted requires unusable quality")
            self.interruption.validate()
            _req(self.interruption.attempt_id == self.attempt_id, "interruption belongs to another attempt")
        else:
            _req(self.interruption is None, "only interrupted results carry an interruption")

    def to_dict(self) -> Dict[str, Any]:
        self.validate()
        return {"task": self.task, "attempt_id": self.attempt_id, "outcome": self.outcome,
                "quality": self.quality.to_dict(), "performance": self.performance,
                "baseline": self.baseline.to_dict(),
                "reading": self.reading.to_dict() if self.reading is not None else None,
                "interruption": self.interruption.to_dict() if self.interruption is not None else None}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "TaskResult":
        _only_keys(d, ("task", "attempt_id", "outcome", "quality", "performance", "baseline", "reading",
                       "interruption"), "task result")
        r = cls(d["task"], d["attempt_id"], d["outcome"], Quality.from_dict(d["quality"]), d["performance"],
                BaselineInfo.from_dict(d["baseline"]),
                Reading.from_dict(d["reading"]) if d["reading"] is not None else None,
                TaskInterrupt.from_dict(d["interruption"]) if d["interruption"] is not None else None)
        r.validate()
        return r


@dataclass
class SummaryAttempt:
    value: float
    usual_low: Optional[float]
    usual_high: Optional[float]
    outside: Optional[bool]

    def to_dict(self) -> Dict[str, Any]:
        _req(_is_num(self.value), "attempt value must be a finite number")
        for name in ("usual_low", "usual_high"):
            v = getattr(self, name)
            _req(v is None or _is_num(v), "attempt %s must be a number or null" % name)
        _req(self.outside is None or isinstance(self.outside, bool), "attempt outside must be a boolean or null")
        return {"value": self.value, "usual_low": self.usual_low, "usual_high": self.usual_high,
                "outside": self.outside}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "SummaryAttempt":
        _only_keys(d, ("value", "usual_low", "usual_high", "outside"), "summary attempt")
        return cls(d["value"], d["usual_low"], d["usual_high"], d["outside"])


@dataclass
class SummarySign:
    sign: str
    side: Optional[str]
    attempts: List[SummaryAttempt]
    confirmed: bool

    def to_dict(self) -> Dict[str, Any]:
        _req(self.sign in SIGNS, "sign %r not in %s" % (self.sign, SIGNS))
        _req(self.side in (None, "left", "right"), "side must be left, right or null")
        _req(len(self.attempts) <= 2, "at most 2 attempts per sign")
        _req(isinstance(self.confirmed, bool), "confirmed must be a boolean")
        attempts = [a.to_dict() for a in self.attempts]
        if self.confirmed:
            # v12: one attempt when the face and an arm confirmed each other on the same side (rules.evaluate_all)
            _req(len(attempts) >= 1, "a confirmed sign needs an attempt")
            for a in attempts:
                _req(a["outside"] is True and _is_num(a["usual_low"]) and _is_num(a["usual_high"]),
                     "a confirmed sign needs outside attempts with a numeric usual range")
        return {"sign": self.sign, "side": self.side, "attempts": attempts, "confirmed": self.confirmed}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "SummarySign":
        _only_keys(d, ("sign", "side", "attempts", "confirmed"), "summary sign")
        return cls(d["sign"], d["side"], [SummaryAttempt.from_dict(a) for a in d["attempts"]], d["confirmed"])


@dataclass
class SessionResult:
    person_id: str
    label: str
    duration_s: int
    answers: Dict[str, str]
    signs: List[SummarySign]
    status: str
    baseline_sessions: int
    baseline_needed: int
    clip_id: Optional[str] = None
    concern: Optional[Dict[str, str]] = None  # v7: {"raised_by": "agent", "reason", "note"}; the key is absent when None

    def _concern_dict(self) -> Optional[Dict[str, str]]:
        if self.concern is None:
            return None
        _only_keys(self.concern, ("raised_by", "reason", "note"), "concern")
        _req(self.concern["raised_by"] == "agent", "concern.raised_by must be agent")
        _req(self.concern["reason"] in CONCERN_REASONS, "concern.reason not in %s" % (CONCERN_REASONS,))
        _req(isinstance(self.concern["note"], str) and len(self.concern["note"]) <= 200, "concern.note too long")
        return dict(self.concern)

    def to_dict(self) -> Dict[str, Any]:
        _check_id(self.person_id, "person_id")
        _req(self.label in LABELS, "label %r not in %s" % (self.label, LABELS))
        _req(_is_int(self.duration_s) and self.duration_s >= 0, "duration_s must be an integer >= 0")
        _only_keys(self.answers, ("medicine", "feeling", "unusual"), "answers")
        _req(self.answers["medicine"] in ("yes", "no", "unclear"), "answers.medicine must be yes, no or unclear")
        for k in ("feeling", "unusual"):
            _req(isinstance(self.answers[k], str) and len(self.answers[k]) <= 2000, "answers.%s too long" % k)
        _req(len(self.signs) <= 3, "at most 3 signs")
        signs = [s.to_dict() for s in self.signs]
        # v12: a sign confirmed from ONE attempt is only the face and an arm confirming each other on one side
        single = [s for s in signs if s["confirmed"] and len(s["attempts"]) == 1]
        if single:
            _req(len(single) == 2 and {s["sign"] for s in single} == {"face", "arm"}
                 and single[0]["side"] is not None and single[0]["side"] == single[1]["side"],
                 "a confirmation from one attempt needs the face and an arm outside on the same side")
        _req(self.status in STATUSES, "status %r not in %s" % (self.status, STATUSES))
        if self.status == "change_noticed":
            _req(any(s["confirmed"] for s in signs), "change_noticed requires a confirmed sign")
        _req(_is_int(self.baseline_sessions) and self.baseline_sessions >= 0, "baseline_sessions must be >= 0")
        _req(_is_int(self.baseline_needed) and self.baseline_needed >= 1, "baseline_needed must be >= 1")
        _req(self.clip_id is None, "clip_id is null in this draft")
        concern = self._concern_dict()
        _req((self.status == "concern_raised") <= (concern is not None), "concern_raised needs a concern")
        out = {"person_id": self.person_id, "label": self.label, "duration_s": self.duration_s,
               "answers": dict(self.answers), "signs": signs, "status": self.status,
               "baseline_sessions": self.baseline_sessions, "baseline_needed": self.baseline_needed,
               "clip_id": None}
        if concern is not None:
            out["concern"] = concern
        return out

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "SessionResult":
        keys = ["person_id", "label", "duration_s", "answers", "signs", "status", "baseline_sessions",
                "baseline_needed", "clip_id"]
        if "concern" in d:
            keys.append("concern")
        _only_keys(d, keys, "session result")
        return cls(d["person_id"], d["label"], d["duration_s"], dict(d["answers"]),
                   [SummarySign.from_dict(s) for s in d["signs"]], d["status"], d["baseline_sessions"],
                   d["baseline_needed"], d["clip_id"], d.get("concern"))


# ---- what a task module receives ----------------------------------------------------------------

@dataclass
class TaskContext:
    """Passed to task_module.run: identity, the frozen baseline and the cancellation signal.

    `baseline` is a dict from analysis.baseline.make_baseline plus a "snapshot_id" key.
    """

    call_id: str
    attempt_id: str
    task: str
    baseline: Dict[str, Any]
    cancel_event: "asyncio.Event" = field(default_factory=asyncio.Event)
