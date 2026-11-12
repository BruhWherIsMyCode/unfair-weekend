"""All drawing for the hand-test window. Display only: no measuring here.

Same look as display.py (the helpers are copied on purpose, so a change there
cannot break this window): the camera picture (NOT flipped) on the left, a
dark side panel on the right. Every string is drawn with ONE cv2.putText call.
"""
from __future__ import annotations

import math
from typing import List, Optional

import cv2
import numpy as np

from analysis import config
from analysis.hand_metric import HandFrameReading
from analysis.hand_summary import summarise

FONT = cv2.FONT_HERSHEY_SIMPLEX
PANEL_W = 480
MIN_PICTURE_W = 720
MIN_HEIGHT = 820  # the panel has many lines
PAD = 14

WHITE = (255, 255, 255)
GREY = (150, 150, 150)
HEAD = (230, 200, 120)
GREEN = (80, 220, 80)
RED = (70, 70, 255)
YELLOW = (0, 220, 255)
CYAN = (255, 255, 0)
ORANGE = (0, 165, 255)
BG = (32, 32, 32)

HAND_NAMES = {"left": "HER LEFT HAND", "right": "HER RIGHT HAND"}
MODE_NAMES = {"normal": "NORMAL", "weak": "ACTED WEAK"}
READING_SHORT = (("hand_sink", "sink"), ("hand_height", "height"), ("palm_turn", "turn"),
                 ("hand_shake", "shake"))
FACE_REASON_TEXT = {
    "face not found": "No face found",
    "face too small or far away": "Move closer",
}


def _put(img, s, x, y, scale=0.5, color=WHITE, thick=1, max_x=None):
    """One putText call; shrink the text so it does not pass max_x."""
    if max_x is not None:
        while scale > 0.3:
            (tw, _), _ = cv2.getTextSize(s, FONT, scale, thick)
            if x + tw <= max_x:
                break
            scale -= 0.03
    cv2.putText(img, s, (int(x), int(y)), FONT, scale, color, thick, cv2.LINE_AA)


def _wrap(s, scale, width):
    lines, cur = [], ""
    for word in s.split():
        trial = word if not cur else cur + " " + word
        if cur and cv2.getTextSize(trial, FONT, scale, 1)[0][0] > width:
            lines.append(cur)
            cur = word
        else:
            cur = trial
    if cur:
        lines.append(cur)
    return lines


def _bar(img, x, y, w, h, value, lo, hi, color):
    cv2.rectangle(img, (x, y), (x + w, y + h), (100, 100, 100), 1)
    if value is None:
        return
    v = max(lo, min(hi, value))
    mx = x + int((v - lo) / (hi - lo) * w)
    if lo < 0 < hi:
        cx = x + int((0 - lo) / (hi - lo) * w)
        cv2.rectangle(img, (min(cx, mx), y + 1), (max(cx, mx), y + h - 1), color, -1)
        cv2.line(img, (cx, y - 3), (cx, y + h + 3), (210, 210, 210), 1)
    else:
        cv2.rectangle(img, (x + 1, y + 1), (mx, y + h - 1), color, -1)
    cv2.line(img, (mx, y - 4), (mx, y + h + 4), WHITE, 2)


def _extended_line(img, p1, p2, color):
    dx, dy = p2[0] - p1[0], p2[1] - p1[1]
    a = (int(p1[0] - 0.4 * dx), int(p1[1] - 0.4 * dy))
    b = (int(p2[0] + 0.4 * dx), int(p2[1] + 0.4 * dy))
    cv2.line(img, a, b, color, 2, cv2.LINE_AA)


