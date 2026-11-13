"""The interface of a streamed task module (backend/ARCHITECTURE_PLAN.md section 6) and shared helpers.

    definition() -> dict      input settings, instruction line ids, metric version
    async run(context, samples, emit_observation) -> TaskResult

`samples` is an async iterator of MediaSample-shaped dicts (sequence, captured_ms, format, and data_base64
or data). `emit_observation` is a synchronous callable taking a TaskObservation; it never calls Claude and
does not wait for a decision. Pure Python; no cv2 or mediapipe imports here.
"""
from __future__ import annotations

import re
from typing import Any, AsyncIterator, Callable, Dict, Optional, Protocol

from analysis.session import limits
from analysis.session.models import BaselineInfo, Quality, TaskContext, TaskResult

NOT_IMPLEMENTED = "not_implemented_in_v5"

# Capture settings equal to x-task-definitions in contract/mvp.openapi.yaml.
CAPTURE_SETTINGS: Dict[str, Dict[str, Any]] = {
    "smile": {"media_kind": "video", "duration_ms": 6000, "prepare_ms": 0, "stop_on_silence_ms": None,
              "format": "jpeg", "sample_period_ms": 200},
    "arms": {"media_kind": "video", "duration_ms": 15000, "prepare_ms": 2000, "stop_on_silence_ms": None,
             "format": "jpeg", "sample_period_ms": 200},
    "sentence": {"media_kind": "audio", "duration_ms": 12000, "prepare_ms": 0, "stop_on_silence_ms": 1500,
                 "format": "pcm_s16le_16000_mono", "sample_period_ms": 250},
}


class StreamTask(Protocol):
    name: str

    def definition(self) -> Dict[str, Any]:
        ...

    async def run(self, context: TaskContext, samples: AsyncIterator[Dict[str, Any]],
                  emit_observation: Callable[[Any], Any]) -> TaskResult:
        ...


def build_definition(task: str, metric_id: Optional[str], metric_unit: Optional[str]) -> Dict[str, Any]:
    return {
        "task": task,
        "sign": limits.TASK_SIGN[task],
        "instruction_line": "task_%s" % task,
        "repeat_line": "repeat_%s" % task,
        "capture": dict(CAPTURE_SETTINGS[task]),
        "required_resource": limits.TASK_RESOURCE[task],
        "observation_period_ms": int(limits.OBSERVATION_PERIOD_S * 1000),
        "metric_id": metric_id,  # versioned; None while the task has no metric
        "metric_unit": metric_unit,
    }


def slug(text: str) -> str:
    """A reason slug matching ^[a-z][a-z0-9_]*$ from free text."""
    s = re.sub(r"[^a-z0-9]+", "_", str(text).lower()).strip("_")
    if not s or not s[0].isalpha():
        s = "reason_" + s if s else "unknown"
    return s


def baseline_info(baseline: Dict[str, Any]) -> BaselineInfo:
    """Baseline dict (make_baseline output + snapshot_id) -> the schema's Baseline."""
    return BaselineInfo(str(baseline.get("snapshot_id") or "baseline_none"), int(baseline.get("sessions") or 0),
                        int(baseline.get("needed") or limits.BASELINE_NEEDED_DEFAULT), bool(baseline.get("ready")))


def stub_result(task: str, context: TaskContext) -> TaskResult:
    """A schema-valid failed result for a task module that is not implemented in this version."""
    return TaskResult(task, context.attempt_id, "failed", Quality("unusable", [NOT_IMPLEMENTED]),
                      "not_observed", baseline_info(context.baseline), None, None)
