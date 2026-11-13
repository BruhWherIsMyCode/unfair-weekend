"""The family message stand-in of the PC version.

`publish` prints a framed block in the server terminal and appends the text to `<runs>/calls/family_messages.log`.
One message id per call: the final publish updates the mid-call one (the block says "UPDATED"). No real delivery
exists in the PC version; the client shows the final family text on its result card.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from analysis.session.adapters import render_family_text


class FamilyStandIn:
    def __init__(self, call_id: str, person_name: str, log_path: Optional[Path] = None,
                 out: Callable[[str], Any] = print) -> None:
        self.call_id = call_id
        self.person_name = person_name
        self.log_path = Path(log_path) if log_path is not None else None
        self.out = out
        self.message_id = "msg_%s" % call_id
        self.messages: List[Dict[str, Any]] = []  # every publish: phase, status, text

    @property
    def latest_text(self) -> Optional[str]:
        return self.messages[-1]["text"] if self.messages else None

    async def publish(self, summary: Dict[str, Any], phase: str, message_id: Optional[str] = None) -> Dict[str, Any]:
        text = render_family_text(summary, self.person_name)
        updated = bool(self.messages)
        self.messages.append({"phase": phase, "status": summary.get("status"), "text": text})
        bar = "=" * 64
        title = "FAMILY MESSAGE (%s)%s  id %s" % (phase, " UPDATED" if updated else "", self.message_id)
        self.out("\n".join(["", bar, title, text, bar]))
        if self.log_path is not None:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.log_path, "a", encoding="utf-8") as fh:
                fh.write("%s\t%s\t%s\t%s\t%s\n" % (time.strftime("%Y-%m-%dT%H:%M:%S"), self.message_id, phase,
                                                  summary.get("status"), text))
        return {"message_id": self.message_id, "delivery": "printed"}
