"""Drawing for the guided tests: the big instruction banner, the countdown box and
the finished screen. Display only. Every string is drawn with ONE cv2.putText call.
"""
from __future__ import annotations

from typing import List

import cv2

from analysis.guide import GuideView

FONT = cv2.FONT_HERSHEY_SIMPLEX
WHITE = (255, 255, 255)
GREY = (190, 190, 190)
YELLOW = (0, 220, 255)
GREEN = (80, 220, 80)
DARK = (25, 25, 25)
COLORS = {"yellow": YELLOW, "green": GREEN, "white": WHITE}


def wrap(text: str, scale: float, thick: int, width: int) -> List[str]:
    """Word-wrap with cv2.getTextSize so no line is wider than width."""
    lines, cur = [], ""
    for word in text.split():
        trial = word if not cur else cur + " " + word
        if cur and cv2.getTextSize(trial, FONT, scale, thick)[0][0] > width:
            lines.append(cur)
            cur = word
        else:
            cur = trial
    if cur:
        lines.append(cur)
    return lines


def _banner(pic, view: GuideView) -> int:
    """Dark box across the top. Returns its bottom y."""
    ph, pw = pic.shape[:2]
    margin = 18
    width = pw - 2 * margin
    big, thick = 1.2, 2
    small, sthick = 0.7, 1
    lines = wrap(view.instruction, big, thick, width)
    # shrink if it needs more than three lines
    while len(lines) > 3 and big > 0.7:
        big -= 0.1
        lines = wrap(view.instruction, big, thick, width)
    detail = wrap(view.detail, small, sthick, width)[:2] if view.detail else []
    warn = wrap(view.warning, small, sthick, width)[:2] if view.warning else []
    line_h = int(big * 38)
    top = 14
    h = (top + len(lines) * line_h + (len(detail) * 28 + 6 if detail else 0)
         + (len(warn) * 28 + (0 if detail else 6) if warn else 0) + 10)
    cv2.rectangle(pic, (0, 0), (pw, h), DARK, -1)
    cv2.line(pic, (0, h), (pw, h), COLORS.get(view.color, WHITE), 3)
    y = top + int(big * 28)
    for ln in lines:
        cv2.putText(pic, ln, (margin, y), FONT, big, WHITE, thick, cv2.LINE_AA)
        y += line_h
    y -= line_h - 30
    for ln in detail:
        cv2.putText(pic, ln, (margin, y), FONT, small, GREY, sthick, cv2.LINE_AA)
        y += 28
    for ln in warn:
        cv2.putText(pic, ln, (margin, y), FONT, small, YELLOW, sthick, cv2.LINE_AA)
        y += 28
    if view.progress:
        (tw, _), _ = cv2.getTextSize(view.progress, FONT, 0.6, 1)
        cv2.putText(pic, view.progress, (pw - tw - 12, h + 24), FONT, 0.6, WHITE, 1, cv2.LINE_AA)
    return h


def _countdown(pic, view: GuideView) -> None:
    if not view.countdown:
        return
    ph, pw = pic.shape[:2]
    s = " ".join(view.countdown.split())
    (tw, th), base = cv2.getTextSize(s, FONT, 1.6, 3)
    x0 = (pw - tw) // 2
    y0 = ph - 30
    cv2.rectangle(pic, (x0 - 16, y0 - th - 14), (x0 + tw + 16, y0 + base + 12), DARK, -1)
    cv2.putText(pic, s, (x0, y0), FONT, 1.6, COLORS.get(view.color, WHITE), 3, cv2.LINE_AA)


def _finished(pic, view: GuideView, top: int) -> None:
    ph, pw = pic.shape[:2]
    margin = 18
    width = pw - 2 * margin
    scale = 0.65
    rows: List[str] = []
    for ln in (view.summary_lines or ("No verdict yet: not enough usable trials.",)):
        rows.extend(wrap(ln, scale, 1, width))
    rows.append("Press any key to close.")
    row_h = 28
    h = len(rows) * row_h + 20
    y0 = max(top + 40, ph - h - 10)
    cv2.rectangle(pic, (0, y0), (pw, ph), DARK, -1)
    y = y0 + 26
    for ln in rows:
        cv2.putText(pic, ln, (margin, y), FONT, scale, WHITE, 1, cv2.LINE_AA)
        y += row_h


def draw(pic, view: GuideView) -> None:
    """Draw the guide on the camera picture (modifies pic)."""
    bottom = _banner(pic, view)
    _countdown(pic, view)
    if view.done:
        _finished(pic, view, bottom)
