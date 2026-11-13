"""A person's usual range from past values, comparison, and a small JSON store.

Pure Python (json and files only). Conventions: positive = her LEFT side is
lower or weaker, so a value above the usual range means side "left" and a
value below it means side "right". Always HER left and right.

Sessions labelled "acted" are stored but never enter the baseline; only
"normal" and "live" do.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from statistics import median
from typing import Dict, List, Optional, Sequence

from analysis import config

VALID_LABELS = ("normal", "live", "acted")


def make_baseline(
    values: Sequence[float],
    k: float = config.BASELINE_K,
    min_half_width: Optional[float] = None,
    needed: int = config.BASELINE_NEEDED,
    metric: Optional[str] = None,
) -> dict:
    """Usual range = median +/- max(k x robust spread, min_half_width).

    Fewer than `needed` values: not ready, status "learning", no range.
    """
    vals = [float(v) for v in values if v is not None]
    n = len(vals)
    if min_half_width is None:
        min_half_width = config.MIN_HALF_WIDTH.get(metric, config.DEFAULT_MIN_HALF_WIDTH)
    if n < needed:
        return {
            "ready": False,
            "status": "learning",
            "sessions": n,
            "needed": needed,
            "median": None,
            "spread": None,
            "usual_low": None,
            "usual_high": None,
        }
    med = median(vals)
    spread = median([abs(v - med) for v in vals]) * config.MAD_TO_SIGMA
    half = max(k * spread, min_half_width)
    return {
        "ready": True,
        "status": "ready",
        "sessions": n,
        "needed": needed,
        "median": med,
        "spread": spread,
        "usual_low": med - half,
        "usual_high": med + half,
    }


def compare(value: Optional[float], baseline: dict) -> dict:
    """Compare one value with a baseline. Never alerts while learning."""
    result = {
        "outside": False,
        "side": None,
        "outside_by": 0.0,
        "usual_low": baseline.get("usual_low"),
        "usual_high": baseline.get("usual_high"),
    }
    if value is None or not baseline.get("ready"):
        return result
    low, high = baseline["usual_low"], baseline["usual_high"]
    if value > high:
        result.update(outside=True, side="left", outside_by=value - high)
    elif value < low:
        result.update(outside=True, side="right", outside_by=low - value)
    return result


# ---- JSON store: one file per person -------------------------------------------------

def person_path(name: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)
    return config.DATA_DIR / ("%s.json" % safe)


def load_person(path) -> dict:
    p = Path(path)
    if not p.exists():
        return {"sessions": []}
    with open(p, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    data.setdefault("sessions", [])
    return data


def add_session(path, metric_values: Dict[str, Optional[float]], label: str) -> dict:
    """Append one session. Acted sessions are stored but excluded from the baseline."""
    if label not in VALID_LABELS:
        raise ValueError("label must be one of %s, got %r" % (", ".join(VALID_LABELS), label))
    p = Path(path)
    data = load_person(p)
    data["sessions"].append({
        "label": label,
        "recorded_at": datetime.now().isoformat(timespec="seconds"),
        "values": dict(metric_values),
    })
    if p.parent and not p.parent.exists():
        os.makedirs(str(p.parent), exist_ok=True)
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    return data


def baseline_values(person: dict, metric: str) -> List[float]:
    """Past values of one metric that may enter the baseline (never acted)."""
    out = []
    for s in person.get("sessions", []):
        if s.get("label") not in config.BASELINE_LABELS:
            continue
        v = s.get("values", {}).get(metric)
        if v is not None:
            out.append(float(v))
    return out


def baseline_for(person: dict, metric: str) -> dict:
    return make_baseline(baseline_values(person, metric), metric=metric)
