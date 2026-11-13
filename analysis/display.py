"""All drawing for the droop-test window. Display only: no measuring here.

Layout: the camera picture (NOT flipped) on the left, a dark side panel on
the right. Every string is drawn with ONE cv2.putText call.
"""
from __future__ import annotations

from typing import Optional

import cv2
import numpy as np

from analysis import config, guide_draw
from analysis.droop_summary import summarise
from analysis.face_metric import face_frame

FONT = cv2.FONT_HERSHEY_SIMPLEX
PANEL_W = 460
MIN_PICTURE_W = 720
MIN_HEIGHT = 960  # the panel has many lines (and a plain-word caption under each); make the window tall enough

# One short plain-word sentence under each panel item.
TEXT_MOUTH_LEVEL = ("0 = both mouth corners equally high. Off-centre = one corner lower. "
                    "Everyone is a little uneven; we compare with your own usual.")
TEXT_MOUTH_LEVEL_3D = ("measured in the face's own 3D frame, so head turn should not count "
                       "(unverified)")
TEXT_SMILE = "how strongly you are smiling right now (0 to 1)"
TEXT_SMILE_BALANCE = ("Google's own guess of which side smiles less. "
                      "A crooked camera angle also moves it.")
TEXT_HEAD = "which way your head is turned; big turns make the other numbers less reliable"
TEXT_STEADINESS = ("how much the mouth-level number wobbles while you hold still; "
                   "a droop must be much bigger than this")
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

