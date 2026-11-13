"""Summary of the angle test: does a crooked phone move a reading as much as a droop does?

Pure Python. Trials are dicts: {label, pose, quality, <readings>, yaw_deg, pitch_deg}
with label in normal / acted_left / acted_right and pose in straight / low / left / right.
Only usable trials count.

For each reading in config.METRICS:
- angle shift = the largest |median(normal at pose) - median(normal, straight)| over low/left/right
- droop size  = the smaller of |median(acted left, straight) - median(normal, straight)|
                and |median(acted right, straight) - median(normal, straight)|
- ratio = angle shift / droop size
  < config.ANGLE_RATIO_OK       -> "angle OK"
  <= config.ANGLE_RATIO_RISKY   -> "angle RISKY"
  above                         -> "angle BREAKS IT"
"""
from __future__ import annotations

from statistics import median
from typing import Dict, List, Optional, Sequence

from analysis import config

POSES = ("straight", "low", "left", "right")
TILTED_POSES = ("low", "left", "right")


def _num(v) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _values(trials: Sequence[dict], label: str, pose: str, key: str) -> List[float]:
    out = []
    for t in trials:
        if t.get("quality") != "usable" or t.get("label") != label or t.get("pose") != pose:
            continue
        v = _num(t.get(key))
        if v is not None:
            out.append(v)
    return out


def _med(vals: List[float]) -> Optional[float]:
    return median(vals) if vals else None


def verdict_for(ratio: Optional[float]) -> str:
    if ratio is None:
        return "no data"
    if ratio < config.ANGLE_RATIO_OK:
        return "angle OK"
    if ratio <= config.ANGLE_RATIO_RISKY:
        return "angle RISKY"
    return "angle BREAKS IT"


def summarise(trials: Sequence[dict]) -> dict:
    metrics: Dict[str, dict] = {}
    for metric in config.METRICS:
        straight = _med(_values(trials, "normal", "straight", metric))
        shift = None
        if straight is not None:
            for pose in TILTED_POSES:
                m = _med(_values(trials, "normal", pose, metric))
                if m is not None:
                    d = abs(m - straight)
                    shift = d if shift is None else max(shift, d)
        droops = []
        if straight is not None:
            for lab in ("acted_left", "acted_right"):
                m = _med(_values(trials, lab, "straight", metric))
                if m is not None:
                    droops.append(abs(m - straight))
        droop = min(droops) if len(droops) == 2 else None
        ratio = None
        if shift is not None and droop is not None and droop > 1e-9:
            ratio = shift / droop
        metrics[metric] = {"angle_shift": shift, "droop_size": droop, "ratio": ratio,
                           "verdict": verdict_for(ratio)}

    poses: Dict[str, dict] = {}
    for pose in POSES:
        yaws: List[float] = []
        pitches: List[float] = []
        count = 0
        for t in trials:
            if t.get("quality") != "usable" or t.get("pose") != pose:
                continue
            count += 1
            y, p = _num(t.get("yaw_deg")), _num(t.get("pitch_deg"))
            if y is not None:
                yaws.append(y)
            if p is not None:
                pitches.append(p)
        poses[pose] = {"count": count, "yaw": _med(yaws), "pitch": _med(pitches)}

    best, best_ratio = None, None
    for metric, m in metrics.items():
        r = m["ratio"]
        if r is not None and (best_ratio is None or r < best_ratio):
            best, best_ratio = metric, r
    return {"metrics": metrics, "poses": poses, "best_metric": best, "best_ratio": best_ratio}


def _deg(v: Optional[float]) -> str:
    return "?" if v is None else "%+.0f" % v


def format_lines(summary: dict) -> List[str]:
    """Plain verdict lines, one per reading, then the pose angles, then the best reading."""
    lines: List[str] = []
    for metric, m in summary["metrics"].items():
        if m["ratio"] is None:
            lines.append("%s: no data" % metric)
        else:
            lines.append("%s: %s (a crooked phone moves it %.2f of a droop: shift %.3f, droop %.3f)"
                         % (metric, m["verdict"], m["ratio"], m["angle_shift"], m["droop_size"]))
    for pose in POSES:
        p = summary["poses"][pose]
        if p["count"]:
            lines.append("pose %s: head turn %s deg, tilt %s deg (%d trials)"
                         % (pose, _deg(p["yaw"]), _deg(p["pitch"]), p["count"]))
    if summary["best_metric"]:
        lines.append("Best against angle: %s (a crooked phone moves it %d%% as much as a droop)."
                     % (summary["best_metric"], int(round(summary["best_ratio"] * 100))))
    else:
        lines.append("Best against angle: not enough data.")
    return lines


def format_summary(summary: dict) -> str:
    return "\n".join(format_lines(summary))
