"""Scripted demo of the call session, no camera, no network (except the optional real-Claude driver).

    python -m analysis.session.demo_call                       # two branches, then a whole call, fixed order
    python -m analysis.session.demo_call --driver fake-claude  # ... then a call run by a scripted fake Claude
    python -m analysis.session.demo_call --driver claude --yes-real-claude   # SPENDS API CREDIT, never run yet

Branch 1 is the architecture plan's section 7 example against the fakes and made-up numbers: greet, smile within
her usual, arms attempt A1 interrupted by camera loss at 4 s, recovery, a new attempt A2 that completes, finish.
Branch 2: two smile readings outside her usual give one mid-call family message. Then the selected driver runs a
whole call (fixed order, a scripted fake Claude that times out right after the smile so the fixed order finishes
the call, or the real Claude). Sentence is not implemented in this version and is skipped. Written, not run.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any, Dict, List, Optional

from analysis.face_metric import FrameReading
from analysis.hand_metric import HandFrameReading
from analysis.session.adapters import FakeAdapter
from analysis.session.call_session import CallSession
from analysis.session.claude_client import ClaudeClient, ClaudeUnavailable, FakeClaudeClient, Hang, tool_use_response
from analysis.session.clock import FakeClock, RealClock
from analysis.session.models import Metric, Quality, TaskObservation
from analysis.session.run_call import run_call
from analysis.tasks.stream_arms import StreamArms, hold_windows
from analysis.tasks.stream_sentence import StreamSentence
from analysis.tasks.stream_smile import StreamSmile

BASELINE = {"snapshot_id": "baseline_demo", "ready": True, "sessions": 12, "needed": 5,
            "usual_low": -0.02, "usual_high": 0.03}  # made-up numbers
_HAND = {"ready": True, "sessions": 12, "needed": 5, "usual_low": -0.10, "usual_high": 0.10}  # made-up numbers
ARMS_BASELINE = {"snapshot_id": "baseline_demo_arms", "ready": True, "sessions": 12, "needed": 5,
                 "hand_left": dict(_HAND), "hand_right": dict(_HAND)}
ANSWERS = {"q_medicine": "Yes, after breakfast.", "q_feeling": "I feel fine, thank you.",
           "q_unusual": "Nothing unusual."}


def make_sample(i: int) -> Dict[str, Any]:
    return {"sequence": i, "captured_ms": i * 200, "format": "jpeg", "data_base64": "AAAA"}


def fake_decode(sample: Dict[str, Any]) -> Any:
    return sample  # the fake readers read the timestamp only


class FakeSmileReader:
    """A made-up face: relaxed for the first 1.5 s, then smiling with the configured mouth-corner height."""

    def __init__(self) -> None:
        self.corner = 0.0

    def __call__(self, image: Any, ts_ms: int) -> FrameReading:
        smiling = ts_ms >= 1500
        height = self.corner if smiling else 0.0
        return FrameReading(usable=True, unit=100.0, below_left=1.0 + height / 2, below_right=1.0 - height / 2,
                            corner_height=height, smile_strength=0.9 if smiling else 0.1, timestamp_ms=ts_ms)


class FakeHandReader:
    """A made-up free hand beside the face: steady at 0.5 eye distances below the eye line, and for the second
    half of its hold it sinks by `sink[side]` (0 = the hand stays up)."""

    def __init__(self) -> None:
        self.sink = {"left": 0.0, "right": 0.0}

    def __call__(self, image: Any, ts_ms: int, side: str) -> HandFrameReading:
        start, end = hold_windows()[side]
        height = 0.5 + (self.sink[side] if ts_ms >= (start + end) / 2 else 0.0)
        return HandFrameReading(usable=True, face_ok=True, unit=100.0, height=height, palm_width_ratio=1.0,
                                timestamp_ms=ts_ms)


class DemoArms:
    """Stand-in for the first arms attempt only: it reports two observations, then waits to be stopped.
    Its second run is the real arms task with the fake hand reader."""

    name = "arms"

    def __init__(self) -> None:
        self.runs = 0
        self.real = StreamArms(FakeHandReader(), fake_decode, use_executor=False)

    def definition(self) -> Dict[str, Any]:
        return self.real.definition()

    def attempt_values(self, attempt_id: str) -> Dict[str, float]:
        return self.real.attempt_values(attempt_id)

    async def run(self, context: Any, samples: Any, emit_observation: Any) -> Any:
        self.runs += 1
        if self.runs > 1:
            return await self.real.run(context, samples, emit_observation)
        sent = 0
        async for sample in samples:
            ms = sample["captured_ms"]
            if sent == 0 and ms >= 1000:
                emit_observation(TaskObservation("obs_%s_0" % context.attempt_id, context.call_id, context.attempt_id,
                                                 "arms", 0, 1000, "capturing", Quality("usable", []), [], "none"))
                sent = 1
            if sent == 1 and ms >= 3000:
                emit_observation(TaskObservation("obs_%s_1" % context.attempt_id, context.call_id, context.attempt_id,
                                                 "arms", 1, 3000, "capturing", Quality("usable", []),
                                                 [Metric("demo_arm_value_v0", "ratio", 0.01)], "none"))
                sent = 2
        await asyncio.Event().wait()  # never finishes by itself


async def stream(adapter: FakeAdapter, clock: FakeClock, attempt_id: str, samples: int, finish: bool = True) -> None:
    """The fake client: one sample every 0.2 s of fake time."""
    for i in range(samples):
        adapter.push_sample(attempt_id, make_sample(i))
        await clock.advance(0.2)
    if finish:
        adapter.finish_capture(attempt_id)
        await clock.settle()


def current_attempt(session: CallSession) -> str:
    return list(session.attempts)[-1]


def resource_event(event_id: str, state: str, sequence: int, reason: Any) -> Dict[str, Any]:
    return {"event_id": event_id, "type": "resource.changed", "command_id": None, "resource": "camera",
            "state": state, "sequence": sequence, "reason": reason}


def new_session(corner_reader: FakeSmileReader, clock: FakeClock, arms: Any, label: str = "acted",
                answers: Optional[Dict[str, Any]] = None) -> tuple:
    adapter = FakeAdapter(person_name="Ona", answers=answers)
    modules = {"smile": StreamSmile(corner_reader, fake_decode, use_executor=False), "arms": arms,
               "sentence": StreamSentence()}
    session = CallSession("call_demo", "p_demo", "Ona", adapter, modules,
                          {"smile": BASELINE, "arms": ARMS_BASELINE}, clock, label=label,
                          initial_resources={"camera": "available", "microphone": "available"})
    adapter.session = session
    return session, adapter


async def run_smile(session: CallSession, adapter: FakeAdapter, clock: FakeClock, corner: float,
                    reader: FakeSmileReader) -> Dict[str, Any]:
    reader.corner = corner
    task = asyncio.ensure_future(session.run_task("smile"))
    await clock.settle()
    await stream(adapter, clock, current_attempt(session), 26)
    return await task


def show(title: str, value: Any) -> None:
    print("\n== %s" % title)
    print(json.dumps(value, indent=2) if not isinstance(value, str) else value)


def show_log(session: CallSession, prefixes: Optional[tuple] = None) -> None:
    print("\n== event log")
    for e in session.log:
        if prefixes and not e["kind"].startswith(prefixes):
            continue
        rest = {k: v for k, v in e.items() if k not in ("t", "kind")}
        print("%7.2f  %-22s %s" % (e["t"], e["kind"], json.dumps(rest, sort_keys=True) if rest else ""))


async def normal_branch() -> None:
    print("##### Branch 1: smile within usual, arms interrupted by camera loss, recovery, new attempt")
    clock, reader = FakeClock(), FakeSmileReader()
    session, adapter = new_session(reader, clock, DemoArms())

    await adapter.say("greet")
    for question, answer in (("medicine", "yes"), ("feeling", "I feel fine."), ("unusual", "Nothing unusual.")):
        await adapter.say("q_" + question)
        session.record_answer(question, answer)
    await adapter.say("setup_phone")

    show("smile result (made-up reading inside her usual)", await run_smile(session, adapter, clock, 0.01, reader))

    arms = asyncio.ensure_future(session.run_task("arms"))
    await clock.settle()
    a1 = current_attempt(session)
    await stream(adapter, clock, a1, 20, finish=False)  # 4 seconds of samples
    session.handle_resource_event(resource_event("evt_camera_lost", "unavailable", 1, "track_ended"))
    show("arms A1: camera lost at 4 s", await arms)

    recovery = asyncio.ensure_future(session.recover_resource("camera"))
    await clock.advance(2.0)
    session.handle_resource_event(resource_event("evt_camera_back", "available", 2, None))
    show("recovery", await recovery)

    arms2 = asyncio.ensure_future(session.run_task("arms"))
    await clock.settle()
    await stream(adapter, clock, current_attempt(session), 101)  # the whole 20 s window, both hands
    show("arms A2 (made-up hands, both within her usual)", await arms2)
    show("sentence (not implemented in this version, so it ends failed)", await session.run_task("sentence"))

    final = await session.finish()
    show_log(session)
    show("final SessionResult", final)
    show("baseline update (empty: acted label)", session.baseline_update())


async def change_branch() -> None:
    print("\n\n##### Branch 2: two smile readings outside her usual -> one mid-call family message")
    clock, reader = FakeClock(), FakeSmileReader()
    session, adapter = new_session(reader, clock, DemoArms())
    first = await run_smile(session, adapter, clock, 0.11, reader)
    show("smile attempt 1 (outside)", first["reading"])
    print("publishes so far: %d" % len(adapter.published))
    second = await run_smile(session, adapter, clock, 0.09, reader)
    show("smile attempt 2 (outside, same side)", second["reading"])
    print("publishes after the second reading: %d" % len(adapter.published))
    for item in adapter.published:
        print("  [%s] message_id=%s  %s" % (item["phase"], item["message_id"], item["text"]))
    final = await session.finish()
    print("publishes after finish: %d (same message id: %s)" % (
        len(adapter.published), len({p["message_id"] for p in adapter.published}) == 1))
    show_log(session)
    show("final SessionResult", final)


# ---- whole calls through the call drivers --------------------------------------------------------------

async def drive(session: CallSession, adapter: FakeAdapter, clock: FakeClock, call: Any,
                fake_idle_time: bool = True) -> Dict[str, Any]:
    """The fake client around a driver: streams samples for every attempt the session starts. The first arms
    attempt loses the camera at 4 s; the camera is back 2 s later, so the second arms attempt completes.

    `fake_idle_time=False` (the real-Claude driver): fake time moves only while something waits for it, so a
    real network request is not timed out by a fast fake clock."""
    main = asyncio.ensure_future(call)
    seen: set = set()
    lost_at: Optional[float] = None
    restored = False
    while not main.done():
        await clock.settle()
        if main.done():
            break
        attempt_id = session.active_attempt_id
        if attempt_id is not None and attempt_id not in seen:
            seen.add(attempt_id)
            task = session.attempts[attempt_id].task
            if task == "arms" and lost_at is None:
                await stream(adapter, clock, attempt_id, 20, finish=False)
                session.handle_resource_event(resource_event("evt_camera_lost", "unavailable", 1, "track_ended"))
                lost_at = clock.now()
            else:
                await stream(adapter, clock, attempt_id, {"smile": 26, "arms": 76}.get(task, 0))
            continue
        if lost_at is not None and not restored and clock.now() - lost_at >= 2.0:
            session.handle_resource_event(resource_event("evt_camera_back", "available", 2, None))
            restored = True
            continue
        recovering = lost_at is not None and not restored
        if fake_idle_time or recovering:
            await clock.advance(0.1)
        else:
            await asyncio.sleep(0.02)
    return await main


def report(session: CallSession, adapter: FakeAdapter, final: Dict[str, Any]) -> None:
    show_log(session)
    show("lines played", adapter.said)
    show("own words played (say_own / ask_followup)", adapter.own_said)
    show("final SessionResult", final)
    show("baseline update (live label, per hand)", session.baseline_update())
    for item in adapter.published:
        print("  [%s] message_id=%s  %s" % (item["phase"], item["message_id"], item["text"]))


async def fixed_driver_demo() -> None:
    print("\n\n##### Whole call, fixed order (no model). Camera lost during arms A1 at 4 s, recovered, A2 completes")
    clock, reader = FakeClock(), FakeSmileReader()
    reader.corner = 0.01
    arms = StreamArms(FakeHandReader(), fake_decode, use_executor=False)
    session, adapter = new_session(reader, clock, arms, label="live", answers=ANSWERS)
    final = await drive(session, adapter, clock, run_call(session, adapter, "fixed"))
    report(session, adapter, final)


def scripted_claude_steps() -> List[Any]:
    steps: List[Any] = [tool_use_response("say", {"line_id": "greet"})]
    for question, value in (("q_medicine", "yes"), ("q_feeling", "I feel fine."), ("q_unusual", "Nothing unusual.")):
        steps.append(tool_use_response("ask", {"question_id": question}))
        if question == "q_medicine":  # v11: one short reaction of its own after the first answer
            steps.append(tool_use_response("say_own", {"text": "Good, thank you for telling me."}))
        if question == "q_feeling":  # v11: one clarifying question, then the answer is recorded
            steps.append(tool_use_response("ask_followup", {"question_id": question,
                                                            "text": "What is the best part of your day so far?"}))
        steps.append(tool_use_response("record_answer", {"question_id": question, "value": value}))
    steps.append(tool_use_response("run_task", {"task": "smile"}))
    steps.append(Hang())  # the next request never answers: the agent times out and the fixed order takes over
    return steps


async def fake_claude_demo() -> None:
    print("\n\n##### Whole call run by a scripted fake Claude that times out right after the smile")
    clock, reader = FakeClock(), FakeSmileReader()
    reader.corner = 0.01
    arms = StreamArms(FakeHandReader(), fake_decode, use_executor=False)
    session, adapter = new_session(reader, clock, arms, label="live", answers=ANSWERS)
    client = FakeClaudeClient(scripted_claude_steps(), clock)
    final = await drive(session, adapter, clock, run_call(session, adapter, "claude", client, agent_clock=clock))
    print("\n== agent log lines")
    for e in session.log:
        if e["kind"].startswith("agent_") or e["kind"] == "fallback_step":
            rest = {k: v for k, v in e.items() if k not in ("t", "kind")}
            print("%7.2f  %-16s %s" % (e["t"], e["kind"], json.dumps(rest, sort_keys=True)))
    report(session, adapter, final)


async def real_claude_demo() -> None:
    print("\n\n##### Whole call run by the REAL Claude (spends API credit; this path has never been run)")
    try:
        client = ClaudeClient()
    except ClaudeUnavailable as exc:
        print("Cannot start the real client: %s" % exc)
        return
    clock, reader = FakeClock(), FakeSmileReader()
    reader.corner = 0.01
    arms = StreamArms(FakeHandReader(), fake_decode, use_executor=False)
    session, adapter = new_session(reader, clock, arms, label="live", answers=ANSWERS)
    call = run_call(session, adapter, "claude", client, agent_clock=RealClock())
    final = await drive(session, adapter, clock, call, fake_idle_time=False)
    report(session, adapter, final)


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scripted demo of the call session.")
    parser.add_argument("--driver", choices=("fixed", "fake-claude", "claude"), default="fixed",
                        help="who runs the whole-call demo (default fixed)")
    parser.add_argument("--yes-real-claude", action="store_true",
                        help="required for --driver claude: confirms you accept that it spends API credit")
    return parser.parse_args(argv)


async def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    if args.driver == "claude" and not args.yes_real_claude:
        print("WARNING: --driver claude sends real requests to the Claude API and SPENDS API CREDIT.\n"
              "Nothing was started. Run again with --yes-real-claude if you accept that.")
        return 2
    await normal_branch()
    await change_branch()
    if args.driver == "fixed":
        await fixed_driver_demo()
    elif args.driver == "fake-claude":
        await fake_claude_demo()
    else:
        print("\nWARNING: the next part sends real requests to the Claude API and spends API credit.")
        await real_claude_demo()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
