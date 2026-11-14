"""Which earlier task video the family sees next to today's in an alert (backend/proposals/baseline-video-comparison).

Read-only over the shared SQLite schema. For one call it answers:

- `task`: the task behind the alert. The first confirmed face or arm sign of the call's latest stored summary
  (`task_source: "alert_sign"`), as `link.alert_frames` chooses the alert frames. Without one, the task with the
  most attempt videos in the call, a tie going to the smile (`"most_tries"`).
- `today`: the call's attempt videos at that task, in recording order, in every state.
- `earlier`: one ready, unexpired, finished, not acted video of the same task from an earlier call of the same
  person, or null.
  1. `earlier_source: "baseline"`: an attempt that is a member of her learned usual (`baseline_members`, whose
     insert trigger already admits only live/normal, as-usual/learning, completed and usable attempts). Members of
     the snapshot frozen for this call (`session_baselines`) come first, then other snapshots; newest first.
  2. `"recent_as_usual"`: when no learned attempt has a video, the newest earlier live/normal call whose final
     summary was as_usual or learning and that never noticed a change.
- `earlier_missing`, when `earlier` is null: `expired` (a matching video existed), `not_retained` (an eligible
  earlier call exists but kept no video) or `no_matching_source`.

Matches the person and task only. The proposal's extra keys (task-definition version, the arm's hand and capture
orientation) are not checked yet. A learned attempt joins its video through the stored id: `baseline_members`
holds "<call_id>.<attempt_id>", which is `attempt_clips.command_id`.
"""

import json
from typing import Any

from .attempt_clips import clip_metadata

SIGN_TASKS = {"face": "smile", "arm": "arms"}

# A video that can stand for her earlier self: same person and task, an earlier call, never acted, fully sent.
_USABLE = (
    "c.task = :task AND c.session_label != 'acted' AND c.capture_status = 'finished' "
    "AND src.person_id = :person AND src.call_id != :call AND src.created_at_ms < :created"
)
_PLAYABLE = "c.state = 'ready' AND c.expires_at_ms > :now"

_BASELINE_SQL = (
    "SELECT c.*, (sb.snapshot_id IS NOT NULL) AS frozen FROM baseline_members m "
    "JOIN baseline_snapshots s ON s.snapshot_id = m.snapshot_id "
    "JOIN task_definitions d ON d.definition_id = m.definition_id "
    "JOIN attempt_clips c ON c.command_id = m.source_attempt_id AND c.call_id = m.source_call_id "
    "JOIN calls src ON src.call_id = m.source_call_id "
    "LEFT JOIN session_baselines sb ON sb.snapshot_id = m.snapshot_id AND sb.call_id = :call AND sb.task = :task "
    "WHERE s.person_id = :person AND d.task = :task AND " + _USABLE + " AND {playable} "
    "ORDER BY frozen DESC, c.recorded_at_ms DESC, c.rowid DESC LIMIT 1"
)

_CALM_CALL = (
    "src.label IN ('live', 'normal') "
    "AND EXISTS (SELECT 1 FROM session_summaries f WHERE f.call_id = src.call_id AND f.phase = 'final' "
    "            AND f.status IN ('as_usual', 'learning')) "
    "AND NOT EXISTS (SELECT 1 FROM session_summaries x WHERE x.call_id = src.call_id AND x.status = 'change_noticed')"
)

_RECENT_SQL = (
    "SELECT c.* FROM attempt_clips c JOIN calls src ON src.call_id = c.call_id "
    "WHERE " + _USABLE + " AND " + _CALM_CALL + " AND {playable} "
    "ORDER BY c.recorded_at_ms DESC, c.rowid DESC LIMIT 1"
)

_ELIGIBLE_CALL_SQL = (
    "SELECT 1 FROM calls src WHERE src.person_id = :person AND src.call_id != :call "
    "AND src.created_at_ms < :created AND (" + _CALM_CALL + " OR EXISTS ("
    "  SELECT 1 FROM baseline_members m JOIN task_definitions d ON d.definition_id = m.definition_id "
    "  WHERE m.source_call_id = src.call_id AND d.task = :task)) LIMIT 1"
)


def alert_task(db: Any, call_id: str, clips: list[dict[str, Any]]) -> tuple[str | None, str | None]:
    row = db.execute("SELECT summary_json FROM session_summaries WHERE call_id = ? ORDER BY revision DESC LIMIT 1",
                     (call_id,)).fetchone()
    signs: Any = []
    if row is not None:
        try:
            signs = json.loads(row["summary_json"]).get("signs") or []
        except (TypeError, ValueError, AttributeError):
            signs = []
    for sign in signs if isinstance(signs, list) else []:
        if isinstance(sign, dict) and sign.get("confirmed") is True and sign.get("sign") in SIGN_TASKS:
            return SIGN_TASKS[sign["sign"]], "alert_sign"
    tries = {task: sum(1 for clip in clips if clip["task"] == task) for task in ("smile", "arms")}
    if not any(tries.values()):
        return None, None
    return max(("smile", "arms"), key=lambda task: tries[task]), "most_tries"


def select_comparison(db: Any, call_id: str, stamp: int) -> dict[str, Any] | None:
    """The comparison for one call; None when the database does not know the call."""
    call = db.execute("SELECT person_id, created_at_ms FROM calls WHERE call_id = ?", (call_id,)).fetchone()
    if call is None:
        return None
    clips = [dict(row) for row in db.execute(
        "SELECT * FROM attempt_clips WHERE call_id = ? ORDER BY recorded_at_ms, created_at_ms, rowid", (call_id,))]
    task, task_source = alert_task(db, call_id, clips)
    result: dict[str, Any] = {
        "call_id": call_id, "task": task, "task_source": task_source,
        "today": [clip_metadata(clip) for clip in clips if clip["task"] == task],
        "earlier": None, "earlier_source": None, "earlier_missing": None,
    }
    if task is None:
        result["earlier_missing"] = "no_matching_source"
        return result
    params = {"call": call_id, "task": task, "person": call["person_id"], "created": call["created_at_ms"], "now": stamp}
    for source, sql in (("baseline", _BASELINE_SQL), ("recent_as_usual", _RECENT_SQL)):
        row = db.execute(sql.format(playable=_PLAYABLE), params).fetchone()
        if row is not None:
            clip = dict(row)
            clip.pop("frozen", None)
            result.update(earlier=clip_metadata(clip), earlier_source=source)
            return result
    expired = "(c.state = 'expired' OR (c.state = 'ready' AND c.expires_at_ms <= :now))"
    if any(db.execute(sql.format(playable=expired), params).fetchone() for sql in (_BASELINE_SQL, _RECENT_SQL)):
        result["earlier_missing"] = "expired"
    elif db.execute(_ELIGIBLE_CALL_SQL, params).fetchone():
        result["earlier_missing"] = "not_retained"
    else:
        result["earlier_missing"] = "no_matching_source"
    return result
