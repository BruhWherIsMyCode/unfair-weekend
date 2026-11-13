"""The one function the team backend calls for an answered call: `run_backend_call(link, modules=None, *, env=None)`.

The backend (`backend/checkin_backend/runner.py`) builds a `ClientLink` (it implements our CallAdapter) for the parent
phone and hands it here. This module builds the `CallSession` over it, loads her usual (baselines), runs the call driver
(`run_call`), then stores the baseline values the call may add and writes the call record. Everything it needs from the
environment is read from `env` (default `os.environ`) and validated BEFORE the call starts, so a bad value is a clear
ValueError and not a half-run call (the backend's runner turns a driver error into an `incomplete` final).

Environment (all optional):

    CALL_DRIVER          fixed | claude      the call driver; unset = follow UW_CLAUDE=on (claude) else fixed
    UW_CLAUDE_BACKEND    api | subscription  how Claude is reached (default api); subscription removes ANTHROPIC_API_KEY
                                             from this process first, as the PC server does
    UW_SAFETY_FLOOR      off                 only "off" turns the safety floor off (experiments); anything else keeps it ON
    UW_MIRRORED          on | off            the phone frames are mirrored (default off)
    UW_BASELINE_PERSON   key the baseline is stored under (default: the person id)
    UW_DATA_DIR          folder of the baseline JSON files (default analysis/data)
    UW_BASELINE_NEEDED   whole number >= 1: normal calls until her usual is ready (default config.BASELINE_NEEDED)
    UW_RUNS_DIR          folder of the call record JSON files (default analysis/runs/calls)
    UW_CALL_LOG          off                 no live log lines (default: one plain line per step, prefix [call <id>])

Required tasks on this path are smile and arms (`limits.REQUIRED_TASKS`): the phone sends raw audio and nothing measures
the sentence on the server yet, so the default modules do not include a sentence module at all.

STATUS: tested with injected frame readers; real phone frames, mediapipe on the server and the real Claude paths were
never run.
"""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional, Protocol

from analysis import config
from analysis.pc.store import BaselineStore
from analysis.session import limits
from analysis.session.adapters import render_family_text
from analysis.session.call_record import LiveLog, build_record, format_log_line, write_record
from analysis.session.call_session import CallSession
from analysis.session.run_call import BACKENDS, FLOOR_ENV, SWITCH_ENV, run_call

log = logging.getLogger(__name__)

DRIVERS = ("fixed", "claude")


class BaselineSource(Protocol):
    """Where her usual comes from. The JSON store today; a database source replaces `make_baseline_source` later."""

    def load_baselines(self) -> Dict[str, Dict[str, Any]]: ...

    def append(self, label: str, values: Dict[str, float]) -> bool: ...


def make_baseline_source(person_key: str, data_dir: Optional[Path], needed: Optional[int]) -> BaselineSource:
    """The baseline source of the backend path (replace this one function for another storage)."""
    return BaselineStore(person_key, data_dir, needed)


class Settings:
    """The validated environment of one call."""

    def __init__(self, env: Mapping[str, str], person_id: str) -> None:
        def get(name: str) -> str:
            return str(env.get(name, "") or "").strip()

        driver = get("CALL_DRIVER").lower()
        if driver and driver not in DRIVERS:
            raise ValueError("CALL_DRIVER must be fixed or claude, got %r" % driver)
        self.mode = driver or ("claude" if get(SWITCH_ENV).lower() == "on" else "fixed")

        self.claude_backend = get("UW_CLAUDE_BACKEND").lower() or "api"
        if self.claude_backend not in BACKENDS:
            raise ValueError("UW_CLAUDE_BACKEND must be api or subscription, got %r" % self.claude_backend)

        self.safety_floor = get(FLOOR_ENV).lower() != "off"

        mirrored = get("UW_MIRRORED").lower() or "off"
        if mirrored not in ("on", "off"):
            raise ValueError("UW_MIRRORED must be on or off, got %r" % mirrored)
        self.mirrored = mirrored == "on"

        needed = get("UW_BASELINE_NEEDED")
        self.baseline_needed: Optional[int] = None
        if needed:
            try:
                self.baseline_needed = int(needed)
            except ValueError:
                self.baseline_needed = 0
            if self.baseline_needed < 1:
                raise ValueError("UW_BASELINE_NEEDED must be a whole number of 1 or more, got %r" % needed)

        self.person_key = get("UW_BASELINE_PERSON") or person_id
        self.data_dir = Path(get("UW_DATA_DIR")) if get("UW_DATA_DIR") else None
        self.runs_dir = Path(get("UW_RUNS_DIR")) if get("UW_RUNS_DIR") else config.RUNS_DIR / "calls"

        call_log = get("UW_CALL_LOG").lower()
        if call_log and call_log not in ("on", "off"):
            raise ValueError("UW_CALL_LOG must be on or off, got %r" % call_log)
        self.call_log = call_log != "off"


