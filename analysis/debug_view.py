"""Debug mode (UW_DEBUG=on): show during a call what the camera sees. It only REPORTS; no measuring rule changes.

Why: on the first real phone calls every arms attempt stopped with `wrong_hand_use_your_left_hand`. Either the phone's
frames are mirrored (her left hand then shows up on her "right" side of the face midline) or she raised the other hand.

Pieces:
- hand_side_facts(): pure. Face found yes/no and, for each hand found, which side of HER face it is on (the same
  midline rule and the same `mirrored` setting as hand_metric.hand_frame_reading), its distance from the midline in eye
  widths, and the side it would be on with the opposite mirroring setting (`if_mirrored_flipped`).
- DebugView: one per call. Fed one frame at a time by the production frame readers (a hook that is None when debug is
  off), it prints about one plain line per second, counts per phase, saves about two pictures per second, and at the end
  of an arms attempt gives the summary, the verdict and the caption the session plays. It also queues one mid-hold
  caption the first time the hand has been on the wrong side for 1 s (at most one per hold phase).
- write_picture(): the default picture writer. cv2 is imported only there, lazily; tests inject a fake writer.

Captions never contain a measurement number. Pictures go to <pictures_dir>/<call_id>/<attempt>_<task>_<ms>.jpg
(analysis/runs/ is gitignored), at most MAX_PICTURES per call. Nothing is printed or saved when debug is off.
"""
from __future__ import annotations

import math
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from analysis import config
from analysis.face_metric import seen_side
from analysis.hand_metric import palm_centre
from analysis.session import limits

LEFT, RIGHT = "her_left", "her_right"
LINE_PERIOD_MS = 1000  # one terminal line per second of capture time (the observation period)
PICTURE_PERIOD_MS = 500  # about two pictures per second
MAX_PICTURES = 200  # per call
WRONG_HOLD_MS = 1000  # on the wrong side this long: one caption during the hold
CATEGORIES = ("left", "right", "none", "no_face")  # where the hand of a frame was: her side, no hand, no face

Writer = Callable[[Path, Any, Dict[str, Any]], None]


def side_name(expected: str) -> str:
    """'left' -> 'her_left' (the side words of the measuring are 'left' / 'right')."""
    return LEFT if expected == "left" else RIGHT


def flipped_side(side: str) -> str:
    return RIGHT if side == LEFT else LEFT


def upper(side: str) -> str:
    return "her LEFT" if side == LEFT else "her RIGHT"


def hand_side_facts(face_obs: Any, hands: Optional[Sequence[Any]], expected_side: str,
                    mirrored: bool = False) -> Dict[str, Any]:
    """What one hand frame shows, as facts. `face_obs` / `hands` are what the extractor returned for the frame, in the
    true pixel space of the `mirrored` setting (the frame is flipped before detection when mirrored, so these points
    already follow that setting). A hand with a larger x than the face midline is on HER LEFT (the measuring's rule).

    Returns {"face": bool, "face_small": bool, "expected": "left"|"right", "mirrored": bool, "midline_x", "unit",
    "eye_points", "points": all named face points, "hands": [{"side": "her_left"|"her_right", "eye_widths": float,
    "if_mirrored_flipped": the side with the opposite setting, "centre", "box"}]}."""
    facts: Dict[str, Any] = {"face": False, "face_small": False, "expected": expected_side, "mirrored": bool(mirrored),
                             "midline_x": None, "unit": None, "eye_points": [], "points": {}, "hands": []}
    if face_obs is None or not getattr(face_obs, "face_found", False):
        return facts
    eye_l = face_obs.points.get("her_left_eye")
    eye_r = face_obs.points.get("her_right_eye")
    if eye_l is None or eye_r is None:
        return facts
    unit = math.hypot(eye_l[0] - eye_r[0], eye_l[1] - eye_r[1])
    if unit <= 0:
        return facts
    mid_x = (eye_l[0] + eye_r[0]) / 2.0
    facts.update(face=True, face_small=unit < config.MIN_EYE_DISTANCE_PX, midline_x=mid_x, unit=unit,
                 eye_points=[eye_r, eye_l], points=dict(face_obs.points))
    for hand in hands or []:
        if len(hand.points) < 21:
            continue
        centre = palm_centre(hand.points)
        off = centre[0] - mid_x
        side = LEFT if off > 0 else RIGHT
        xs = [p[0] for p in hand.points]
        ys = [p[1] for p in hand.points]
        facts["hands"].append({"side": side, "eye_widths": abs(off) / unit, "if_mirrored_flipped": flipped_side(side),
                               "centre": centre, "box": (min(xs), min(ys), max(xs), max(ys))})
    return facts


