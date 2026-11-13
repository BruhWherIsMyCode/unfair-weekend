"""Measuring the spoken sentence: PURE functions, no I/O.

Claude cannot hear audio, so the speech-to-text word timings are turned into numbers:

    duration_s        first word start -> last word end
    longest_pause_s   the largest gap between two consecutive words
    words_per_min     heard words / duration
    word_error_rate   word-level edit distance between the expected and the heard words / expected word count
                      (both lowercased, punctuation removed)
    missing_words / extra_words / changed_words   what differs (at most 10 each)
    heard             the transcript (at most 200 characters)

The primary number for the comparison with her usual and for the safety floor is `duration_s` (metric
"speech_duration_v1"). Outside her usual means LONGER than her usual high only (slower); faster is never outside.
Every threshold is a first guess; nothing here is validated.
"""
from __future__ import annotations

import math
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

METRIC_ID = "speech_duration_v1"
UNIT = "s"
MAX_LIST = 10
HEARD_MAX_CHARS = 200


def normalise(text: str) -> List[str]:
    """Lowercase, punctuation removed, split into words."""
    cleaned = re.sub(r"[^\w\s]", "", str(text).lower().replace("_", " "))
    return cleaned.split()


def _align(expected: Sequence[str], heard: Sequence[str]) -> Tuple[int, List[str], List[str], List[Dict[str, str]]]:
    """Levenshtein over words. Returns (edits, missing, extra, changed)."""
    n, m = len(expected), len(heard)
    cost = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        cost[i][0] = i
    for j in range(m + 1):
        cost[0][j] = j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            same = 0 if expected[i - 1] == heard[j - 1] else 1
            cost[i][j] = min(cost[i - 1][j] + 1, cost[i][j - 1] + 1, cost[i - 1][j - 1] + same)
    missing: List[str] = []
    extra: List[str] = []
    changed: List[Dict[str, str]] = []
    i, j = n, m
    while i > 0 or j > 0:
        if i > 0 and j > 0 and cost[i][j] == cost[i - 1][j - 1] + (0 if expected[i - 1] == heard[j - 1] else 1):
            if expected[i - 1] != heard[j - 1]:
                changed.append({"expected": expected[i - 1], "heard": heard[j - 1]})
            i, j = i - 1, j - 1
        elif i > 0 and cost[i][j] == cost[i - 1][j] + 1:
            missing.append(expected[i - 1])
            i -= 1
        else:
            extra.append(heard[j - 1])
            j -= 1
    missing.reverse()
    extra.reverse()
    changed.reverse()
    return cost[n][m], missing, extra, changed


def _timed(words: Any) -> List[Dict[str, float]]:
    """Words with a usable start and end, in order."""
    out = []
    for w in words or []:
        if not isinstance(w, dict) or not isinstance(w.get("text"), str) or not w["text"].strip():
            continue
        start, end = w.get("start"), w.get("end")
        if (isinstance(start, (int, float)) and isinstance(end, (int, float)) and not isinstance(start, bool)
                and not isinstance(end, bool) and math.isfinite(start) and math.isfinite(end) and end >= start):
            out.append({"text": w["text"], "start": float(start), "end": float(end)})
    return out


def measure_sentence(expected_text: str, words: Any, transcript: Optional[str] = None) -> Dict[str, Any]:
    """The readings of one spoken attempt.

    Returns {"usable": bool, "reason": str|None, "readings": dict|None}. No words (or none with timings) is
    unusable with reason "nothing_heard"."""
    spoken = [w for w in (words or []) if isinstance(w, dict) and isinstance(w.get("text"), str) and w["text"].strip()]
    timed = _timed(spoken)
    if not spoken or not timed:
        return {"usable": False, "reason": "nothing_heard", "readings": None}
    heard_words = normalise(" ".join(w["text"] for w in spoken))
    if not heard_words:
        return {"usable": False, "reason": "nothing_heard", "readings": None}
    expected = normalise(expected_text)
    edits, missing, extra, changed = _align(expected, heard_words)
    duration = timed[-1]["end"] - timed[0]["start"]
    pause = max([max(0.0, b["start"] - a["end"]) for a, b in zip(timed, timed[1:])] or [0.0])
    wpm = (len(timed) / duration * 60.0) if duration > 0 else None
    heard = (transcript if isinstance(transcript, str) and transcript.strip()
             else " ".join(w["text"] for w in spoken)).strip()[:HEARD_MAX_CHARS]
    readings = {
        "duration_s": round(duration, 3),
        "longest_pause_s": round(pause, 3),
        "words_per_min": round(wpm, 1) if wpm is not None else None,
        "word_error_rate": round(edits / len(expected), 3) if expected else 0.0,
        "missing_words": missing[:MAX_LIST],
        "extra_words": extra[:MAX_LIST],
        "changed_words": changed[:MAX_LIST],
        "heard": heard,
    }
    return {"usable": True, "reason": None, "readings": readings}


def compare_duration(value: float, baseline: Dict[str, Any]) -> Dict[str, Any]:
    """Her usual sentence duration against today's. Outside = longer than her usual high only; never while learning.

    Returns {"usual_low", "usual_high", "outside"} with outside None while the baseline is not ready."""
    if not baseline.get("ready"):
        return {"usual_low": None, "usual_high": None, "outside": None}
    low, high = float(baseline["usual_low"]), float(baseline["usual_high"])
    return {"usual_low": low, "usual_high": high, "outside": bool(value > high)}
