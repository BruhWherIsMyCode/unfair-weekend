"""One entry point for a whole call: run_call(session, adapter, mode, client).

mode "fixed"  - the fixed-order driver (no model). The default.
mode "claude" - the Claude tool loop; it falls back to the fixed-order driver for the rest of the call when
                Claude is unusable or fails (the reason goes to session.log as agent_fallback).
mode None     - follow the feature switch: environment UW_CLAUDE=on means "claude", anything else "fixed".
                This is the only place UW_CLAUDE is read.

Whatever happens, the call ends with session.finish() (idempotent), so a final summary is always published.
Returns the SessionResult dict.
"""
from __future__ import annotations

import os
from typing import Any, Dict, Optional

from analysis.session.agent import ClaudeAgent
from analysis.session.agent_tools import ToolHandlers, load_script
from analysis.session.claude_client import ClaudeClient, ClaudeUnavailable
from analysis.session.fallback import FixedOrderDriver

SWITCH_ENV = "UW_CLAUDE"


def mode_from_switch() -> str:
    return "claude" if os.environ.get(SWITCH_ENV, "").strip().lower() == "on" else "fixed"


async def run_call(session: Any, adapter: Any, mode: Optional[str] = None, client: Any = None,
                   script: Optional[Dict[str, str]] = None, agent_clock: Any = None) -> Dict[str, Any]:
    if mode is None:
        mode = mode_from_switch()
    if mode not in ("fixed", "claude"):
        raise ValueError("mode must be 'fixed' or 'claude'")
    handlers = ToolHandlers(session, adapter, script if script is not None else load_script())
    session.note("call_driver", mode=mode)
    try:
        if mode == "claude":
            reason: Optional[str] = None
            if client is None:
                try:
                    client = ClaudeClient()
                except ClaudeUnavailable as exc:
                    reason = "client_unavailable: %s" % exc
            agent = ClaudeAgent(session, handlers, client, agent_clock, unavailable_reason=reason)
            await agent.run()
        else:
            await FixedOrderDriver(handlers).run()
    except Exception as exc:  # a driver bug must not lose the call: the fixed order finishes it
        session.note("driver_crashed", mode=mode, error=type(exc).__name__)
        if not handlers.ended:
            await FixedOrderDriver(handlers).run()
    return await session.finish()
