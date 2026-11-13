"""Scripted demo of the call session, no camera, no Claude, no network.

    python -m analysis.session.demo_call

Runs the architecture plan's section 7 example against the fake adapters and a fake smile reader with made-up
numbers: greet, smile completed within her usual, arms attempt A1 interrupted by camera loss at 4 s,
recovery, a new attempt A2, finish. Then the alternate branch: two smile readings outside her usual give one
mid-call family message. Arms and sentence are not implemented in this version, so A2 ends `failed`; the
demo says so. Written, not run.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List

from analysis.face_metric import FrameReading
from analysis.session.adapters import FakeAdapter
from analysis.session.call_session import CallSession
from analysis.session.clock import FakeClock
from analysis.session.models import Metric, Quality, TaskObservation
from analysis.tasks.stream_arms import StreamArms
from analysis.tasks.stream_sentence import StreamSentence
from analysis.tasks.stream_smile import StreamSmile

BASELINE = {"snapshot_id": "baseline_demo", "ready": True, "sessions": 12, "needed": 5,
            "usual_low": -0.02, "usual_high": 0.03}  # made-up numbers


def make_sample(i: int) -> Dict[str, Any]:
    return {"sequence": i, "captured_ms": i * 200, "format": "jpeg", "data_base64": "AAAA"}


def fake_decode(sample: Dict[str, Any]) -> Any:
    return sample  # the fake reader reads the timestamp only


class FakeSmileReader:
    """A made-up face: relaxed for the first 1.5 s, then smiling with the configured mouth-corner height."""

    def __init__(self) -> None:
        self.corner = 0.0

    def __call__(self, image: Any, ts_ms: int) -> FrameReading:
        smiling = ts_ms >= 1500
        height = self.corner if smiling else 0.0
        return FrameReading(usable=True, unit=100.0, below_left=1.0 + height / 2, below_right=1.0 - height / 2,
                            corner_height=height, smile_strength=0.9 if smiling else 0.1, timestamp_ms=ts_ms)


class DemoArms:
    """Stand-in for the first arms attempt only: it reports two observations, then waits to be stopped.
    Its second run is the real, not-implemented stub."""

    name = "arms"

    def __init__(self) -> None:
        self.runs = 0
        self.stub = StreamArms()

    def definition(self) -> Dict[str, Any]:
        return self.stub.definition()

    async def run(self, context: Any, samples: Any, emit_observation: Any) -> Any:
        self.runs += 1
        if self.runs > 1:
            return await self.stub.run(context, samples, emit_observation)
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


def new_session(corner_reader: FakeSmileReader, clock: FakeClock, arms: Any) -> tuple:
    adapter = FakeAdapter(person_name="Ona")
    modules = {"smile": StreamSmile(corner_reader, fake_decode, use_executor=False), "arms": arms,
               "sentence": StreamSentence()}
    session = CallSession("call_demo", "p_demo", "Ona", adapter, modules, {"smile": BASELINE}, clock, label="acted",
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


def show_log(session: CallSession) -> None:
    print("\n== event log")
    for e in session.log:
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

    show("arms A2 (arms is not implemented in this version, so it ends failed)", await session.run_task("arms"))
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


async def main() -> None:
    await normal_branch()
    await change_branch()


if __name__ == "__main__":
    asyncio.run(main())