def primary_hand(facts: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The hand the measuring would look at: one on the expected side if there is one, else the farthest from the
    midline."""
    hands = facts["hands"]
    if not hands:
        return None
    on_expected = [h for h in hands if h["side"] == side_name(facts["expected"])]
    return max(on_expected or hands, key=lambda h: h["eye_widths"])


def frame_category(facts: Dict[str, Any]) -> str:
    """'left' / 'right' (her side the hand is on), 'none' (face but no hand) or 'no_face'."""
    if not facts["face"]:
        return "no_face"
    hand = primary_hand(facts)
    if hand is None:
        return "none"
    return "left" if hand["side"] == LEFT else "right"


def _hands_text(facts: Dict[str, Any]) -> str:
    hands = facts["hands"]
    if not facts["face"]:
        return "no hand check without a face"
    if not hands:
        return "no hand"
    parts = ["%s (%.1f eye widths)" % (upper(h["side"]), h["eye_widths"]) for h in hands]
    return ("hand on " if len(hands) == 1 else "hands on ") + " and ".join(parts)


def arms_line(ms: int, phase: str, facts: Dict[str, Any]) -> str:
    """The once-per-second terminal line of an arms hold."""
    flipped = " and ".join(upper(h["if_mirrored_flipped"]) for h in facts["hands"]) or "-"
    return "%.1fs DEBUG arms %s: face %s | %s | expected %s | mirroring %s -> if flipped: %s" % (
        ms / 1000.0, phase, "yes" if facts["face"] else "no", _hands_text(facts), facts["expected"].upper(),
        "on" if facts["mirrored"] else "off", flipped)


def smile_line(ms: int, phase: str, reading: Any, obs: Any) -> str:
    """The once-per-second terminal line of a smile attempt."""
    face = obs is not None and getattr(obs, "face_found", False)
    head = "%.1fs DEBUG smile %s: face %s" % (ms / 1000.0, phase, "yes" if face else "no")
    if reading is None or not reading.usable or reading.smile_strength is None:
        return head + " | not usable%s" % (": %s" % reading.reason if reading is not None and reading.reason else "")
    side = seen_side(reading.corner_height)
    if side in (None, "level"):
        corners = "mouth corners level"
    else:
        corners = "mouth corners %s lower by %.2f" % (side, abs(reading.corner_height))
    return head + " | smile strength %.2f | %s" % (reading.smile_strength, corners)


def arms_phase_of(ms: int) -> str:
    for name, start, end, _side in limits.ARMS_PHASES:
        if start <= ms < end:
            return name
    return "outside"


def smile_phase_of(ms: int) -> str:
    return "relax" if ms < limits.SMILE_CUE_MS else "smile"


# ---- captions and the verdict (no numbers anywhere in a caption) ------------------------------------------------------

def wrong_side_caption(seen: str, expected: str) -> str:
    return "Debug: I see a hand on your %s side. I expected your %s." % (seen, expected)


def phase_judgement(counts: Dict[str, int], expected: str) -> str:
    """The majority of the frames of one hold: 'wrong', 'none', 'no_face' or 'good'. Ties go to the more informative one
    (in that order)."""
    other = "right" if expected == "left" else "left"
    scores = [("wrong", counts.get(other, 0)), ("none", counts.get("none", 0)),
              ("no_face", counts.get("no_face", 0)), ("good", counts.get(expected, 0))]
    return max(scores, key=lambda s: s[1])[0]


class _Attempt:
    def __init__(self, task: str) -> None:
        self.task = task
        self.frames = 0
        self.next_line = 0
        self.next_picture = 0
        self.phases: Dict[str, Dict[str, Any]] = {}  # arms: phase -> {"expected", "counts", "wrong_since", "alerted"}
        self.captions: List[str] = []  # mid-hold captions waiting for the session
        self.mirrored = False  # the setting the frames were read with


class DebugView:
    """One per call. Thread-safe: the frame readers run in an executor."""

    def __init__(self, call_id: str, out: Callable[[str], Any], pictures_dir: Optional[Path] = None,
                 writer: Optional[Writer] = None, max_pictures: int = MAX_PICTURES) -> None:
        self.call_id = call_id
        self.out = out
        self.pictures_dir = Path(pictures_dir) / call_id if pictures_dir is not None else None
        self.writer: Writer = writer or write_picture
        self.max_pictures = int(max_pictures)
        self.pictures = 0
        self._attempts: Dict[str, _Attempt] = {}
        self._lock = threading.Lock()
        self._announced = False

    # ---- once per call ----------------------------------------------------------------------------------------

    def announce(self) -> None:
        if self._announced:
            return
        self._announced = True
        if self.pictures_dir is not None:
            self.out("DEBUG pictures are saved in %s (about 2 per second, at most %d)" % (self.pictures_dir,
                                                                                          self.max_pictures))

    def _attempt(self, attempt_id: str, task: str) -> _Attempt:
        st = self._attempts.get(attempt_id)
        if st is None:
            st = self._attempts[attempt_id] = _Attempt(task)
        return st

    def _show(self, line: str) -> None:
        try:
            self.out(line)
        except Exception:  # printing must never break a call
            pass

    def _picture(self, st: _Attempt, attempt_id: str, ms: int, image: Any, overlay: Dict[str, Any]) -> None:
        if self.pictures_dir is None or ms < st.next_picture or self.pictures >= self.max_pictures:
            return
        st.next_picture = (ms // PICTURE_PERIOD_MS + 1) * PICTURE_PERIOD_MS
        path = self.pictures_dir / ("%s_%s_%d.jpg" % (attempt_id, st.task, ms))
        try:
            self.writer(path, image, overlay)
            self.pictures += 1
        except Exception:  # a picture that cannot be saved never stops the call
            self.pictures = self.max_pictures
            self._show("DEBUG pictures stopped: could not save one")

    # ---- frames (called by the production readers) ------------------------------------------------------------

    def arms_frame(self, attempt_id: str, ms: int, expected_side: str, image: Any, face_obs: Any,
                   hands: Optional[Sequence[Any]], mirrored: bool) -> None:
        facts = hand_side_facts(face_obs, hands, expected_side, mirrored)
        category = frame_category(facts)
        phase = arms_phase_of(ms)
        with self._lock:
            st = self._attempt(attempt_id, "arms")
            st.frames += 1
            st.mirrored = bool(mirrored)
            ph = st.phases.setdefault(phase, {"expected": expected_side, "counts": {c: 0 for c in CATEGORIES},
                                             "wrong_since": None, "alerted": False})
            ph["counts"][category] += 1
            if category == ("right" if expected_side == "left" else "left"):
                if ph["wrong_since"] is None:
                    ph["wrong_since"] = ms
                if not ph["alerted"] and ms - ph["wrong_since"] >= WRONG_HOLD_MS:
                    ph["alerted"] = True
                    st.captions.append(wrong_side_caption(category, expected_side))
            else:
                ph["wrong_since"] = None
            if ms >= st.next_line:
                st.next_line = (ms // LINE_PERIOD_MS + 1) * LINE_PERIOD_MS
                self._show(arms_line(ms, phase, facts))
            self._picture(st, attempt_id, ms, image, {
                "task": "arms", "phase": phase, "expected": expected_side, "mirrored": bool(mirrored),
                "midline_x": facts["midline_x"], "eye_points": facts["eye_points"], "points": facts["points"],
                "hands": [{"box": h["box"], "side": h["side"], "centre": h["centre"]} for h in facts["hands"]]})

    def smile_frame(self, attempt_id: str, ms: int, image: Any, obs: Any, reading: Any, mirrored: bool) -> None:
        phase = smile_phase_of(ms)
        points = dict(obs.points) if obs is not None and getattr(obs, "face_found", False) else {}
        eyes = [points[k] for k in ("her_right_eye", "her_left_eye") if k in points]
        midline = sum(p[0] for p in eyes) / 2.0 if len(eyes) == 2 else None
        with self._lock:
            st = self._attempt(attempt_id, "smile")
            st.frames += 1
            if ms >= st.next_line:
                st.next_line = (ms // LINE_PERIOD_MS + 1) * LINE_PERIOD_MS
                self._show(smile_line(ms, phase, reading, obs))
            self._picture(st, attempt_id, ms, image, {
                "task": "smile", "phase": phase, "expected": None, "mirrored": bool(mirrored), "midline_x": midline,
                "eye_points": eyes, "points": points, "hands": []})

    # ---- for the session --------------------------------------------------------------------------------------

    def take_caption(self, attempt_id: str) -> Optional[str]:
        """The next mid-hold caption waiting for this attempt, or None."""
        with self._lock:
            st = self._attempts.get(attempt_id)
            return st.captions.pop(0) if st is not None and st.captions else None

    def finish_attempt(self, attempt_id: str) -> Optional[str]:
        """End of an arms attempt: print the per-phase summary and the verdict; return the caption to play (None when
        nothing was seen by the debug view, e.g. the attempt stopped before its first frame, or for other tasks)."""
        with self._lock:
            st = self._attempts.get(attempt_id)
            if st is None or st.task != "arms" or st.frames == 0:
                return None
            holds = [(name, ph) for name, ph in st.phases.items() if ph["expected"]]
            parts = []
            for name, ph in holds:
                total = sum(ph["counts"].values()) or 1
                share = lambda key: int(round(100.0 * ph["counts"][key] / total))  # noqa: E731
                parts.append("%s (expected %s): hand on her LEFT %d%% | on her RIGHT %d%% | no hand %d%% | no face %d%% "
                             "(%d frames)" % (name, ph["expected"].upper(), share("left"), share("right"),
                                              share("none"), share("no_face"), total))
            verdict, caption = self._verdict(holds, st.mirrored)
        self._show("DEBUG arms %s summary: %s" % (attempt_id, "; ".join(parts)))
        self._show("DEBUG arms %s verdict: %s" % (attempt_id, verdict))
        return caption

    @staticmethod
    def _verdict(holds: Sequence[Any], mirrored: bool) -> "tuple[str, str]":
        judged = [(name, ph, phase_judgement(ph["counts"], ph["expected"])) for name, ph in holds]
        pick = next((j for j in judged if j[2] == "wrong"), None) or next(
            (j for j in judged if j[2] in ("none", "no_face")), None)
        if pick is None:
            good = judged[0][1]["expected"] if judged else "left"
            return "hand on the expected side", "Debug: I see your %s hand. Good." % good
        name, ph, kind = pick
        expected = ph["expected"]
        if kind == "wrong":
            seen = "right" if expected == "left" else "left"
            fix = "restart without --mirrored" if mirrored else "restart with --mirrored"
            return ("hand mostly on the side NOT expected (expected %s, seen %s): if she raised her %s hand as asked, the "
                    "picture is mirrored -> %s; if she raised the other hand, nothing is wrong with the picture"
                    % (expected.upper(), seen.upper(), expected, fix)), wrong_side_caption(seen, expected)
        if kind == "none":
            return "no hand seen", "Debug: no hand seen."
        return "no face seen", "Debug: I do not see your face."


def write_picture(path: Path, image: Any, overlay: Dict[str, Any]) -> None:
    """Draw the overlay on the frame and save it as a JPEG. cv2 is imported here, lazily."""
    import cv2

    img = image.copy()
    if overlay.get("mirrored"):
        img = cv2.flip(img, 1)  # the frame as the code interprets it (the readers flip it before detecting)
    h, w = img.shape[:2]
    scale = max(0.6, w / 640.0)
    thick = max(1, int(round(2 * scale)))
    mid = overlay.get("midline_x")
    if mid is not None:
        cv2.line(img, (int(mid), 0), (int(mid), h), (255, 255, 0), thick)
    for x, y in (overlay.get("points") or {}).values():
        cv2.circle(img, (int(x), int(y)), max(2, int(3 * scale)), (0, 255, 255), -1)
    for x, y in overlay.get("eye_points") or []:
        cv2.circle(img, (int(x), int(y)), max(3, int(5 * scale)), (0, 200, 0), -1)
    for hand in overlay.get("hands") or []:
        x0, y0, x1, y1 = hand["box"]
        cv2.rectangle(img, (int(x0), int(y0)), (int(x1), int(y1)), (0, 0, 255), thick)
        cv2.putText(img, upper(hand["side"]).replace("her ", ""), (int(x0), max(15, int(y0) - 6)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6 * scale, (0, 0, 255), thick)
    big = 1.2 * scale  # her left = larger x = the right half of the picture
    cv2.putText(img, "HER LEFT", (int(w * 0.52), int(40 * scale)), cv2.FONT_HERSHEY_SIMPLEX, big, (0, 165, 255), thick + 1)
    cv2.putText(img, "HER RIGHT", (int(w * 0.02), int(40 * scale)), cv2.FONT_HERSHEY_SIMPLEX, big, (255, 128, 0), thick + 1)
    expected = overlay.get("expected")
    info = "%s %s | expected %s | mirroring %s" % (overlay.get("task"), overlay.get("phase"),
                                                   expected.upper() if expected else "-",
                                                   "on" if overlay.get("mirrored") else "off")
    cv2.putText(img, info, (6, h - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.6 * scale, (255, 255, 255), thick)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), img, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
