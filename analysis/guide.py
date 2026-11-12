"""The step machine that runs a guided test by itself. Pure Python: no cv2, no mediapipe.

Feed it once per frame:  view = guide.update(now_s, Signal(...)).
The caller collects frame readings while view.collect is True. When
view.finished_measuring is True the caller works out the clip result and
calls guide.trial_finished(usable, reason). The guide never imports any
measuring code.

Phases of one trial: WAIT (until ready, continuously), the measured phases
(face: RELAX then SMILE; hand: GET READY then HOLD), RESULT, and for the hand
test LOWER. Before the first trial a START card, before each block an INTRO
card, at the end DONE.

"left"/"right" are the person's own left/right ("your" in the texts).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from analysis import config

START, INTRO, WAIT, MEASURE, RESULT, LOWER, DONE = (
    "START", "INTRO", "WAIT", "MEASURE", "RESULT", "LOWER", "DONE")

START_TEXT = "This test runs by itself. Follow the big text. Press Q to stop."
START_DETAIL = "Sit an arm's length from the camera, light in front of you."
FACE_HINT = "I can't see your face clearly. Sit about an arm's length away, light in front of you."
WRONG_SIDE_HINT = ("I see your other hand. If you ARE raising your %s hand, "
                   "close this and start again with --mirrored")


@dataclass(frozen=True)
class Trial:
    test: str  # "face" or "hand"
    label: str  # face: normal / acted_left / acted_right; hand: normal / weak
    instruction: str
    detail: str
    block: str = ""  # block title, e.g. "Part 2 of 3: droop on your LEFT side"
    side: Optional[str] = None  # hand test: "left" or "right"


@dataclass
class Signal:
    """What the caller saw in this frame."""
    ready: bool  # face test: face usable; hand test: the expected hand is usable
    hand_seen_any: bool = False  # hand test: any hand in the picture
    wrong_side: bool = False  # hand test: a hand is there but on the other side
    face_ok: Optional[bool] = None  # for the start card; None means "same as ready"


@dataclass(frozen=True)
class GuideView:
    instruction: str
    detail: str
    countdown: Optional[str]  # "SMILE 2.1" or None
    progress: str  # "Trial 7 of 20" or ""
    phase: str  # START INTRO WAIT RELAX SMILE "GET READY" HOLD RESULT LOWER DONE
    color: str  # "yellow" while waiting, "green" while measuring, "white" for a result
    collect: bool = False  # the caller should store this frame's reading
    finished_measuring: bool = False  # the caller should now call trial_finished
    done: bool = False
    block: str = ""
    summary_lines: Tuple[str, ...] = ()


# ---- plans ----------------------------------------------------------------------------

def face_plan(per_label: Optional[int] = None) -> List[Trial]:
    n = config.GUIDE_FACE_PER_LABEL if per_label is None else per_label
    blocks = [
        ("normal", "Part 1 of 3: normal smiles",
         "Look at the camera. When it says SMILE, give a big normal smile.",
         "Relax your face first, then smile when the text says SMILE."),
        ("acted_left", "Part 2 of 3: droop on your LEFT side",
         "Now ACT A DROOP on your LEFT side: smile, but keep your LEFT mouth corner down.",
         "Do it when the text says SMILE. Your right side smiles as usual."),
        ("acted_right", "Part 3 of 3: droop on your RIGHT side",
         "Now ACT A DROOP on your RIGHT side: smile, but keep your RIGHT mouth corner down.",
         "Do it when the text says SMILE. Your left side smiles as usual."),
    ]
    return [Trial("face", label, text, detail, block) for label, block, text, detail in blocks
            for _ in range(n)]


def hand_plan(per_mode: Optional[int] = None) -> List[Trial]:
    n = config.GUIDE_HAND_PER_MODE if per_mode is None else per_mode
    plan: List[Trial] = []
    part = 0
    for side in ("left", "right"):
        word = side.upper()
        for mode in ("normal", "weak"):
            part += 1
            if mode == "normal":
                text = ("Hold your %s hand up next to your face, palm to the camera. "
                        "Keep it still." % word)
                detail = "Keep it up until the countdown ends."
                block = "Part %d of 4: %s hand, normal" % (part, word)
            else:
                text = ("Hold your %s hand up next to your face, then ACT WEAK: "
                        "let it slowly sink and turn the palm in." % word)
                detail = "Start with the hand up, then let it sink during the countdown."
                block = "Part %d of 4: %s hand, acted weak" % (part, word)
            plan.extend(Trial("hand", mode, text, detail, block, side) for _ in range(n))
    return plan


# ---- plain-word helpers -----------------------------------------------------------------

_REASON_WORDS = (
    ("too few usable frames", "I could not see you clearly enough"),
    ("no clear smile", "I did not see a clear smile"),
    ("hand not in the picture", "I did not see your hand"),
    ("hand not seen steadily", "I did not see your hand steadily"),
    ("hand dropped out of view", "your hand dropped out of view"),
)


def plain_reason(reason: Optional[str]) -> str:
    """Turn a clip reason string into a short plain phrase."""
    first = (reason or "").split(";")[0].strip()
    if not first:
        return "not enough clear frames"
    for key, words in _REASON_WORDS:
        if first.startswith(key):
            return words
    return re.sub(r"\s*\(.*$", "", first) or "not enough clear frames"


def verdict_lines(text: str, test: str) -> List[str]:
    """Pick the plain verdict lines out of a format_summary text (verdicts first)."""
    out: List[str] = []
    metric = ""
    for raw in text.splitlines():
        line = " ".join(raw.split())
        if not line:
            continue
        if test == "face":
            m = re.match(r"== (\w+)", line)
            if m:
                metric = m.group(1)
            elif line.startswith("acted_left") or line.startswith("acted_right"):
                out.append("%s: %s" % (metric, line))
            elif line.startswith("best metric"):
                out.append(line)
        else:
            if line.startswith("her ") and " hand:" in line:
                out.append(line)
    return out


# ---- the machine ------------------------------------------------------------------------

class Guide:
    def __init__(self, plan: Sequence[Trial], test: str, *,
                 settle_s: Optional[float] = None, hold_s: Optional[float] = None):
        self.plan = list(plan)
        self.test = test
        if test == "face":
            self.settle_s = config.RELAX_SECONDS if settle_s is None else settle_s
            self.hold_s = config.SMILE_SECONDS if hold_s is None else hold_s
            self.names = ("RELAX", "SMILE")
        else:
            self.settle_s = config.HAND_SETTLE_S if settle_s is None else settle_s
            self.hold_s = config.HAND_HOLD_S if hold_s is None else hold_s
            self.names = ("GET READY", "HOLD")
        self.idx = 0
        self.retries = 0
        self.state = START
        self.notes: List[str] = []  # e.g. skipped trials
        self.outcomes: List[Tuple[int, bool, str]] = []  # (plan index, usable, reason)
        self.summary_lines: Tuple[str, ...] = ()  # the caller sets this when done
        self.measure_start_s: Optional[float] = None
        self._started: Optional[float] = None
        self._last: Optional[float] = None
        self._now = 0.0
        self._seen_since: Optional[float] = None
        self._since = 0.0  # when the current state began
        self._ready_since: Optional[float] = None
        self._wrong_time = 0.0
        self._next_idx = 0
        self._message = ""
        self._good = False
        self._no_hand_since: Optional[float] = None
        self._intro_shown: set = set()

    # -- accessors for the caller
    @property
    def current(self) -> Optional[Trial]:
        return self.plan[self.idx] if 0 <= self.idx < len(self.plan) else None

    @property
    def hold_start_s(self) -> Optional[float]:
        return None if self.measure_start_s is None else self.measure_start_s + self.settle_s

    @property
    def hold_end_s(self) -> Optional[float]:
        return None if self.measure_start_s is None else self.measure_start_s + self.settle_s + self.hold_s

    # -- transitions
    def _set(self, state: str, now: float) -> None:
        self.state = state
        self._since = now

    def _begin_trial(self, now: float) -> None:
        if self.idx >= len(self.plan):
            self._set(DONE, now)
            return
        t = self.plan[self.idx]
        first_of_block = self.idx == 0 or self.plan[self.idx - 1].block != t.block
        if t.block and first_of_block and self.idx not in self._intro_shown:
            self._intro_shown.add(self.idx)
            self._set(INTRO, now)
        else:
            self._enter_wait(now)

    def _enter_wait(self, now: float) -> None:
        self._set(WAIT, now)
        self._ready_since = None
        self._wrong_time = 0.0

    def trial_finished(self, usable: bool, reason: str = "") -> None:
        """The caller's verdict on the clip. Ignored unless a trial was being measured."""
        if self.state != MEASURE:
            return
        now = self._now
        self.outcomes.append((self.idx, bool(usable), reason or ""))
        why = plain_reason(reason)
        if usable:
            self._message, self._good = "Got it.", True
            self._next_idx = self.idx + 1
            self.retries = 0
        elif self.retries < config.GUIDE_MAX_RETRIES:
            self.retries += 1
            self._message = "That one did not count: %s. Once more." % why
            self._good = False
            self._next_idx = self.idx
        else:
            self._message = "That one did not count: %s. Skipping it." % why
            self._good = False
            self.notes.append("Trial %d skipped: %s" % (self.idx + 1, why))
            self._next_idx = self.idx + 1
            self.retries = 0
        self._set(RESULT, now)

    def _advance(self, now: float) -> None:
        self.idx = self._next_idx
        self._begin_trial(now)

    # -- the per-frame call
    def update(self, now_s: float, signal: Signal) -> GuideView:
        now = float(now_s)
        dt = 0.0 if self._last is None else max(0.0, now - self._last)
        self._last = now
        self._now = now
        if self._started is None:
            self._started = now
            self._since = now
        face_ok = signal.ready if signal.face_ok is None else signal.face_ok

        if self.state == START:
            if face_ok:
                if self._seen_since is None:
                    self._seen_since = now
            else:
                self._seen_since = None
            if (now - self._started >= config.GUIDE_START_MIN_S and self._seen_since is not None
                    and now - self._seen_since >= config.GUIDE_START_FACE_S):
                self.idx = 0
                self._begin_trial(now)
        elif self.state == INTRO:
            if now - self._since >= config.GUIDE_INTRO_S:
                self._enter_wait(now)
        elif self.state == WAIT:
            if signal.ready:
                if self._ready_since is None:
                    self._ready_since = now
                if now - self._ready_since >= config.GUIDE_READY_S:
                    self.measure_start_s = now
                    self._set(MEASURE, now)
            else:
                self._ready_since = None
                if signal.wrong_side:
                    self._wrong_time += dt
        elif self.state == RESULT:
            if now - self._since >= config.GUIDE_RESULT_S:
                if self.test == "hand" and self._next_idx < len(self.plan):
                    self._no_hand_since = None
                    self._set(LOWER, now)
                else:
                    self._advance(now)
        elif self.state == LOWER:
            if signal.hand_seen_any:
                self._no_hand_since = None
            elif self._no_hand_since is None:
                self._no_hand_since = now
            gone = (self._no_hand_since is not None
                    and now - self._no_hand_since >= config.GUIDE_LOWER_NO_HAND_S)
            if gone or now - self._since >= config.GUIDE_LOWER_MAX_S:
                self._advance(now)
        return self._view(now)

    # -- the view
    def _progress(self) -> str:
        return "Trial %d of %d" % (self.idx + 1, len(self.plan)) if self.plan else ""

    def _hint(self, now: float) -> Optional[str]:
        t = self.current
        if t is None or now - self._since < config.GUIDE_HINT_AFTER_S:
            return None
        if t.test == "face":
            return FACE_HINT
        word = (t.side or "left").upper()
        waited = now - self._since
        if self._wrong_time > waited / 2.0:
            return WRONG_SIDE_HINT % word
        return "I can't see your %s hand. Hold it up beside your face, palm to the camera." % word

    def _view(self, now: float) -> GuideView:
        s = self.state
        t = self.current
        block = t.block if t is not None else ""
        progress = self._progress()
        if s == START:
            return GuideView(START_TEXT, START_DETAIL, None, "", START, "yellow", block=block)
        if s == INTRO:
            return GuideView(t.block, "Get ready. It starts in a moment.", None, progress,
                             INTRO, "yellow", block=block)
        if s == WAIT:
            detail = self._hint(now) or t.detail
            return GuideView(t.instruction, detail, None, progress, WAIT, "yellow", block=block)
        if s == MEASURE:
            elapsed = now - (self.measure_start_s if self.measure_start_s is not None else now)
            total = self.settle_s + self.hold_s
            if elapsed >= total:
                return GuideView(t.instruction, t.detail, None, progress, self.names[1], "green",
                                 finished_measuring=True, block=block)
            if elapsed < self.settle_s:
                name, left = self.names[0], self.settle_s - elapsed
            else:
                name, left = self.names[1], total - elapsed
            return GuideView(t.instruction, t.detail, "%s %.1f" % (name, left), progress, name,
                             "green", collect=True, block=block)
        if s == RESULT:
            return GuideView(self._message, "", None, progress, RESULT, "white", block=block)
        if s == LOWER:
            return GuideView("Lower your hand", "", None, progress, LOWER, "yellow", block=block)
        return GuideView("Finished. Results below.", "", None, "Done", DONE, "white", done=True,
                         block="", summary_lines=tuple(self.summary_lines))
