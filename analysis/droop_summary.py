"""Summary of a droop-test session: per-label stats and a separation verdict.

Pure Python. Conventions: positive = her LEFT side is lower or weaker. So an
acted LEFT droop should give values ABOVE every normal value, and an acted
RIGHT droop values BELOW every normal value.

Trials are dicts: {label, corner_height, corner_lift, smile_score, quality}
with label in normal / acted_left / acted_right. Only usable trials count.
"""
from __future__ import annotations

from statistics import median
from typing import Dict, List, Optional, Sequence

from analysis import config

LABELS = ("normal", "acted_left", "acted_right")


def _values(trials: Sequence[dict], label: str, metric: str) -> List[float]:
    out = []
    for t in trials:
        if t.get("label") != label or t.get("quality") != "usable":
            continue
        v = t.get(metric)
        if v is not None:
            out.append(float(v))
    return out


def _stats(vals: List[float]) -> dict:
    if not vals:
        return {"count": 0, "median": None, "min": None, "max": None}
    return {"count": len(vals), "median": median(vals), "min": min(vals), "max": max(vals)}


def summarise(trials: Sequence[dict]) -> dict:
    metrics: Dict[str, dict] = {}
    for metric in config.METRICS:
        per_label = {lab: _values(trials, lab, metric) for lab in LABELS}
        stats = {lab: _stats(v) for lab, v in per_label.items()}
        normal = per_label["normal"]
        normal_spread = (max(normal) - min(normal)) if normal else None

        verdicts: Dict[str, dict] = {}
        gaps: List[float] = []
        for lab in ("acted_left", "acted_right"):
            acted = per_label[lab]
            if not acted or not normal:
                verdicts[lab] = {"verdict": "no data", "wrong_side": None, "count": len(acted)}
                continue
            if lab == "acted_left":
                wrong = sum(1 for v in acted if not v > max(normal))
                gap = min(acted) - max(normal)
            else:
                wrong = sum(1 for v in acted if not v < min(normal))
                gap = min(normal) - max(acted)
            gaps.append(gap)
            verdicts[lab] = {
                "verdict": "separates" if wrong == 0 else "overlaps",
                "wrong_side": wrong,
                "count": len(acted),
                "gap": gap,
            }

        relative = None
        if gaps and normal_spread is not None:
            relative = min(gaps) / max(normal_spread, config.MIN_NORMAL_SPREAD)
        metrics[metric] = {
            "labels": stats,
            "normal_spread": normal_spread,
            "verdicts": verdicts,
            "relative_gap": relative,
        }

    best = None
    best_rel = None
    for metric, m in metrics.items():
        rel = m["relative_gap"]
        if rel is not None and (best_rel is None or rel > best_rel):
            best, best_rel = metric, rel
    return {"metrics": metrics, "best_metric": best, "best_relative_gap": best_rel}


def _f(v: Optional[float]) -> str:
    return "   -   " if v is None else "%+.3f" % v


def format_summary(summary: dict) -> str:
    lines: List[str] = []
    for metric, m in summary["metrics"].items():
        lines.append("== %s (positive = her LEFT lower/weaker) ==" % metric)
        lines.append("%-12s %5s %8s %8s %8s" % ("label", "n", "median", "min", "max"))
        for lab in LABELS:
            s = m["labels"][lab]
            lines.append("%-12s %5d %8s %8s %8s"
                         % (lab, s["count"], _f(s["median"]), _f(s["min"]), _f(s["max"])))
        lines.append("normal spread (max-min): %s" % _f(m["normal_spread"]).strip())
        for lab in ("acted_left", "acted_right"):
            v = m["verdicts"][lab]
            extra = ""
            if v["wrong_side"] is not None:
                extra = " (%d of %d trials not beyond normal on the correct side)" % (
                    v["wrong_side"], v["count"])
            lines.append("%-12s %s%s" % (lab, v["verdict"], extra))
        lines.append("")
    if summary["best_metric"]:
        lines.append("best metric: %s (smallest acted-vs-normal gap = %.2f x normal spread)"
                     % (summary["best_metric"], summary["best_relative_gap"]))
    else:
        lines.append("best metric: not enough data")
    return "\n".join(lines)
