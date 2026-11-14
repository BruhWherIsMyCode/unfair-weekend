"""Mirror analysis session events as metadata without copying conversational content."""

import logging
import re
from typing import Any

from .logging_config import log_event

log = logging.getLogger(__name__)
FIELDS = {"attempt_id", "command_id", "task", "outcome", "state", "code", "stop_kind", "resource",
          "mode", "backend", "turn", "tool", "line", "question", "heard", "status", "interruption", "latency_s",
          "allow_fallback"}
QUIET = {"observation", "task_observation", "sample", "task_progress"}


class SessionEventLog(list):
    """Keep the session's list interface and its existing live-print callback."""

    def __init__(self, previous: list, call_id: str) -> None:
        super().__init__(previous)
        self._previous = previous
        self._call_id = call_id

    def append(self, entry: dict[str, Any]) -> None:
        self._previous.append(entry)
        super().append(entry)
        kind = entry.get("kind", "")
        if not isinstance(kind, str) or not re.fullmatch(r"[a-z_]{1,80}", kind):
            return
        level = logging.DEBUG if kind in QUIET else logging.INFO
        if kind.endswith(("_failed", "_crashed", "_error")):
            level = logging.ERROR
        elif kind in ("agent_fallback", "attempt_stopping") or entry.get("outcome") in ("failed", "interrupted"):
            level = logging.WARNING
        error = entry.get("error")
        error_type = error if isinstance(error, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,79}", error) else None
        fields = {key: value for key, value in entry.items() if key in FIELDS}
        if kind == "agent_tool":
            fields["tool"] = entry.get("name")
        if kind in ("agent_fallback", "agent_failed"):
            fields["reason"] = entry.get("reason")
        log_event(log, level, f"session.{kind}", call_id=self._call_id, error_type=error_type, **fields)
