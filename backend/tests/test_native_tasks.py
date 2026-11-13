"""Native reader lifecycle with controlled worker threads; no camera or providers."""

import asyncio
import threading
from types import SimpleNamespace

import pytest

pytest.importorskip("analysis.tasks.stream_arms")

from analysis.face_metric import FrameReading
from analysis.session import limits
from analysis.session.adapters import FakeAdapter
from analysis.session.call_session import CallSession
from analysis.session.models import TaskContext
from analysis.tasks.stream_arms import StreamArms
from analysis.tasks.stream_smile import StreamSmile
from analysis.tests.agent_helpers import ARMS_BASELINE, FakeHands

from checkin_backend.native_tasks import NativeStreamTask, protect_native_modules
from checkin_backend.runner import run_analysis_call


async def samples(times=(1000,)):
    for sequence, ms in enumerate(times):
        yield {"sequence": sequence, "captured_ms": ms, "format": "jpeg", "data": b"test"}


def context(name, attempt_id="A1", baseline=None):
    return TaskContext("call_test", attempt_id, name, baseline or {}, asyncio.Event())


class Reader:
    def __init__(self, name, *, blocked=False, fail=False, loop=None):
        self.name = name
        self.fail = fail
        self.loop = loop or asyncio.get_running_loop()
        self.started = asyncio.Event()
        self.release = threading.Event()
        if not blocked:
            self.release.set()
        self.active = False
        self.closed = 0
        self.closed_while_active = False
        self.close_thread = None
        self.hands = FakeHands()

    def __call__(self, image, ms, *side):
        self.active = True
        self.loop.call_soon_threadsafe(self.started.set)
        try:
            if not self.release.wait(5):
                raise AssertionError("test did not release inference")
            if self.fail:
                raise ValueError("inference failed")
            if self.name == "arms":
                return self.hands(image, ms, *side)
            return FrameReading(usable=False, timestamp_ms=ms, reason="no face")
        finally:
            self.active = False

    def close(self):
        self.closed_while_active |= self.active
        self.close_thread = threading.get_ident()
        self.closed += 1


def stream_task(name, **kwargs):
    cls = StreamArms if name == "arms" else StreamSmile
    return cls(decode=lambda sample: sample, **kwargs)


@pytest.mark.parametrize("name", ["arms", "smile"])
def test_cancellation_waits_for_inference_before_closing_even_when_cancelled_twice(name):
    async def scenario():
        reader = Reader(name, blocked=True)
        observations = []
        task_context = context(name)
        module = NativeStreamTask(stream_task(name), lambda: reader)
        task = asyncio.create_task(module.run(task_context, samples(), observations.append))
        try:
            await asyncio.wait_for(reader.started.wait(), 2)
            task_context.cancel_event.set()
            task.cancel()
            done, _ = await asyncio.wait({task}, timeout=0.03)
            assert not done and reader.closed == 0
            task.cancel()  # the supervisor calls stop again in its finally
            done, _ = await asyncio.wait({task}, timeout=0.03)
            assert not done and reader.closed == 0
        finally:
            reader.release.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 2)
        assert reader.closed == 1 and not reader.closed_while_active
        assert reader.close_thread != threading.get_ident()
        assert observations == []  # the cancelled measurement cannot publish

    asyncio.run(scenario())


@pytest.mark.parametrize("name", ["arms", "smile"])
def test_cancel_during_decode_cannot_create_a_reader_after_cleanup(name):
    async def scenario():
        loop = asyncio.get_running_loop()
        decoding = asyncio.Event()
        decoded = asyncio.Event()
        release = threading.Event()
        created = []

        def decode(sample):
            loop.call_soon_threadsafe(decoding.set)
            if not release.wait(5):
                raise AssertionError("test did not release decoding")
            loop.call_soon_threadsafe(decoded.set)
            return sample

        def factory():
            created.append(True)
            raise AssertionError("late sample opened a reader")

        source = stream_task(name)
        source._decode = decode
        module = NativeStreamTask(source, factory)
        task = asyncio.create_task(module.run(context(name), samples(), lambda obs: None))
        try:
            await asyncio.wait_for(decoding.wait(), 2)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 2)
        finally:
            release.set()
            await asyncio.wait_for(decoded.wait(), 2)
        # asyncio.run drains executor jobs before returning to the test.
        return created

    assert asyncio.run(scenario()) == []


@pytest.mark.parametrize("name", ["arms", "smile"])
def test_each_attempt_gets_a_fresh_reader_and_errors_still_clean_up(name):
    async def scenario():
        readers = []
        loop = asyncio.get_running_loop()

        def factory():
            reader = Reader(name, fail=not readers, loop=loop)
            readers.append(reader)
            return reader

        module = NativeStreamTask(stream_task(name), factory)
        with pytest.raises(ValueError, match="inference failed"):
            await module.run(context(name), samples(), lambda obs: None)
        result = await module.run(context(name, "A2"), samples(), lambda obs: None)
        assert result.attempt_id == "A2" and result.outcome == "completed"
        assert len(readers) == 2 and all(r.closed == 1 for r in readers)
        assert not any(r.closed_while_active for r in readers)

    asyncio.run(scenario())