def _draw_picture(pic, obs, hr: HandFrameReading, mirrored, scale, show_points, phase_text,
                  start_height):
    def to_screen(p):
        x = (obs.image_w - p[0]) if mirrored else p[0]
        return (int(x * scale), int(p[1] * scale))

    if hr.eye_line is not None:
        eye_r, eye_l = hr.eye_line
        _extended_line(pic, to_screen(eye_r), to_screen(eye_l), CYAN)
    elif obs.face_found and len(obs.points) >= 2:
        _extended_line(pic, to_screen(obs.points["her_right_eye"]),
                       to_screen(obs.points["her_left_eye"]), CYAN)

    if hr.palm_centre is not None:
        if show_points:
            for p in hr.hand_points:
                cv2.circle(pic, to_screen(p), 3, YELLOW, -1)
        c = to_screen(hr.palm_centre)
        half = int(40 * scale / 1.0)
        # current height tick
        cv2.line(pic, (c[0] - half, c[1]), (c[0] + half, c[1]), GREEN if hr.usable else RED, 3)
        # start height tick: the same palm shifted along the eye-line normal to the start height
        if start_height is not None and hr.eye_line is not None and hr.height is not None:
            eye_r, eye_l = hr.eye_line
            ex, ey = eye_l[0] - eye_r[0], eye_l[1] - eye_r[1]
            unit = math.hypot(ex, ey)
            if unit > 1e-9:
                nx, ny = -ey / unit, ex / unit
                shift = (hr.height - start_height) * unit
                s = to_screen((hr.palm_centre[0] - nx * shift, hr.palm_centre[1] - ny * shift))
                cv2.line(pic, (s[0] - half, s[1]), (s[0] + half, s[1]), GREY, 3)

    if phase_text:
        ph, pw = pic.shape[:2]
        s = " ".join(phase_text.split())
        (tw, th), base = cv2.getTextSize(s, FONT, 1.6, 3)
        x0 = (pw - tw) // 2
        y0 = ph - 30
        cv2.rectangle(pic, (x0 - 16, y0 - th - 14), (x0 + tw + 16, y0 + base + 12), (25, 25, 25), -1)
        cv2.putText(pic, s, (x0, y0), FONT, 1.6, WHITE, 3, cv2.LINE_AA)


def _trial_line(t: dict) -> str:
    short = "L" if t["hand"] == "left" else "R"
    if t["quality"] != "usable":
        reason = (t.get("reason") or "").split(";")[0].split(" (")[0]
        return "#%d unusable: %s" % (t["trial"], reason)

    def f(v):
        return "-" if v is None else "%+.2f" % v
    return "#%d %s %s: sink %s height %s turn %s" % (
        t["trial"], short, t["mode"], f(t["hand_sink"]), f(t["hand_height"]), f(t["palm_turn"]))


