"""Versioned policy and task definitions (tables `policy_definitions`, `task_definitions`), published once.

A definition row is built from what the code already says about itself: the policy from `analysis.session.limits`,
a task from its module's `definition()`. Their versions carry a short fingerprint of the content, so changing a
tunable or the arms phases publishes a NEW version (her usual for the changed feature starts again) instead of
colliding with the old one. Publishing the same content twice is a no-op; different content under one version is a
ConflictError (never an update)."""
from __future__ import annotations

import json
import sqlite3
from typing import Any, Dict, Optional, Tuple

from analysis.session import limits
from analysis.storage.util import ConflictError, dumps, fingerprint

SCRIPT_VERSION = "script.en"  # contract/script.en.json

# How each task's DB feature is built (documentation inside definition_json; no thresholds are invented here).
_IMPLEMENTATION = {
    "smile": {"implementation": "analysis.tasks.stream_smile.final_result", "baseline_kind": "single_range",
              "aggregation": "one final reading per attempt; usual range from the median of past readings"},
    "arms": {"implementation": "analysis.tasks.stream_arms.final_result", "baseline_kind": "per_hand_ranges",
             "aggregation": "one reading per attempt (the hand furthest above its own usual); the usual is one "
                            "range per hand, kept outside the schema's single-range snapshot (see README)"},
    "sentence": {"implementation": "analysis.tasks.stream_speech", "baseline_kind": "single_range",
                 "aggregation": "sentence duration in seconds"},
}


def policy_definition() -> Tuple[str, Dict[str, Any]]:
    """(version, definition): every numeric/text tunable of `limits`, versioned by content."""
    values: Dict[str, Any] = {}
    for name in dir(limits):
        if not name.isupper():
            continue
        try:
            values[name] = json.loads(json.dumps(getattr(limits, name)))
        except (TypeError, ValueError):
            continue
    return "policy_%s" % fingerprint(values, 10), {"limits": values, "source": "analysis/session/limits.py"}


def task_rows(modules: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """task -> definition row for every module that has a metric (a stub without metric has nothing to measure)."""
    rows: Dict[str, Dict[str, Any]] = {}
    for task, module in (modules or {}).items():
        if task not in limits.TASKS:
            continue
        try:
            d = module.definition()
            metric_id, unit = d.get("metric_id"), d.get("metric_unit")
            if not metric_id or not unit:
                continue
            body = json.loads(json.dumps(d))
            body.update(_IMPLEMENTATION[task])
            for attr in ("phases", "period_ms"):  # the feature layout is part of the formula (arms: hold windows)
                value = getattr(module, attr, None)
                if value is not None:
                    body[attr] = json.loads(json.dumps(value))
            version = "%s.%s" % (metric_id, fingerprint(body))
            rows[task] = {
                "definition_id": "%s-%s" % (task, version), "task": task, "version": version,
                "metric_id": str(metric_id), "unit": str(unit),
                "instruction_line_id": str(d["instruction_line"]), "repeat_line_id": str(d["repeat_line"]),
                "required_resource": str(d["required_resource"]), "window_ms": int(d["capture"]["duration_ms"]),
                "definition_json": body}
        except Exception:  # a module that cannot describe itself is simply not stored
            continue
    return rows


def publish_policy(db: sqlite3.Connection, version: str, definition: Dict[str, Any], stamp: int) -> bool:
    """True when inserted, False when the identical definition already exists; ConflictError otherwise."""
    row = db.execute("SELECT definition_json FROM policy_definitions WHERE policy_version = ?", (version,)).fetchone()
    if row is not None:
        if json.loads(row["definition_json"]) != json.loads(dumps(definition)):
            raise ConflictError("policy %s exists with different content" % version)
        return False
    db.execute("INSERT INTO policy_definitions (policy_version, definition_json, created_at_ms) VALUES (?, ?, ?)",
               (version, dumps(definition), stamp))
    return True


_COLUMNS = ("task", "version", "metric_id", "unit", "instruction_line_id", "repeat_line_id", "required_resource",
            "window_ms")


def publish_task_definition(db: sqlite3.Connection, row: Dict[str, Any], stamp: int) -> bool:
    """True when inserted, False for an identical re-publish; ConflictError for different content."""
    old = db.execute("SELECT * FROM task_definitions WHERE definition_id = ?", (row["definition_id"],)).fetchone()
    if old is not None:
        same = (all(old[c] == row[c] for c in _COLUMNS)
                and json.loads(old["definition_json"]) == json.loads(dumps(row["definition_json"])))
        if not same:
            raise ConflictError("task definition %s exists with different content" % row["definition_id"])
        return False
    db.execute(
        "INSERT INTO task_definitions (definition_id, task, version, metric_id, unit, instruction_line_id, "
        "repeat_line_id, required_resource, window_ms, definition_json, created_at_ms) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (row["definition_id"], row["task"], row["version"], row["metric_id"], row["unit"],
         row["instruction_line_id"], row["repeat_line_id"], row["required_resource"], row["window_ms"],
         dumps(row["definition_json"]), stamp))
    return True


def definition_for(db: sqlite3.Connection, task: str, version: str) -> Optional[str]:
    row = db.execute("SELECT definition_id FROM task_definitions WHERE task = ? AND version = ?",
                     (task, version)).fetchone()
    return row[0] if row else None
