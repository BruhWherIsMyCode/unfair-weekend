"""The client's OpenCV window: the camera picture on the left, a side panel in plain words on the right.

Panel sections: CONNECTION, NOW SAYING (the line, wrapped, big), WHAT I SEE (the hint), TIMER, and at the end of a
call the RESULT card (status in plain words + the family text). While the server waits for an answer the panel shows
the answer box. The window is resizable (WINDOW_NORMAL | WINDOW_KEEPRATIO). One cv2.putText per string.

Keys: SPACE = start a call, q = hang up / quit. While the answer box is open, keystrokes fill it (Enter sends,
Esc = no answer, Backspace deletes). `poll_key()` returns an event the main loop passes to the state machine.

STATUS: written, never run in the build session (it needs a display).
"""
from __future__ import annotations

from typing import Any, List, Optional, Tuple

import cv2
import numpy as np

from analysis.pc.client_state import View, wrap_text

WINDOW_NAME = "Check-in (PC client)"
PANEL_W = 460
CAM_H = 540
FONT = cv2.FONT_HERSHEY_SIMPLEX
BG = (40, 40, 40)
WHITE = (240, 240, 240)
GREY = (160, 160, 160)
GREEN = (120, 220, 120)
AMBER = (80, 190, 255)


class Window:
    def __init__(self) -> None:
        cv2.namedWindow(WINDOW_NAME, cv2.WINDOW_NORMAL | cv2.WINDOW_KEEPRATIO)

    def closed(self) -> bool:
        try:
            return cv2.getWindowProperty(WINDOW_NAME, cv2.WND_PROP_VISIBLE) < 1
        except cv2.error:
            return True

    def close(self) -> None:
        cv2.destroyAllWindows()

    @staticmethod
    def _text(img: Any, text: str, x: int, y: int, scale: float, color: Tuple[int, int, int], thick: int = 1) -> None:
        cv2.putText(img, text, (x, y), FONT, scale, color, thick, cv2.LINE_AA)

    def _block(self, img: Any, lines: List[str], x: int, y: int, scale: float, color: Tuple[int, int, int],
               step: int, thick: int = 1) -> int:
        for line in lines:
            self._text(img, line, x, y, scale, color, thick)
            y += step
        return y

    def draw(self, frame: Optional[Any], view: View) -> None:
        if frame is not None:
            h, w = frame.shape[:2]
            cam = cv2.resize(frame, (max(1, int(w * CAM_H / h)), CAM_H))
        else:
            cam = np.full((CAM_H, 640, 3), 30, dtype=np.uint8)
            self._text(cam, "no camera picture", 20, CAM_H // 2, 0.9, GREY)
        panel = np.full((CAM_H, PANEL_W, 3), BG, dtype=np.uint8)
        x, y = 16, 30
        self._text(panel, "CONNECTION", x, y, 0.5, GREY)
        y = self._block(panel, wrap_text(view.connection, 36), x, y + 24, 0.6, WHITE, 24)
        y += 14
        self._text(panel, "NOW SAYING", x, y, 0.5, GREY)
        y = self._block(panel, wrap_text(view.saying or "-", 24)[:6], x, y + 34, 0.9, WHITE, 34, 2)
        y += 8
        self._text(panel, "WHAT I SEE", x, y, 0.5, GREY)
        hint_color = GREEN if view.hint in ("going fine", "") else AMBER
        y = self._block(panel, wrap_text(view.hint or "-", 32)[:2], x, y + 28, 0.7, hint_color, 28)
        y += 8
        self._text(panel, "TIMER", x, y, 0.5, GREY)
        y = self._block(panel, [view.timer or "-"], x, y + 28, 0.7, WHITE, 28)
        y += 8
        if view.answer_prompt is not None:
            y = self._block(panel, wrap_text(view.answer_prompt, 38), x, y + 8, 0.5, AMBER, 20)
            self._text(panel, "> " + view.answer_text[-34:] + "_", x, y + 10, 0.6, WHITE)
        elif view.result_title is not None:
            self._text(panel, "RESULT", x, y, 0.5, GREY)
            y = self._block(panel, wrap_text(view.result_title, 34), x, y + 28, 0.7, GREEN, 26, 2)
            self._block(panel, wrap_text(view.result_text or "", 44)[:8], x, y + 6, 0.5, WHITE, 20)
        canvas = np.hstack([cam, panel])
        cv2.imshow(WINDOW_NAME, canvas)

    def poll_key(self, waiting_for_answer: bool) -> Optional[str]:
        """One key event, or None: 'start', 'quit', 'enter', 'escape', 'backspace', or the typed character."""
        raw = cv2.waitKey(1)
        if raw == -1:
            return None
        key = raw & 0xFF
        if waiting_for_answer:
            if key == 13:
                return "enter"
            if key == 27:
                return "escape"
            if key in (8, 127):
                return "backspace"
            if 32 <= key <= 126:
                return chr(key)
            return None
        if key == ord(" "):
            return "start"
        if key in (ord("q"), ord("Q")):
            return "quit"
        return None