def _draw_panel(panel, hr: HandFrameReading, hand: str, mode: str, trials: List[dict],
                trial_active: bool, sink: Optional[float], turn: Optional[float]):
    H = panel.shape[0]
    x0, x1 = PAD, PANEL_W - PAD
    bar_w = x1 - x0
    y = 10
    word = hand.upper()

    def heading(s):
        nonlocal y
        y += 16
        _put(panel, s, x0, y, 0.45, HEAD, 1, x1)

    def line(s, color=WHITE, scale=0.55, thick=1, gap=22):
        nonlocal y
        y += gap
        if y < H:
            _put(panel, s, x0, y, scale, color, thick, x1)

    def grey(s):
        nonlocal y
        for part in _wrap(s, 0.38, bar_w):
            y += 15
            if y < H:
                _put(panel, part, x0, y, 0.38, GREY, 1, x1)

    # 1. FACE
    if hr.face_ok:
        line("Face OK", GREEN, 0.9, 2, 30)
    else:
        line(FACE_REASON_TEXT.get(hr.reason, hr.reason or "No face found"), RED, 0.8, 2, 30)

    # 2. HAND
    heading("HAND")
    if hr.usable:
        line("Her %s hand seen" % word, GREEN, 0.6, 1, 22)
    elif hr.wrong_side:
        line("wrong hand: use her %s hand" % word, RED, 0.6, 1, 22)
    elif hr.face_ok:
        line("No hand on her %s side" % word, ORANGE, 0.6, 1, 22)
    else:
        line("-", GREY, 0.5, 1, 22)
    if hr.hand_label:
        grey("library label (for reference only): %s" % hr.hand_label)

    # 3. HAND HEIGHT
    heading("HAND HEIGHT")
    v = hr.height if hr.usable else None
    y += 8
    _bar(panel, x0, y, bar_w, 14, v, -0.5, 1.5, GREEN)
    y += 14
    if v is None:
        line("-", GREY, 0.5, 1, 20)
    else:
        line("palm %.2f eye-widths %s the eye line" % (abs(v), "below" if v >= 0 else "above"),
             WHITE, 0.5, 1, 20)

    # 4. SINK SO FAR
    heading("SINK SO FAR")
    y += 8
    _bar(panel, x0, y, bar_w, 12, sink if trial_active else None, 0.0, 0.5,
         GREEN if sink is not None and sink < 0.05 else ORANGE)
    y += 12
    if not trial_active or sink is None:
        line("-", GREY, 0.5, 1, 20)
    elif sink < 0.05:
        line("steady", GREEN, 0.5, 1, 20)
    else:
        line("sank %.2f" % sink, ORANGE, 0.5, 1, 20)

    # 5. PALM TURN
    heading("PALM TURN")
    if turn is None:
        line("-", GREY, 0.5, 1, 20)
    elif turn < 0.05:
        line("palm facing camera", GREEN, 0.5, 1, 20)
    else:
        line("palm turning in by %.2f" % turn, ORANGE, 0.5, 1, 20)

    # 6. note
    grey("measured against her face, so moving the phone does not count")

    # 7. TEST
    y += 10
    cv2.line(panel, (x0, y), (x1, y), (90, 90, 90), 1)
    heading("TEST")
    line("%s  %s" % (HAND_NAMES[hand], MODE_NAMES[mode]), WHITE, 0.8, 2, 28)

    def cnt(h, m):
        return sum(1 for t in trials if t["hand"] == h and t["mode"] == m and t["quality"] == "usable")
    line("L normal %d/10  L weak %d/10  R normal %d/10  R weak %d/10"
         % (cnt("left", "normal"), cnt("left", "weak"), cnt("right", "normal"), cnt("right", "weak")),
         WHITE, 0.45, 1, 20)
    if trials:
        line(_trial_line(trials[-1]), GREY, 0.42, 1, 20)
    line("1 her left hand  2 her right hand  n normal  w acted weak", HEAD, 0.4, 1, 18)
    line("SPACE start  m points  q quit", HEAD, 0.4, 1, 16)

    # 8. SO FAR
    heading("SO FAR")
    if not any(t["mode"] == "normal" and t["quality"] == "usable" for t in trials):
        line("need normal trials first", GREY, 0.5, 1, 20)
        return
    summ = summarise(trials)["hands"]
    for h, short in (("left", "L"), ("right", "R")):
        for reading, nm in READING_SHORT:
            e = summ[h]["readings"][reading]
            n = e["normal"]
            if n["count"] == 0:
                continue
            if e["above"] is None:
                weak = "weak -"
            else:
                weak = "weak %d/%d above %s" % (e["above"], e["count"],
                                                "OK" if e["above"] == e["count"] else "X")
            line("%s %s: normal %.2f..%.2f | %s" % (short, nm, n["min"], n["max"], weak),
                 WHITE, 0.42, 1, 17)


def render(frame, obs, hr: HandFrameReading, *, mirrored: bool, show_points: bool, hand: str,
           mode: str, trials: List[dict], phase_text: Optional[str], trial_active: bool,
           start_height: Optional[float], sink: Optional[float], turn: Optional[float]):
    """Return the whole window image: picture on the left, panel on the right."""
    h, w = frame.shape[:2]
    scale = max(1.0, MIN_PICTURE_W / float(w), MIN_HEIGHT / float(h))
    pic = cv2.resize(frame, (int(round(w * scale)), int(round(h * scale))),
                     interpolation=cv2.INTER_LINEAR)
    _draw_picture(pic, obs, hr, mirrored, scale, show_points, phase_text, start_height)
    panel = np.full((pic.shape[0], PANEL_W, 3), BG, dtype=np.uint8)
    _draw_panel(panel, hr, hand, mode, trials, trial_active, sink, turn)
    return np.hstack([pic, panel])
