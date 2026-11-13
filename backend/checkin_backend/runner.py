"""Runs one answered call: builds Agent 3's CallSession over a ClientLink and drives it to the end.

The analysis package (`analysis/`, Agent 3) is imported when it is on the Python path, or when the repo root (the
parent of `backend/`) holds it: that root is then added to the path. One function runs the call:
`analysis.backend_entry.run_backend_call(link, modules)`. Without the package the call
falls back to a scripted driver that plays the fixed lines, records answers and runs each task capture with
no measurement, so the client and the call service can be exercised end to end. That fallback never produces
a reading and always reports the session as incomplete.
"""

import asyncio
import importlib.util
import logging
import os
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from .link import CallEnded, ClientLink, PlaybackFailed
from .logging_config import log_event

log = logging.getLogger(__name__)

READY_TIMEOUT_S = 15.0
TASKS = ("smile", "arms", "sentence")
# The cue lines Agent 3's session plays after a capture starts (analysis/session/limits.py SMILE_CUE_MS and the
# ARMS_PHASES swap). The stand-in runners play the same ones, so the phone's task card and timer behave alike.
CUES = {"smile": [{"at_ms": 1500, "line_id": "smile_now"}], "arms": [{"at_ms": 6500, "line_id": "swap_hands"}]}
QUESTIONS = ("q_medicine", "q_feeling", "q_unusual")
CAPTURE_SETTINGS = {
    "smile": {"media_kind": "video", "duration_ms": 6000, "prepare_ms": 0, "stop_on_silence_ms": None,
              "format": "jpeg", "sample_period_ms": 200},
    "arms": {"media_kind": "video", "duration_ms": 15000, "prepare_ms": 2000, "stop_on_silence_ms": None,
             "format": "jpeg", "sample_period_ms": 200},
    "sentence": {"media_kind": "audio", "duration_ms": 12000, "prepare_ms": 0, "stop_on_silence_ms": 1500,
                 "format": "pcm_s16le_16000_mono", "sample_period_ms": 250},
}


REPO_ROOT = Path(__file__).resolve().parents[2]  # the parent of backend/


def ensure_analysis_path() -> bool:
    """Make `analysis` importable. When it is missing and the repo root (the parent of backend/) holds
    analysis/__init__.py, that root is added to sys.path, so the backend works without PYTHONPATH."""
    if importlib.util.find_spec("analysis") is not None:
        return True
    if (REPO_ROOT / "analysis" / "__init__.py").is_file():
        if str(REPO_ROOT) not in sys.path:
            sys.path.insert(0, str(REPO_ROOT))
        importlib.invalidate_caches()
        return importlib.util.find_spec("analysis") is not None
    return False


def analysis_available() -> bool:
    ensure_analysis_path()
    try:
        import analysis.backend_entry  # noqa: F401
        import analysis.session.call_session  # noqa: F401
        import analysis.session.run_call  # noqa: F401
    except ImportError:
        return False
    return True


async def run_analysis_call(link: ClientLink, modules: str, database_path: str | None = None) -> dict[str, Any]:
    """Agent 3's session over this link. All call settings are read by analysis.backend_entry (CALL_DRIVER, ...).
    `database_path` (DATABASE_PATH) turns on Agent 3's recording in the shared database; None leaves it off."""
    ensure_analysis_path()
    from analysis.backend_entry import Settings, default_modules, run_backend_call

    # One writer for the shared database: when the call service's Store is on, Agent 3's recorder hands its jobs
    # to the same writer thread (its calls row is then always committed before ours; summaries stay the Store's).
    store = getattr(link, "store", None)
    submit = store._submit if (database_path and store is not None and getattr(store, "enabled", False)) else None
    if modules == "fake":
        from .fake_tasks import fake_modules

        chosen = fake_modules()
    else:
        from .native_tasks import protect_native_modules

        settings = Settings(os.environ, link.person.person_id)
        chosen = protect_native_modules(default_modules(settings.mirrored))
    return await run_backend_call(link, modules=chosen, database_path=database_path, submit=submit)


class ScriptedSession:
    """Just enough of CallSession's event surface for the scripted driver."""

    def __init__(self, link: ClientLink) -> None:
        self.link = link
        self.hung_up = False
        self.started: dict[str, asyncio.Event] = {}

    def started_event(self, attempt_id: str) -> asyncio.Event:
        return self.started.setdefault(attempt_id, asyncio.Event())

    def handle_capture_started(self, attempt_id: str) -> str:
        self.started_event(attempt_id).set()
        return "applied"

    def handle_cancel_ack(self, attempt_id: str) -> None:
        return None

    def handle_command_failed(self, attempt_id: str, error_code: str) -> None:
        return None

    def handle_resource_event(self, event: Any) -> str:
        return "applied"

    def hang_up(self) -> None:
        self.hung_up = True


async def play_cues(link: ClientLink, started: asyncio.Event, cues: list[dict[str, Any]]) -> None:
    """Each cue `at_ms` after the phone reported capture.started. A failed cue is skipped, never fails the task."""
    await started.wait()
    begin = asyncio.get_running_loop().time()
    for cue in sorted(cues, key=lambda c: c["at_ms"]):
        delay = begin + cue["at_ms"] / 1000 - asyncio.get_running_loop().time()
        if delay > 0:
            await asyncio.sleep(delay)
        try:
            await link.say(cue["line_id"])
        except (CallEnded, PlaybackFailed):
            return


