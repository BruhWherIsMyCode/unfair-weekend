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
# v11 (owner, after real calls): an attempt that ends without a usable reading (unusable, could_not_perform,
# interrupted, failed) is repeated at most ONCE; the second not-measured attempt of a task skips the task.
MAX_NOT_MEASURED_PER_TASK = 2
# v12b (owner, for a short demo call): ONE usable reading outside her usual that points to weakness confirms the
# change and the family is told; no task is repeated to confirm. Only a try that measured nothing keeps its one
# technical repeat. False restores the two-reading rule (the same task twice, or the face and an arm on one side).
CONFIRM_FROM_ONE_READING = True

WATCHDOG_TICK_S = 0.1  # how often the session re-checks its watchdogs (through the clock)
SAMPLE_QUEUE_MAX = 64  # bounded sample queue per attempt; a full queue drops the new sample and logs it

BASELINE_NEEDED_DEFAULT = 5  # used for the summary when no baseline is known

# ---- v6: the one-hand arms task ----------------------------------------------------------------
# One 12 s capture window, two halves. (phase, start_ms, end_ms, expected side or None). A sample belongs to a
# phase when start_ms <= captured_ms < end_ms. Hold phases are measured; settle and swap frames are ignored.
# Side words are HER left and right. First guesses, editable in this one place.
# v11 (owner, after the real PC run): 15 s window (the contract maximum), longer holds; the right hold starts while
# the swap line is still playing, so its raise grace covers the rest of the line.
# v12 (owner, after the acted phone run): moving the phone from one hand to the other took longer than the 2 s swap,
# so the window is 20 s (CAPTURE_MAX_MS, the raised contract maximum) and the swap 4.5 s. The swap line still plays
# when the swap starts; it ends about when the right hold starts, and the raise grace covers the raise.
CAPTURE_MAX_MS = 20000  # longest capture window and largest captured_ms/elapsed_ms (also contract, backend, iOS)
ARMS_PHASES = (
    ("settle", 0, 1000, None),
    ("hold_left", 1000, 7000, "left"),
    ("swap", 7000, 11500, None),
    ("hold_right", 11500, 20000, "right"),
)
# Time she gets at the start of each hold phase to raise the hand: until her hand is first seen in that phase, and
# for at most this long, a frame without the hand is "pending", not framing_unusable (the 2 s framing rule
# otherwise interrupted every arms attempt ~3 s in, while she was still raising the hand). The hold is then
# measured from the first frame her hand was seen.
ARMS_RAISE_GRACE_MS = 3000
ARMS_METRIC_ID = "hand_sink_v1"
ARMS_BASELINE_KEYS = {"left": "arms.hand_left.hand_sink", "right": "arms.hand_right.hand_sink"}

# ---- v6: the call driver (Claude tool loop and fixed-order fallback) ----------------------------
LISTEN_TIMEOUT_S = 8.0  # how long adapter.listen waits for her answer
CLAUDE_TIMEOUT_S = 6.0  # one model request
CLAUDE_MAX_TURNS = 30  # more requests than this switches to the fixed-order driver
CLAUDE_MAX_TOKENS = 300
FINISH_GUARD_S = 30.0  # finish_call is allowed without further checks when less than this remains of CALL_MAX_S
MAX_ASKS_PER_QUESTION = 2
ANSWER_MAX_CHARS = 200  # record_answer value limit
TRANSCRIPT_TO_CLAUDE_CHARS = 500
QUESTIONS = ("q_medicine", "q_feeling", "q_unusual")
ANSWER_KEY = {"q_medicine": "medicine", "q_feeling": "feeling", "q_unusual": "unusual"}
SAYABLE_LINES = ("greet", "setup_phone")
RESOURCES = ("camera", "microphone")

# ---- v7: PC version ------------------------------------------------------------------------------
REQUIRED_TASKS = ("smile", "arms")  # the sentence task has no measuring yet; the speech build makes it required again
SAFETY_FLOOR_DEFAULT = True  # ON: two same-side outside readings are published mid-call whatever Claude does
CLAUDE_SUB_TIMEOUT_S = 15.0  # one request through the claude CLI (slower round trips than the API)
ALERT_REASONS = ("said_unwell", "asked_for_help", "confused_answers", "reading_far_outside", "other")
ALERT_NOTE_MAX_CHARS = 200
ALERT_MAX_PER_CALL = 2

# ---- v8: speech on the PC version ---------------------------------------------------------------
REQUIRED_TASKS_WITH_SPEECH = ("smile", "arms", "sentence")  # chosen per call when the client reports speech
STT_TIMEOUT_S = 15.0  # one ElevenLabs speech-to-text request
TTS_TIMEOUT_S = 15.0  # one ElevenLabs text-to-speech request
STT_TOKEN_WAIT_S = 5.0  # the client waits this long for the server's single-use token
STT_TOKENS_PER_CALL = 10  # the server mints at most this many tokens per call (one in flight at a time)
SPEECH_SILENCE_S = 1.5  # recording stops this long after speech ended
SPEECH_ANSWER_START_S = 5.0  # a spoken answer: nobody starts speaking within this -> no answer
SPEECH_SENTENCE_START_S = 8.0  # the sentence: nobody starts speaking within this -> nothing heard
SPEECH_ANSWER_GRACE_S = 20.0  # the server waits this much longer than the listen window for recording + transcription
SENTENCE_RESULT_GRACE_S = 20.0  # the client gives the sentence this long beyond its capture window to deliver a result
SPEECH_METRIC_ID = "speech_duration_v1"
SPEECH_BASELINE_KEY = "sentence.duration_s"
SENTENCE_MIN_HALF_WIDTH_S = 0.5  # minimum half width of her usual sentence duration (seconds); first guess

# ---- v11: live talk -------------------------------------------------------------------------------
# The smile window has a relaxed phase first, then the cue line `smile_now`, then the smile phase. The relaxed
# reference of the reading is taken from the relax phase (the owner's real calls asked "smile until the timer
# finishes", so she was already smiling when capture started and no rise was ever seen).
SMILE_PHASES = (
    ("relax", 0, 1500),
    ("smile", 1500, 6000),
)
SMILE_CUE_LINE = "smile_now"
SMILE_CUE_MS = 1500
SMILE_MIN_PHASE_FRAMES = 3  # usable frames needed in the relax phase (and for the peak) for the phase-aware rule
SMILE_ALREADY_SMILING = 0.5  # relaxed smile strength at or above this means she was already smiling; first guess

OWN_LINES_MAX = 10  # say_own + ask_followup together, per call
OWN_LINE_MAX_WORDS = 25
OWN_LINE_MAX_CHARS = 200
FOLLOWUPS_PER_QUESTION = 1
OWN_LINE_BANNED_WORDS = ("stroke", "detect", "diagnose", "predict", "suspect", "medical", "safe", "risk", "emergency")
RETRY_HINTS = ("face_not_in_view", "already_smiling", "no_smile_seen", "hand_not_seen", "hand_too_late",
               "camera_lost", "too_dark", "other")

TASK_WINDOW_S = {"smile": 6.0, "arms": 12.0, "sentence": 12.0}
TASK_SIGN = {"smile": "face", "arms": "arm", "sentence": "speech"}
TASK_RESOURCE = {"smile": "camera", "arms": "camera", "sentence": "microphone"}
TASKS = ("smile", "arms", "sentence")
