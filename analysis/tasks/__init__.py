"""Task framework: run_task(task, clip_path, person, attempt, ...) -> result dict.

Every result has the section-6 shape of docs/AGENT_3_CONNECTOR.md plus a
`status`: as_usual / different_from_usual / unusable / learning.
"""
from __future__ import annotations

from typing import Dict, Optional, Tuple

from analysis.tasks import hand, smile

TASKS = {
    "smile": smile.run,
    "hand_left": hand.run,
    "hand_right": hand.run,
}


def run_task_with_values(task: str, clip_path: str, person: Optional[str] = None, attempt: int = 1,
                         mirrored: bool = False, hold=None) -> Tuple[dict, Dict[str, Optional[float]]]:
    """Like run_task, but also returns the values a --save would store."""
    if task not in TASKS:
        raise ValueError("unknown task %r (known: %s)" % (task, ", ".join(sorted(TASKS))))
    return TASKS[task](task, clip_path, person, attempt, mirrored, hold)


def run_task(task: str, clip_path: str, person: Optional[str] = None, attempt: int = 1,
             mirrored: bool = False, hold=None) -> dict:
    """`person` is a name (baseline file analysis/data/NAME.json) or None. `hold` = (start_ms, end_ms)."""
    return run_task_with_values(task, clip_path, person, attempt, mirrored, hold)[0]
