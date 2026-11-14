"""One entry point for a whole call: run_call(session, adapter, mode, client, backend=...).

mode "fixed"  - the fixed-order driver (no model). The default.
mode "claude" - the Claude tool loop; it falls back to the fixed-order driver for the rest of the call when
                Claude is unusable or fails (the reason goes to session.log as agent_fallback).
                backend "api" (default) = ClaudeClient (ANTHROPIC_API_KEY), backend "subscription" =
                ClaudeSubscriptionClient (the claude CLI login, Claude Agent SDK). A construction failure means
                the fixed order for the whole call, with the reason logged, unless fallback is disabled.
mode None     - follow the feature switch: environment UW_CLAUDE=on means "claude", anything else "fixed".
                This is the only place UW_CLAUDE is read.

UW_CLAUDE_FALLBACK=off (or allow_fallback=False) requires Claude mode. On failure it logs agent_failed,
finishes the session and raises ClaudeFailed; no fixed-order questions or tasks run. The default is on.
A client with a close() method (the subscription client) is closed in the finally. Normal completion returns
the SessionResult dict. Finalization uses the existing idempotent session.finish().

Also here: `safety_floor_from_env()`, the only reader of UW_SAFETY_FLOOR (server.py is the other place that decides
the floor, from its flag). Keep the floor ON for the demo and any real use.
"""
from __future__ import annotations

import os
from typing import Any, Dict, Mapping, Optional

from analysis.session import limits
from analysis.session.agent import ClaudeAgent, ClaudeFailed
from analysis.session.agent_tools import ToolHandlers, load_script
from analysis.session.claude_client import ClaudeClient, ClaudeUnavailable
from analysis.session.fallback import FixedOrderDriver

SWITCH_ENV = "UW_CLAUDE"
FLOOR_ENV = "UW_SAFETY_FLOOR"
FALLBACK_ENV = "UW_CLAUDE_FALLBACK"
BACKENDS = ("api", "subscription")


def mode_from_switch() -> str:
    return "claude" if os.environ.get(SWITCH_ENV, "").strip().lower() == "on" else "fixed"


def fallback_from_env(env: Optional[Mapping[str, str]] = None) -> bool:
    source = os.environ if env is None else env
    value = str(source.get(FALLBACK_ENV, "") or "").strip().lower() or "on"
    if value not in ("on", "off"):
        raise ValueError("%s must be on or off" % FALLBACK_ENV)
    return value == "on"


def safety_floor_from_env(default: bool = limits.SAFETY_FLOOR_DEFAULT) -> bool:
    """UW_SAFETY_FLOOR=off turns the floor off (experiments only); anything else keeps the default."""
    value = os.environ.get(FLOOR_ENV, "").strip().lower()
    return False if value == "off" else default


def build_client(backend: str) -> Any:
    """The real client for a backend. Raises ClaudeUnavailable when it cannot be built."""
    if backend == "subscription":
        from analysis.session.claude_sub_client import ClaudeSubscriptionClient

        return ClaudeSubscriptionClient()
    return ClaudeClient()


async def run_call(session: Any, adapter: Any, mode: Optional[str] = None, client: Any = None,
                   script: Optional[Dict[str, str]] = None, agent_clock: Any = None,
                   backend: str = "api", allow_fallback: Optional[bool] = None) -> Dict[str, Any]:
    if mode is None:
        mode = mode_from_switch()
    if mode not in ("fixed", "claude"):
        raise ValueError("mode must be 'fixed' or 'claude'")
    if backend not in BACKENDS:
        raise ValueError("backend must be 'api' or 'subscription'")
    if allow_fallback is None:
        allow_fallback = fallback_from_env()
    if not isinstance(allow_fallback, bool):
        raise ValueError("allow_fallback must be a boolean")
    if not allow_fallback and mode != "claude":
        raise ValueError("UW_CLAUDE_FALLBACK=off requires CALL_DRIVER=claude")
    handlers = ToolHandlers(session, adapter, script if script is not None else load_script())
    session.note("call_driver", mode=mode, backend=backend if mode == "claude" else None,
                 allow_fallback=allow_fallback)
    try:
        if mode == "claude":
            reason: Optional[str] = None
            if client is None:
                try:
                    client = build_client(backend)
                except ClaudeUnavailable as exc:
                    reason = "client_unavailable: %s" % exc
            timeout_s = limits.CLAUDE_SUB_TIMEOUT_S if backend == "subscription" else limits.CLAUDE_TIMEOUT_S
            agent = ClaudeAgent(session, handlers, client, agent_clock, unavailable_reason=reason, timeout_s=timeout_s,
                                allow_fallback=allow_fallback)
            await agent.run()
        else:
            await FixedOrderDriver(handlers).run()
    except ClaudeFailed:
        if not handlers.ended:
            await handlers.force_finish()
        raise
    except Exception as exc:  # finish a driver failure, respecting the operator's fallback setting
        session.note("driver_crashed", mode=mode, error=type(exc).__name__)
        if not allow_fallback:
            reason = "driver_error_%s" % type(exc).__name__
            session.note("agent_failed", reason=reason)
            if not handlers.ended:
                await handlers.force_finish()
            raise ClaudeFailed(reason) from None
        if not handlers.ended:
            await FixedOrderDriver(handlers).run()
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            try:
                await close()
            except Exception as exc:
                session.note("client_close_failed", error=type(exc).__name__)
    return await session.finish()
