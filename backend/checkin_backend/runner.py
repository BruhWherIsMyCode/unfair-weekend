"""Runs one answered call: builds Agent 3's CallSession over a ClientLink and drives it to the end.

The analysis package (`analysis/`, Agent 3) is imported when it is on the Python path. Without it the call
falls back to a scripted driver that plays the fixed lines, records answers and runs each task capture with
no measurement, so the client and the call service can be exercised end to end. That fallback never produces
a reading and always reports the session as incomplete.
"""

import asyncio
import os
from collections.abc import Awaitable, Callable
from typing import Any

from .link import CallEnded, ClientLink, PlaybackFailed

READY_TIMEOUT_S = 15.0
TASKS = ("smile", "arms", "sentence")
QUESTIONS = ("q_medicine", "q_feeling", "q_unusual")
CAPTURE_SETTINGS = {
    "smile": {"media_kind": "video", "duration_ms": 5000, "prepare_ms": 0, "stop_on_silence_ms": None,
              "format": "jpeg", "sample_period_ms": 200},
    "arms": {"media_kind": "video", "duration_ms": 12000, "prepare_ms": 2000, "stop_on_silence_ms": None,
             "format": "jpeg", "sample_period_ms": 200},
    "sentence": {"media_kind": "audio", "duration_ms": 12000, "prepare_ms": 0, "stop_on_silence_ms": 1500,
                 "format": "pcm_s16le_16000_mono", "sample_period_ms": 250},
}


def analysis_available() -> bool:
    try:
        import analysis.session.call_session  # noqa: F401
        import analysis.session.run_call  # noqa: F401
    except ImportError:
        return False
    return True


def _modules(kind: str) -> dict[str, Any]:
    if kind == "fake":
        from .fake_tasks import fake_modules

        return fake_modules()
    from analysis.tasks.stream_registry import STREAM_TASKS

    return dict(STREAM_TASKS)


async def run_analysis_call(link: ClientLink, modules: str) -> dict[str, Any]:
    from analysis.session.call_session import CallSession
    from analysis.session.run_call import run_call

    session = CallSession(
        link.call_id,
        link.person.person_id,
        link.person.name,
        link,
        _modules(modules),
        baselines=link.person.baselines,
        label=link.person.label,
        initial_resources=link.initial_resources(),
    )
    link.attach(session)
    mode = os.environ.get("CALL_DRIVER") or None
    return await run_call(session, link, mode=mode)


class ScriptedSession:
    """Just enough of CallSession's event surface for the scripted driver."""

    def __init__(self, link: ClientLink) -> None:
        self.link = link
        self.hung_up = False
        self.started: set[str] = set()

    def handle_capture_started(self, attempt_id: str) -> str:
        self.started.add(attempt_id)
        return "applied"

    def handle_cancel_ack(self, attempt_id: str) -> None:
        return None

    def handle_command_failed(self, attempt_id: str, error_code: str) -> None:
        return None

    def handle_resource_event(self, event: Any) -> str:
        return "applied"

    def hang_up(self) -> None:
        self.hung_up = True


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
        window_s = CAPTURE_SETTINGS[task]["duration_ms"] / 1000
        count = 0
        try:
            async with asyncio.timeout(window_s + 10):
                async for _sample in link.samples(attempt_id):
                    count += 1
        except TimeoutError:
            await link.cancel_capture(attempt_id, "timeout")
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


def choose_driver(kind: str) -> Callable[[ClientLink], Awaitable[dict[str, Any]]]:
    if kind == "auto":
        kind = "analysis" if analysis_available() else "scripted"
    if kind == "scripted":
        return run_scripted_call
    if kind in ("analysis", "fake"):
        modules = "fake" if kind == "fake" else "real"
        return lambda link: run_analysis_call(link, modules)
    raise ValueError("CALL_RUNNER must be auto, analysis, fake or scripted")


async def run_answered_call(link: ClientLink, driver: Callable[[ClientLink], Awaitable[dict[str, Any]]]) -> dict[str, Any] | None:
    try:
        async with asyncio.timeout(READY_TIMEOUT_S):
            await link.ready.wait()
    except TimeoutError:
        link.log.append({"kind": "client_not_ready"})
        if not link.ended:
            await link.publish({"status": "incomplete", "signs": []}, "final")
        return None
    if link.ended:
        return None
    try:
        return await driver(link)
    except (CallEnded, PlaybackFailed) as exc:
        link.log.append({"kind": "call_stopped", "error": type(exc).__name__})
        if not link.ended:
            await link.publish({"status": "incomplete", "signs": []}, "final")
        return None
    except Exception as exc:  # a driver crash must still end the call and tell the family
        link.log.append({"kind": "driver_crashed", "error": type(exc).__name__})
        if not link.ended:
            try:
                await link.publish({"status": "incomplete", "signs": []}, "final")
            except Exception:
                pass
        return None
