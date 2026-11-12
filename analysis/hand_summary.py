"""Summary of a hand-test session: per hand, per reading, normal range vs acted-weak trials.

Pure Python. A LARGER value = more of a problem, so an acted weak hand should
give values ABOVE every normal value of the same hand.

Trials are dicts: {hand: "left"|"right", mode: "normal"|"weak", quality,
hand_sink, hand_height, palm_turn, hand_shake}. Only usable trials count.
"""
from __future__ import annotations

from statistics import median
from typing import Dict, List, Optional, Sequence

from analysis import config

HANDS = ("left", "right")
MODES = ("normal", "weak")


def _values(trials: Sequence[dict], hand: str, mode: str, reading: str) -> List[float]:
    out = []
    for t in trials:
        if t.get("hand") != hand or t.get("mode") != mode or t.get("quality") != "usable":
            continue
        v = t.get(reading)
        if v is not None:
            out.append(float(v))
    return out


def _stats(vals: List[float]) -> dict:
    if not vals:
        return {"count": 0, "median": None, "min": None, "max": None}
    return {"count": len(vals), "median": median(vals), "min": min(vals), "max": max(vals)}


def summarise(trials: Sequence[dict]) -> dict:
    hands: Dict[str, dict] = {}
    for hand in HANDS:
        readings: Dict[str, dict] = {}
        best: Optional[str] = None
        best_rel: Optional[float] = None
        for reading in config.HAND_READINGS:
            normal = _values(trials, hand, "normal", reading)
            weak = _values(trials, hand, "weak", reading)
            entry = {
                "normal": _stats(normal), "weak": _stats(weak), "weak_values": weak,
                "above": None, "count": len(weak), "gap": None, "separates": None,
            }
            if normal and weak:
                top = max(normal)
                entry["above"] = sum(1 for v in weak if v > top)
                entry["gap"] = min(weak) - top
                entry["separates"] = entry["above"] == len(weak)
                spread = max(max(normal) - min(normal), config.MIN_NORMAL_SPREAD)
                rel = entry["gap"] / spread
                if entry["separates"] and (best_rel is None or rel > best_rel):
                    best, best_rel = reading, rel
            readings[reading] = entry
        hands[hand] = {"readings": readings, "best": best,
                       "verdict": _verdict(hand, readings, best)}
    return {"hands": hands}


def _verdict(hand: str, readings: Dict[str, dict], best: Optional[str]) -> str:
    word = hand.upper()
    if best is not None:
        e = readings[best]
        return "her %s hand: acted weak separates on %s (%d of %d above normal)" % (
            word, best, e["above"], e["count"])
    if any(e["separates"] is not None for e in readings.values()):
        return "her %s hand: acted weak does not separate" % word
    return "her %s hand: not enough data (need normal and acted weak trials)" % word


def _f(v: Optional[float]) -> str:
    return "   -   " if v is None else "%+.3f" % v


def format_summary(summary: dict) -> str:
    lines: List[str] = []
    for hand in HANDS:
        h = summary["hands"][hand]
        lines.append("== her %s hand (larger = more of a problem) ==" % hand.upper())
        for reading in config.HAND_READINGS:
            e = h["readings"][reading]
            n, w = e["normal"], e["weak"]
            lines.append("%-12s normal n=%d min %s median %s max %s" % (
                reading, n["count"], _f(n["min"]), _f(n["median"]), _f(n["max"])))
            weak_txt = ", ".join("%+.3f" % v for v in e["weak_values"]) or "-"
            if e["above"] is None:
                lines.append("%-12s acted weak: %s" % ("", weak_txt))
            else:
                lines.append("%-12s acted weak: %s | %d of %d above the normal max"
                             % ("", weak_txt, e["above"], e["count"]))
        lines.append(h["verdict"])
        lines.append("")
    return "\n".join(lines)
