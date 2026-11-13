"""The baseline store of the server: one JSON file per person, `analysis/data/<person>_calls.json`.

After each finished call the values `CallSession.baseline_update()` returns are appended (nothing when it returns
nothing: the existing eligibility rules decide, and a call labelled "acted" never stores). Loading gives the per-task
baseline dicts a CallSession expects:

    smile  make_baseline over the stored corner_height values (+ snapshot_id)
    arms   {"snapshot_id", "sessions", "needed", "ready", "hand_left": make_baseline, "hand_right": make_baseline}
           (ready only when both hands are), as in v6.

The snapshot id includes how many values the baseline was built from, so it changes when the baseline does and is
frozen for the whole call (loaded once at call start).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from analysis import baseline as baseline_mod
from analysis import config
from analysis.session import limits
from analysis.tasks.stream_smile import METRIC_ID as SMILE_METRIC_ID

HAND_KEYS = limits.ARMS_BASELINE_KEYS  # {"left": "arms.hand_left.hand_sink", "right": ...}


def calls_path(person: str, data_dir: Optional[Path] = None) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in person)
    return Path(data_dir or config.DATA_DIR) / ("%s_calls.json" % safe)


class BaselineStore:
    def __init__(self, person: str, data_dir: Optional[Path] = None) -> None:
        self.person = person
        self.path = calls_path(person, data_dir)

    def append(self, label: str, values: Dict[str, float]) -> bool:
        """Append one finished call's baseline values. False when there was nothing to store."""
        if label == "acted" or not values:
            return False
        baseline_mod.add_session(self.path, {k: float(v) for k, v in values.items()}, label)
        return True

    def load_baselines(self) -> Dict[str, Dict[str, Any]]:
        person = baseline_mod.load_person(self.path)
        smile_values = baseline_mod.baseline_values(person, SMILE_METRIC_ID)
        smile = baseline_mod.make_baseline(smile_values, metric="corner_height")
        smile["snapshot_id"] = "baseline_%s_smile_%d" % (self._safe(), len(smile_values))

        hands: Dict[str, Dict[str, Any]] = {}
        counts = []
        for side, key in HAND_KEYS.items():
            values = baseline_mod.baseline_values(person, key)
            hands[side] = baseline_mod.make_baseline(values, metric="hand_sink")
            counts.append(len(values))
        arms: Dict[str, Any] = {
            "snapshot_id": "baseline_%s_arms_%d_%d" % (self._safe(), counts[0], counts[1]),
            "sessions": min(counts), "needed": config.BASELINE_NEEDED,
            "ready": all(h["ready"] for h in hands.values()),
            "hand_left": hands["left"], "hand_right": hands["right"]}
        return {"smile": smile, "arms": arms}

    def _safe(self) -> str:
        return "".join(c if c.isalnum() or c in "-_" else "_" for c in self.person)
