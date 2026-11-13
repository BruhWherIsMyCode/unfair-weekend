"""Stand-in task modules for exercising the call service without mediapipe or opencv.

They read the real samples, check that frames/chunks arrive, emit neutral observations about once per second
and then return a `failed` result with reason `no_analyzer_in_this_build`. They never produce a reading, so a
call that uses them always ends as incomplete. Select them with CALL_RUNNER=fake.
"""

from typing import Any

from .media import jpeg_size

# Same cue lines as Agent 3's real modules (runner.CUES), so the phone's timer follows the same parts.
CUES = {"smile": [{"at_ms": 1500, "line_id": "smile_now"}], "arms": [{"at_ms": 7000, "line_id": "swap_hands"}]}

WINDOW_MS = {"smile": 6000, "arms": 20000, "sentence": 12000}
SIGN = {"smile": "face", "arms": "arm", "sentence": "speech"}
RESOURCE = {"smile": "camera", "arms": "camera", "sentence": "microphone"}
CAPTURE = {
    "smile": {"media_kind": "video", "duration_ms": 6000, "prepare_ms": 0, "stop_on_silence_ms": None,
              "format": "jpeg", "sample_period_ms": 200},
    "arms": {"media_kind": "video", "duration_ms": 20000, "prepare_ms": 2000, "stop_on_silence_ms": None,
             "format": "jpeg", "sample_period_ms": 200},
    "sentence": {"media_kind": "audio", "duration_ms": 12000, "prepare_ms": 0, "stop_on_silence_ms": 1500,
                 "format": "pcm_s16le_16000_mono", "sample_period_ms": 250},
}
REASON = "no_analyzer_in_this_build"


class InputOnlyTask:
    def __init__(self, task: str) -> None:
        self.name = task

    def definition(self) -> dict[str, Any]:
        return {
            "task": self.name,
            "sign": SIGN[self.name],
            "instruction_line": f"task_{self.name}",
            "repeat_line": f"repeat_{self.name}",
            "capture": dict(CAPTURE[self.name]),
            "required_resource": RESOURCE[self.name],
            "observation_period_ms": 1000,
            "metric_id": "input_check_v0",
            "metric_unit": "none",
            "cues": [dict(cue) for cue in CUES.get(self.name, [])],
        }

    async def run(self, context: Any, samples: Any, emit_observation: Any) -> Any:
        from analysis.session.models import Quality, TaskObservation, TaskResult
        from analysis.tasks.stream_base import baseline_info

        sequence = 0
        next_ms = 0
        elapsed = 0

        def observe(ms: int, status: str, reasons: list[str], phase: str) -> None:
            nonlocal sequence
            emit_observation(
                TaskObservation(
                    f"obs_{context.attempt_id}_{sequence}", context.call_id, context.attempt_id, self.name,
                    sequence, max(0, min(ms, 20000)), phase, Quality(status, reasons), [], "none",
                )
            )
            sequence += 1

        async for sample in samples:
            if context.cancel_event.is_set():
                break
            ms = int(sample["captured_ms"])
            elapsed = ms
            if ms >= next_ms:
                data = sample.get("data") or b""
                ok = jpeg_size(data) is not None if sample["format"] == "jpeg" else len(data) > 0
                observe(ms, "usable" if ok else "unusable", [] if ok else ["unreadable_input"], "capturing")
                next_ms = (ms // 1000 + 1) * 1000
            if ms >= WINDOW_MS[self.name]:
                break
        observe(elapsed, "pending", [], "finalizing")
        return TaskResult(self.name, context.attempt_id, "failed", Quality("unusable", [REASON]), "not_observed",
                          baseline_info(context.baseline), None, None)


def fake_modules() -> dict[str, Any]:
    return {task: InputOnlyTask(task) for task in WINDOW_MS}