def test_arms_keeps_the_same_definition_reading_and_per_hand_baseline_values():
    async def scenario():
        reader = Reader("arms")
        task = stream_task("arms", mirrored=True)
        module = NativeStreamTask(task, lambda: reader)
        assert module.definition() == task.definition()
        result = await module.run(context("arms", baseline=ARMS_BASELINE), samples(range(0, 15001, 200)),
                                  lambda obs: None)
        assert result.to_dict()["quality"] == {"status": "usable", "reasons": []}
        assert result.reading.outside is False
        assert module.attempt_values("A1") == {"left": 0.0, "right": 0.0}
        assert reader.closed == 1

    asyncio.run(scenario())


def test_session_keeps_analyzer_busy_until_cleanup_and_rejects_late_result(monkeypatch):
    async def scenario():
        monkeypatch.setattr(limits, "WORKER_STOP_WAIT_S", 0.02)
        reader = Reader("arms", blocked=True)
        module = NativeStreamTask(stream_task("arms"), lambda: reader)
        adapter = FakeAdapter()
        session = CallSession("call_test", "p_test", "Ona", adapter, {"arms": module},
                              initial_resources={"camera": "available"})
        adapter.session = session
        adapter.push_sample("A1", {"sequence": 0, "captured_ms": 1000, "data": b"test"})
        running = asyncio.create_task(session.run_task("arms"))
        try:
            await asyncio.wait_for(reader.started.wait(), 2)
            session.handle_resource_event({"event_id": "camera_lost", "type": "resource.changed",
                                           "resource": "camera", "state": "unavailable", "sequence": 1,
                                           "reason": "track_ended", "command_id": None})
            stopped = await asyncio.wait_for(running, 2)
            assert stopped["outcome"] == "interrupted"
            assert stopped["reading"] is None and reader.closed == 0
            session.handle_resource_event({"event_id": "camera_back", "type": "resource.changed",
                                           "resource": "camera", "state": "available", "sequence": 2,
                                           "reason": None, "command_id": None})
            refused = await session.run_task("arms")
            assert refused["quality"]["reasons"] == ["analyzer_busy"]
            assert session.records_for("arms")[0].usable is False
        finally:
            reader.release.set()
            await asyncio.gather(running, return_exceptions=True)
            if session._busy_worker is not None:
                await asyncio.gather(session._busy_worker, return_exceptions=True)
        assert reader.closed == 1 and not reader.closed_while_active
        assert session.attempts["A1"].result == stopped
        adapter.push_sample("A2", {"sequence": 0, "captured_ms": 1000, "data": b"test"})
        adapter.finish_capture("A2")
        retry = await asyncio.wait_for(session.run_task("arms"), 2)
        assert retry["attempt_id"] == "A2" and retry["outcome"] == "completed"

    asyncio.run(scenario())


def test_injected_and_non_native_modules_are_kept():
    injected = StreamArms(frame_reader=lambda *args: None)
    custom = SimpleNamespace(name="custom")
    modules = {"arms": injected, "custom": custom}
    assert protect_native_modules(modules) == modules


def test_real_runner_installs_managed_readers_and_preserves_mirroring(monkeypatch):
    from analysis import backend_entry
    from analysis.tasks import stream_arms, stream_smile

    async def scenario():
        created = []
        loop = asyncio.get_running_loop()

        def factory(name):
            def make(mirrored=False):
                created.append((name, mirrored))
                return Reader(name, loop=loop)
            return make

        monkeypatch.setattr(stream_arms, "ProductionHandReader", factory("arms"))
        monkeypatch.setattr(stream_smile, "ProductionFrameReader", factory("smile"))
        monkeypatch.setenv("UW_MIRRORED", "on")

        async def run(link, *, modules, **kwargs):
            assert set(modules) == {"arms", "smile"}
            for name, module in modules.items():
                await module.run(context(name), samples(), lambda obs: None)
            return {"status": "incomplete"}

        # Only decoding is replaced; this exercises the runner's real default
        # module selection and the production reader constructors it captures.
        monkeypatch.setattr(stream_arms, "_default_decoder", lambda: lambda sample: sample)
        monkeypatch.setattr(stream_smile, "_default_decoder", lambda: lambda sample: sample)
        monkeypatch.setattr(backend_entry, "run_backend_call", run)
        link = SimpleNamespace(person=SimpleNamespace(person_id="p_test"))
        assert await run_analysis_call(link, "real") == {"status": "incomplete"}
        assert sorted(created) == [("arms", True), ("smile", True)]

    asyncio.run(scenario())
