"""Every tunable of the call session in one place (backend/ARCHITECTURE_PLAN.md sections 5-6).

All values are engineering starting values from the plan, not measured. Seconds unless the name says _MS.
"""
from __future__ import annotations

OBSERVATION_PERIOD_S = 1.0  # a task reports about once per second
FRAMING_UNUSABLE_S = 2.0  # framing_unusable continuously this long interrupts
FIRST_SAMPLE_S = 2.0  # no first sample this long after capture started interrupts (no_samples)
SAMPLE_GAP_S = 2.0  # a gap this long between samples interrupts (no_samples)
NO_ACK_S = 5.0  # no capture.started and no sample this long after the command interrupts (no_samples)
ANALYSIS_STALLED_S = 3.0  # samples arrive but no observation/result this long interrupts (analysis_stalled)
CANCEL_ACK_S = 1.0  # client must acknowledge cancellation within this, else the attempt is invalidated locally
FINALIZE_EXTRA_S = 20.0  # finalize deadline = task window + this
RECOVERY_WAIT_S = 10.0  # one bounded wait for a lost resource to come back, per interruption
PREPARE_PLAYBACK_S = 30.0  # instruction playback / preparation limit
CALL_MAX_S = 180.0  # whole call
WORKER_STOP_WAIT_S = 1.0  # how long a stopped analyzer may take to end before its slot counts as busy

MAX_ATTEMPTS_PER_TASK = 3  # two usable + one technical retry, three in total
MAX_USABLE_PER_TASK = 2  # a third usable reading is never needed (two confirm or settle it)

WATCHDOG_TICK_S = 0.1  # how often the session re-checks its watchdogs (through the clock)
SAMPLE_QUEUE_MAX = 64  # bounded sample queue per attempt; a full queue drops the new sample and logs it

BASELINE_NEEDED_DEFAULT = 5  # used for the summary when no baseline is known

TASK_WINDOW_S = {"smile": 5.0, "arms": 12.0, "sentence": 12.0}
TASK_SIGN = {"smile": "face", "arms": "arm", "sentence": "speech"}
TASK_RESOURCE = {"smile": "camera", "arms": "camera", "sentence": "microphone"}
TASKS = ("smile", "arms", "sentence")