def default_modules(mirrored: bool) -> Dict[str, Any]:
    """New production stream tasks per call. No sentence module: the call driver skips a check without a module."""
    from analysis.tasks.stream_arms import StreamArms
    from analysis.tasks.stream_smile import StreamSmile

    return {"smile": StreamSmile(mirrored=mirrored), "arms": StreamArms(mirrored=mirrored)}


async def run_backend_call(link: Any, modules: Optional[Dict[str, Any]] = None, *,
                           env: Optional[Mapping[str, str]] = None,
                           out: Callable[[str], Any] = print) -> Dict[str, Any]:
    """Run one answered call over `link` and return the SessionResult dict.

    `modules` (tests, the backend's fake runner) replaces the production stream tasks. `out` receives the live log lines.
    """
    env = os.environ if env is None else env
    person = link.person
    settings = Settings(env, person.person_id)  # raises ValueError before anything happens
    if settings.mode == "claude" and settings.claude_backend == "subscription":
        from analysis.session.claude_sub_client import ignore_api_key_env

        ignore_api_key_env()  # the CLI must not bill an API key

    source = make_baseline_source(settings.person_key, settings.data_dir, settings.baseline_needed)
    baselines = source.load_baselines()
    if modules is None:
        modules = default_modules(settings.mirrored)

    started_wall = time.time()
    session = CallSession(link.call_id, person.person_id, person.name, link, modules, baselines,
                          label=person.label, required_tasks=limits.REQUIRED_TASKS,
                          initial_resources=link.initial_resources(), safety_floor=settings.safety_floor)
    if settings.call_log:
        prefix = "[call %s] " % link.call_id

        def show(entry: Dict[str, Any]) -> None:
            line = format_log_line(entry, session)
            if line is not None:
                out(prefix + line)

        session.log = LiveLog(show)
    link.attach(session)
    session.note("backend_call", mode=settings.mode, backend=settings.claude_backend if settings.mode == "claude" else None,
                 safety_floor=settings.safety_floor, mirrored=settings.mirrored, person_key=settings.person_key,
                 baseline_needed=settings.baseline_needed, required_tasks=list(session.required_tasks))

    try:
        summary = await run_call(session, link, settings.mode, backend=settings.claude_backend)
    except Exception as exc:  # the call is lost, the backend is not
        session.note("call_crashed", error=type(exc).__name__)
        summary = session.build_session_result().to_dict()

    try:
        stored = source.append(person.label, session.baseline_update())
        session.note("baseline_stored", stored=stored)
    except Exception as exc:  # never into the backend
        log.error("baseline store failed: %s", type(exc).__name__)
        session.note("baseline_store_failed", error=type(exc).__name__)

    try:
        family_text = render_family_text(summary, person.name)
        record = build_record(link.call_id, settings.person_key, person.label, settings.mode, settings.safety_floor,
                              summary, family_text, session.log, started_wall)
        write_record(settings.runs_dir, link.call_id, record, log.error)
    except Exception as exc:
        log.error("call record failed: %s", type(exc).__name__)
    return summary
