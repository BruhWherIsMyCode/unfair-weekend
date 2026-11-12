"""Every threshold and tuning constant of the face tool, in one place.

Every number here is a FIRST GUESS to tune after the droop test. None of
them has been measured yet.

Conventions used everywhere in this package:
- "left" and "right" mean HER left and HER right (the person in the video).
- A positive reading means her LEFT side is lower or weaker; negative means
  her right side; zero means level.
"""
from __future__ import annotations

from pathlib import Path

# ---- Paths -----------------------------------------------------------------
ANALYSIS_DIR = Path(__file__).resolve().parent  # the analysis/ folder
MODEL_PATH = ANALYSIS_DIR / "models" / "face_landmarker.task"  # downloaded by get_models
RUNS_DIR = ANALYSIS_DIR / "runs"  # droop-test CSV and summaries (gitignored)
RECORDINGS_DIR = ANALYSIS_DIR / "recordings"  # optional trial videos (gitignored)
DATA_DIR = ANALYSIS_DIR / "data"  # one baseline JSON per person (gitignored)

MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/face_landmarker/"
    "face_landmarker/float16/latest/face_landmarker.task"
)  # Google's model file

# ---- Landmark indices (from memory, confirm with the wink check) -----------
# Which index of a pair is her left is NOT assumed: sides are assigned by
# geometry (in the un-mirrored image her left is on the image's right).
EYE_OUTER_CORNER_IDS = (33, 263)  # outer eye corners, from memory, confirm with the wink check
MOUTH_CORNER_IDS = (61, 291)  # mouth corners, from memory, confirm with the wink check
FOREHEAD_ID = 10  # forehead point, from memory, unverified (used for the face's own 3D frame)
NOSE_BRIDGE_ID = 168  # nose-bridge point, from memory, unverified

# ---- Frame usability --------------------------------------------------------
MIN_EYE_DISTANCE_PX = 40.0  # smaller face than this (pixels between eyes) is unusable; first guess
MAX_YAW_DEG = 20.0  # head turned more than this left/right is unusable; first guess
MAX_PITCH_DEG = 20.0  # head tipped more than this up/down is unusable; first guess
CHECK_HEAD_ANGLES = True  # set False (or pass --no-angle-check) if the unverified angle maths marks a frontal face unusable

# ---- Clip reading -----------------------------------------------------------
RELAXED_FRACTION = 0.2  # share of usable frames (weakest smile) taken as the relaxed face; first guess
PEAK_FRACTION = 0.2  # share of usable frames (strongest smile) taken as the peak; first guess
MIN_PEAK_FRAMES = 3  # at least this many peak frames are used; first guess
MIN_USABLE_FRAMES = 8  # fewer usable frames than this makes the clip unusable; first guess
MIN_SMILE_RISE = 0.15  # peak minus relaxed smile strength (stronger side) below this means no smile happened; lowered from 0.25 after the owner's first run rejected normal smiles

# ---- Baseline ---------------------------------------------------------------
BASELINE_K = 3.0  # range = median +/- K x robust spread; first guess
BASELINE_NEEDED = 5  # values needed before the baseline is ready (else "learning"); first guess
MIN_HALF_WIDTH = {  # minimum half-width of the usual range per metric, so a steady person does not trigger on noise; first guess
    "corner_height": 0.02,
    "corner_lift": 0.02,
    "corner_height_3d": 0.02,  # first guess
    "smile_score": 0.08,
    "hand_sink": 0.05,  # first guess
    "hand_height": 0.10,  # first guess
    "palm_turn": 0.10,  # first guess
    "hand_shake": 0.02,  # first guess
}
DEFAULT_MIN_HALF_WIDTH = 0.02  # used for a metric not listed above; first guess
BASELINE_LABELS = ("normal", "live")  # session labels that may enter the baseline ("acted" never does)
MAD_TO_SIGMA = 1.4826  # standard factor turning median absolute deviation into a spread