async def run_scripted_call(link: ClientLink) -> dict[str, Any]:
    session = ScriptedSession(link)
    link.attach(session)
    answers = {"medicine": "unclear", "feeling": "", "unusual": ""}
    await link.say("greet")
    for question in QUESTIONS:
        await link.say(question)
        text = await link.listen(question, 8.0)
        key = question.removeprefix("q_")
        if text:
            answers[key] = text if key != "medicine" else "unclear"
    await link.say("setup_phone")
    for number, task in enumerate(TASKS, start=1):
        if session.hung_up:
            break
        await link.say(f"task_{task}")
        attempt_id = f"S{number}"
        await link.start_capture(attempt_id, task, CAPTURE_SETTINGS[task])
        cues = asyncio.ensure_future(play_cues(link, session.started_event(attempt_id), CUES.get(task, [])))
        window_s = CAPTURE_SETTINGS[task]["duration_ms"] / 1000
        count = 0
        try:
            async with asyncio.timeout(window_s + 10):
                async for _sample in link.samples(attempt_id):
                    count += 1
        except TimeoutError:
            await link.cancel_capture(attempt_id, "timeout")
        finally:
            cues.cancel()
            await asyncio.gather(cues, return_exceptions=True)
        link.log.append({"kind": "scripted_task", "task": task, "samples": count})
    if not session.hung_up:
        await link.say("bye")
    summary = {
        "person_id": link.person.person_id,
        "label": link.person.label,
        "duration_s": 0,
        "answers": answers,
        "signs": [],
        "status": "incomplete",
        "baseline_sessions": 0,
        "baseline_needed": 5,
        "clip_id": None,
    }
    await link.publish(summary, "final")
    return summary


def measuring_problems() -> list[str]:
    """Plain problems that stop Agent 3's real measuring (`analysis.selfcheck.check()`); empty = measuring is ready."""
    if not analysis_available():
        return ["Agent 3's analysis package was not found (it lives next to backend/ in the repo)."]
    from analysis import selfcheck

    return selfcheck.check()


def problems_block(problems: list[str], consequence: str) -> str:
    bar = "=" * 72
    lines = ["", bar, "MEASURING IS NOT READY", *(f"  - {problem}" for problem in problems), f"  -> {consequence}", bar]
    return "\n".join(lines)


def _print_block(block: str) -> None:
    log.warning(block)


def choose_driver(
    kind: str, database_path: str | None = None, report: Callable[[str], Any] = _print_block
) -> Callable[[ClientLink], Awaitable[dict[str, Any]]]:
    """`auto` uses the analysis only when its self-check finds no problem; otherwise it prints the problems in one
    block at start-up and runs the scripted call. `analysis` with problems refuses to start (never mid-call)."""
    if kind in ("auto", "analysis"):
        problems = measuring_problems()
        if problems and kind == "analysis":
            raise RuntimeError(problems_block(problems, "CALL_RUNNER=analysis cannot start. Fix the above, or use "
                                                        "CALL_RUNNER=scripted."))
        if problems:
            report(problems_block(problems, "Using the scripted call: it plays the lines but measures nothing."))
            kind = "scripted"
        else:
            kind = "analysis"
    if kind == "scripted":
        return run_scripted_call
    if kind in ("analysis", "fake"):
        modules = "fake" if kind == "fake" else "real"
        return lambda link: run_analysis_call(link, modules, database_path)
    raise ValueError("CALL_RUNNER must be auto, analysis, fake or scripted")


async def run_answered_call(link: ClientLink, driver: Callable[[ClientLink], Awaitable[dict[str, Any]]]) -> dict[str, Any] | None:
    log_event(log, logging.INFO, "call.waiting_for_client", call_id=link.call_id, timeout_s=READY_TIMEOUT_S)
    try:
        async with asyncio.timeout(READY_TIMEOUT_S):
            await link.ready.wait()
    except TimeoutError:
        log_event(log, logging.WARNING, "call.client_not_ready", call_id=link.call_id)
        link.log.append({"kind": "client_not_ready"})
        if not link.ended:
            await link.publish({"status": "incomplete", "signs": []}, "final")
        return None
    if link.ended:
        return None
    try:
        log_event(log, logging.INFO, "call.driver_started", call_id=link.call_id)
        result = await driver(link)
        log_event(log, logging.INFO, "call.driver_completed", call_id=link.call_id, status=result.get("status"))
        return result
    except (CallEnded, PlaybackFailed) as exc:
        log_event(log, logging.WARNING, "call.driver_stopped", call_id=link.call_id, error_type=type(exc).__name__)
        link.log.append({"kind": "call_stopped", "error": type(exc).__name__})
        if not link.ended:
            await link.publish({"status": "incomplete", "signs": []}, "final")
        return None
    except Exception as exc:  # a driver crash must still end the call and tell the family
        log_event(log, logging.ERROR, "call.driver_failed", call_id=link.call_id, exc_info=True)
        link.log.append({"kind": "driver_crashed", "error": type(exc).__name__})
        if not link.ended:
            try:
                await link.publish({"status": "incomplete", "signs": []}, "final")
            except Exception:
                log_event(log, logging.ERROR, "call.fallback_publish_failed", call_id=link.call_id, exc_info=True)
        return None
