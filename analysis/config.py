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
MIN_SMILE_RISE = 0.25  # peak minus relaxed smile strength below this means no smile happened; first guess

# ---- Baseline ---------------------------------------------------------------
BASELINE_K = 3.0  # range = median +/- K x robust spread; first guess
BASELINE_NEEDED = 5  # values needed before the baseline is ready (else "learning"); first guess
MIN_HALF_WIDTH = {  # minimum half-width of the usual range per metric, so a steady person does not trigger on noise; first guess
    "corner_height": 0.02,
    "corner_lift": 0.02,
    "smile_score": 0.08,
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

# ---- Output -----------------------------------------------------------------
METRICS = ("corner_height", "corner_lift", "smile_score")  # the three candidate readings