# ---- Droop test (live window) ------------------------------------------------
RELAX_SECONDS = 1.0  # length of the RELAX phase of a trial; first guess
SMILE_SECONDS = 3.0  # length of the SMILE phase of a trial; first guess
JITTER_WINDOW = 30  # usable frames used for the live jitter number; first guess
WINK_MIN_DIFF = 0.3  # blink score difference needed to call a wink side on screen; first guess
RECORD_FPS = 30.0  # frame rate written into --record-trials videos; first guess
DEFAULT_FPS = 30.0  # used by measure_clip when a file does not report its fps

# ---- Droop summary ------------------------------------------------------------
MIN_NORMAL_SPREAD = 1e-6  # floor for normal spread so the gap ratio never divides by zero

# ---- Hand task (one hand held up beside the face; every number is a first guess) ----
HAND_MODEL_PATH = ANALYSIS_DIR / "models" / "hand_landmarker.task"  # downloaded by get_models; first guess
HAND_MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/hand_landmarker/"
    "hand_landmarker/float16/latest/hand_landmarker.task"
)  # from memory, unverified; get_models reports a failed download plainly
HAND_SETTLE_S = 1.0  # seconds to get the hand up before the hold counts; first guess, not measured
HAND_HOLD_S = 5.0  # length of the hold in a live trial; first guess (short by the owner's choice)
HAND_WINDOW_S = 1.0  # start window and end window length inside the hold; first guess
HAND_MIN_USABLE_FRAMES = 15  # fewer usable hold frames than this makes the clip unusable; first guess
DROP_COUNTS_AS_SINK = True  # a hand that drops out of view counts as a big sink; first guess
DROP_SINK_VALUE = 1.0  # sink value (eye widths) given to a dropped hand; first guess
HAND_READINGS = ("hand_sink", "hand_height", "palm_turn", "hand_shake")  # candidate readings; the hand test picks the winner
HAND_PRIMARY = "hand_sink"  # primary reading for now; first guess

# ---- Guided (self-running) tests; every number is a first guess ----------------------
GUIDE_READY_S = 0.8  # the face / hand must be usable this long, without a gap, before a trial starts; first guess
GUIDE_HINT_AFTER_S = 8.0  # seconds of waiting before the window shows a hint; first guess
GUIDE_RESULT_S = 2.0  # seconds the "Got it" / "did not count" message stays up; first guess
GUIDE_MAX_RETRIES = 2  # an unusable trial is repeated at most this many times, then skipped; first guess
GUIDE_FACE_PER_LABEL = 5  # face trials per label (normal, acted left, acted right); first guess
GUIDE_HAND_PER_MODE = 5  # hand trials per hand and mode (normal, acted weak); first guess
GUIDE_INTRO_S = 3.0  # length of the card shown before each block; first guess
GUIDE_START_MIN_S = 3.0  # the start card stays at least this long; first guess
GUIDE_START_FACE_S = 1.0  # and until the face has been seen this long; first guess
GUIDE_LOWER_NO_HAND_S = 0.5  # "Lower your hand" ends after no hand was seen this long; first guess
GUIDE_LOWER_MAX_S = 3.0  # or after this many seconds; first guess
GUIDE_DONE_CLOSE_S = 60.0  # the finished screen closes by itself after this; first guess

# ---- "I saw" texts and the angle test; every number is a first guess -------------------
SEEN_LEVEL = 0.02  # |corner_height| below this is shown as "level"; first guess
SEEN_SINK = 0.05  # hand sink below this is shown as "hand steady"; first guess
SEEN_PALM_TURN = 0.10  # palm turn above this is added to the hand "I saw" text; first guess
GUIDE_ANGLE_PER_POSE = 3  # trials per pose in the angle test; first guess
ANGLE_RATIO_OK = 0.5  # angle shift / droop size below this is "angle OK"; first guess
ANGLE_RATIO_RISKY = 1.0  # above this is "angle BREAKS IT", in between "angle RISKY"; first guess

# ---- Output -----------------------------------------------------------------
METRICS = ("corner_height", "corner_lift", "smile_score", "corner_height_3d")  # the candidate readings
