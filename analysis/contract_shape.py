"""Build the tool result dict in the shape of docs/AGENT_3_CONNECTOR.md section 6.

Pure Python. `side` is always HER left or right. While the baseline is
learning, no measured alert is possible (outside False, side None). An
unusable clip carries a reason and no reading, so it is never read as
"as usual".
"""
from __future__ import annotations

from typing import Optional

from analysis import baseline as baseline_mod


def tool_result(task: str, attempt: int, clip_reading, primary_metric: str, baseline: dict,
                sign: str = "face", one_sided_side: Optional[str] = None) -> dict:
    """`baseline` is the dict from baseline.make_baseline for the primary metric.

    Defaults give the smile result. With `one_sided_side` ("left"/"right") only a
    value ABOVE the usual range counts as outside, and `side` is that side; a
    value below the range is as usual (used by the hand tasks).
    """
    result = {
        "task": task,
        "attempt": attempt,
        "quality": clip_reading.quality,
    }
    baseline_info = {
        "sessions": baseline.get("sessions", 0),
        "needed": baseline.get("needed"),
        "ready": bool(baseline.get("ready")),
    }
    all_readings = clip_reading.readings()

    if clip_reading.quality != "usable":
        result["reason"] = "; ".join(clip_reading.reasons) or "unusable"
        result["reading"] = None
        result["baseline"] = baseline_info
        result["all_readings"] = {k: None for k in all_readings}
        return result

    value = all_readings.get(primary_metric)
    if value is None:
        result["quality"] = "unusable"
        result["reason"] = "reading %s not available" % primary_metric
        result["reading"] = None
        result["baseline"] = baseline_info
        result["all_readings"] = {k: None for k in all_readings}
        return result

    cmp = baseline_mod.compare(value, baseline)
    if one_sided_side is not None:
        if cmp["outside"] and cmp["side"] == "left":  # compare() calls "above the range" side "left"
            cmp["side"] = one_sided_side
        else:  # below the range (or inside) is as usual
            cmp.update(outside=False, side=None, outside_by=0.0)
    result["reading"] = {
        "sign": sign,
        "value": value,
        "usual_low": cmp["usual_low"],
        "usual_high": cmp["usual_high"],
        "outside": cmp["outside"],
        "side": cmp["side"],
        "outside_by": cmp["outside_by"],
    }
    result["baseline"] = baseline_info
    result["all_readings"] = all_readings
    return result