LABEL_NAMES = {"normal": "NORMAL", "acted_left": "DROOP LEFT", "acted_right": "DROOP RIGHT"}
REASON_TEXT = {
    "face not found": "No face found",
    "face points missing": "No face found",
    "eyes at the same spot": "No face found",
    "face too small or far away": "Move closer",
    "head turned too far": "Turn your head to the camera",
    "head tipped too far": "Face the camera, not up or down",
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


def _draw_face_frame(pic, obs, to_screen):
    """The face's own axes (x and down) from the eye mid-point, projected with x, y only.
    They should follow the head when it turns or tips."""
    frame = face_frame(obs.points3d)
    if frame is None:
        return
    origin, x_axis, down, unit = frame
    eye_l = obs.points3d["her_left_eye"]
    mid = ((origin[0] + eye_l[0]) / 2.0, (origin[1] + eye_l[1]) / 2.0)
    length = 0.8 * unit
    start = to_screen(mid)
    for axis, color, name in ((x_axis, ORANGE, "x"), (down, YELLOW, "down")):
        end = to_screen((mid[0] + axis[0] * length, mid[1] + axis[1] * length))
        cv2.arrowedLine(pic, start, end, color, 3, cv2.LINE_AA, 0, 0.25)
        _put(pic, name, end[0] + 6, end[1] + 6, 0.5, color)


def _draw_picture(pic, obs, fr, mirrored, scale, show_mesh, phase_text):
    def to_screen(p):
        x = (obs.image_w - p[0]) if mirrored else p[0]
        return (int(x * scale), int(p[1] * scale))

    if obs.face_found:
        if show_mesh:
            for p in obs.mesh:
                cv2.circle(pic, to_screen(p), 1, (170, 170, 170), -1)
        pts = {k: to_screen(v) for k, v in obs.points.items()}
        if len(pts) == 4:
            ok = fr is not None and fr.usable
            _extended_line(pic, pts["her_left_eye"], pts["her_right_eye"], CYAN)
            _extended_line(pic, pts["her_left_mouth"], pts["her_right_mouth"], GREEN if ok else RED)
        names = {"her_left_eye": "her L eye", "her_right_eye": "her R eye",
                 "her_left_mouth": "her L mouth", "her_right_mouth": "her R mouth"}
        for k, p in pts.items():
            cv2.circle(pic, p, 4, YELLOW, -1)
            _put(pic, names.get(k, k), p[0] + 7, p[1] - 7, 0.45, YELLOW)
        if show_mesh:
            _draw_face_frame(pic, obs, to_screen)

    if phase_text:
        ph, pw = pic.shape[:2]
        s = " ".join(phase_text.split())
        (tw, th), base = cv2.getTextSize(s, FONT, 1.6, 3)
        x0 = (pw - tw) // 2
        y0 = ph - 30
        cv2.rectangle(pic, (x0 - 16, y0 - th - 14), (x0 + tw + 16, y0 + base + 12), (25, 25, 25), -1)
        cv2.putText(pic, s, (x0, y0), FONT, 1.6, WHITE, 3, cv2.LINE_AA)


def _head_text(obs):
    yaw, pitch = obs.yaw_deg, obs.pitch_deg
    if yaw is None and pitch is None:
        return "-"
    parts = []
    if yaw is not None and abs(yaw) >= 5:
        parts.append("turned %s %.0f deg" % ("right" if yaw > 0 else "left", abs(yaw)))
    if pitch is not None and abs(pitch) >= 5:
        parts.append("tilted %s %.0f deg" % ("up" if pitch > 0 else "down", abs(pitch)))
    return ", ".join(parts) if parts else "straight"


def _draw_panel(panel, obs, fr, label, trials, jitter, guide=None):
    H = panel.shape[0]
    x0, x1 = PAD, PANEL_W - PAD
    bar_w = x1 - x0
    y = 10

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
    if fr is not None and fr.usable:
        line("Face OK", GREEN, 0.9, 2, 30)
    else:
        why = REASON_TEXT.get(fr.reason, fr.reason) if fr is not None and fr.reason else "No face found"
        line(why, RED, 0.8, 2, 30)

    has = fr is not None and fr.corner_height is not None

    # 2. MOUTH LEVEL
    heading("MOUTH LEVEL")
    v = fr.corner_height if has else None
    y += 8
    _bar(panel, x0, y, bar_w, 14, v, -0.15, 0.15, GREEN if v is not None and abs(v) < 0.01 else ORANGE)
    y += 14
    if v is None:
        line("-", GREY, 0.5, 1, 20)
    elif abs(v) < 0.01:
        line("level", GREEN, 0.55, 1, 20)
    else:
        line("her %s corner lower by %.3f" % ("LEFT" if v > 0 else "RIGHT", abs(v)), ORANGE, 0.55, 1, 20)
    grey(TEXT_MOUTH_LEVEL)

    # 2b. MOUTH LEVEL, angle-corrected
    heading("MOUTH LEVEL (angle-corrected)")
    v3 = fr.corner_height_3d if fr is not None else None
    y += 8
    _bar(panel, x0, y, bar_w, 14, v3, -0.15, 0.15, GREEN if v3 is not None and abs(v3) < 0.01 else ORANGE)
    y += 14
    if v3 is None:
        line("-", GREY, 0.5, 1, 20)
    elif abs(v3) < 0.01:
        line("level", GREEN, 0.55, 1, 20)
    else:
        line("her %s corner lower by %.3f" % ("LEFT" if v3 > 0 else "RIGHT", abs(v3)), ORANGE, 0.55, 1, 20)
    grey(TEXT_MOUTH_LEVEL_3D)

    # 3. SMILE
    heading("SMILE")
    s = fr.smile_strength if has else None
    y += 8
    _bar(panel, x0, y, bar_w, 12, s, 0.0, 1.0, GREEN)
    y += 12
    line("-" if s is None else ("smiling" if s >= 0.5 else "relaxed"), WHITE, 0.5, 1, 20)
    grey(TEXT_SMILE)

    # 4. SMILE BALANCE
    if has and fr.smile_score is not None:
        heading("SMILE BALANCE")
        sc = fr.smile_score
        y += 8
        _bar(panel, x0, y, bar_w, 12, sc, -0.5, 0.5, GREEN if abs(sc) < 0.05 else ORANGE)
        y += 12
        if abs(sc) < 0.05:
            line("even", GREEN, 0.5, 1, 20)
        else:
            line("her %s side smiles less by %.2f" % ("LEFT" if sc > 0 else "RIGHT", abs(sc)),
                 ORANGE, 0.5, 1, 20)
        grey(TEXT_SMILE_BALANCE)

    # 5. HEAD
    heading("HEAD")
    line(_head_text(obs) if obs.face_found else "-", WHITE, 0.5, 1, 20)
    grey(TEXT_HEAD + " (angle maths unverified)")

    # 6. STEADINESS
    heading("STEADINESS")
    if jitter is None:
        line("measuring...", GREY, 0.5, 1, 20)
    elif jitter < 0.006:
        line("steady (%.3f)" % jitter, GREEN, 0.5, 1, 20)
    else:
        line("moving (%.3f)" % jitter, ORANGE, 0.5, 1, 20)
    grey(TEXT_STEADINESS)

    # 7. WINK CHECK
    heading("WINK CHECK")
    line("wink your LEFT eye", WHITE, 0.5, 1, 20)
    bl = obs.blendshapes.get("eyeBlinkLeft", 0.0)
    br = obs.blendshapes.get("eyeBlinkRight", 0.0)
    if obs.face_found and abs(bl - br) >= config.WINK_MIN_DIFF:
        if bl > br:
            line("seen: HER LEFT", GREEN, 0.5, 1, 20)
        else:
            line("seen: HER RIGHT", YELLOW, 0.5, 1, 20)
            grey("if you winked your left eye, restart with --mirrored")

    # 8. TEST
    y += 10
    cv2.line(panel, (x0, y), (x1, y), (90, 90, 90), 1)
    heading("TEST")
    if guide is not None:
        for part in _wrap(guide.block or "Starting", 0.6, bar_w):
            line(part, WHITE, 0.6, 1, 24)
    else:
        line(LABEL_NAMES.get(label, label), WHITE, 0.9, 2, 30)
    counts = {lab: sum(1 for t in trials if t["label"] == lab and t["quality"] == "usable")
              for lab in LABEL_NAMES}
    line("normal %d/10   left %d/10   right %d/10"
         % (counts["normal"], counts["acted_left"], counts["acted_right"]), WHITE, 0.5, 1, 20)
    if trials:
        t = trials[-1]
        if t["quality"] == "usable":
            txt = "#%d %s: mouth level %+.3f, lift %+.3f" % (
                t["trial"], LABEL_NAMES.get(t["label"], t["label"]), t["corner_height"], t["corner_lift"])
            if t["smile_score"] is not None:
                txt += ", balance %+.2f" % t["smile_score"]
            if t.get("pose"):
                txt += ", pose %s, yaw %s, pitch %s" % (
                    t["pose"],
                    "?" if t.get("yaw_deg") is None else "%+.0f" % t["yaw_deg"],
                    "?" if t.get("pitch_deg") is None else "%+.0f" % t["pitch_deg"])
        else:
            reason = (t.get("reason") or "").split(";")[0].split(" (")[0]
            txt = "#%d unusable: %s" % (t["trial"], reason)
        line(txt, GREY, 0.42, 1, 20)
    if guide is not None:
        line("Runs by itself. Q = stop.", HEAD, 0.5, 1, 20)
    else:
        line("n normal  l left  r right  SPACE start  m mesh  q quit", HEAD, 0.42, 1, 20)

    # 9. SO FAR
    heading("SO FAR")
    if counts["normal"] == 0:
        line("need normal trials first", GREY, 0.5, 1, 20)
        return
    summ = summarise(trials)["metrics"]
    names = {"corner_height": "mouth level", "corner_lift": "lift", "smile_score": "balance",
             "corner_height_3d": "level 3D"}
    for metric, nm in names.items():
        m = summ[metric]
        n = m["labels"]["normal"]
        if n["count"] == 0:
            continue
        line("%s: normal %+.2f..%+.2f" % (nm, n["min"], n["max"]), WHITE, 0.45, 1, 18)
        parts = []
        for lab, short in (("acted_left", "left"), ("acted_right", "right")):
            ver = m["verdicts"][lab]
            if ver["wrong_side"] is None:
                parts.append("%s -" % short)
            else:
                good = ver["count"] - ver["wrong_side"]
                parts.append("%s %d/%d outside %s" % (short, good, ver["count"],
                                                       "OK" if ver["wrong_side"] == 0 else "X"))
        line("   " + " | ".join(parts), WHITE, 0.45, 1, 18)


def render(frame, obs, fr, *, mirrored: bool, show_mesh: bool, label: str, trials: list,
           jitter: Optional[float], phase_text: Optional[str], guide=None):
    """Return the whole window image: picture on the left, panel on the right.

    guide: a GuideView in guided mode (banner, countdown, no key hints), else None.
    """
    h, w = frame.shape[:2]
    scale = max(1.0, MIN_PICTURE_W / float(w), MIN_HEIGHT / float(h))
    pic = cv2.resize(frame, (int(round(w * scale)), int(round(h * scale))),
                     interpolation=cv2.INTER_LINEAR)
    _draw_picture(pic, obs, fr, mirrored, scale, show_mesh, None if guide is not None else phase_text)
    if guide is not None:
        guide_draw.draw(pic, guide)
    panel = np.full((pic.shape[0], PANEL_W, 3), BG, dtype=np.uint8)
    _draw_panel(panel, obs, fr, label, trials, jitter, guide)
    return np.hstack([pic, panel])
