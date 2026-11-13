"""Own native readers on the backend's streamed-task path.

Cancelling run_in_executor only cancels its asyncio future; inference may still
be using the reader. Inject a reader per attempt so the analysis task does not
close it in its own finally block. Serialize close with inference on a worker,
and keep the module pending until cleanup finishes (CallSession's busy slot).
"""

import asyncio
import threading
from collections.abc import Callable
from copy import copy
from functools import partial
from typing import Any


class _Reader:
    def __init__(self, factory: Callable[[], Any]) -> None:
        self._factory = factory
        self._reader: Any = None
        self._lock = threading.Lock()
        self._stopped = threading.Event()

    def __call__(self, *args: Any) -> Any:
        with self._lock:
            # A cancelled executor job may still be decoding, or waiting for
            # this lock. It must not create/reopen native objects after stop.
            if self._stopped.is_set():
                return None
            if self._reader is None:
                self._reader = self._factory()
            return self._reader(*args)

    def stop(self) -> None:
        self._stopped.set()

    def close(self) -> None:
        with self._lock:
            if self._reader is not None:
                reader, self._reader = self._reader, None
                reader.close()


class NativeStreamTask:
    def __init__(self, task: Any, reader_factory: Callable[[], Any]) -> None:
        self.name = task.name
        self._task = task
        self._reader_factory = reader_factory

    def definition(self) -> dict[str, Any]:
        return self._task.definition()

    def attempt_values(self, attempt_id: str) -> dict[str, float]:
        values = getattr(self._task, "attempt_values", None)
        return values(attempt_id) if values is not None else {}

    async def run(self, context: Any, samples: Any, emit_observation: Any) -> Any:
        reader = _Reader(self._reader_factory)
        # Preserve the original task's timing/decoder/settings and shared
        # per-attempt value cache, with an independent injected frame reader.
        task = copy(self._task)
        task._frame_reader = reader
        try:
            return await task.run(context, samples, emit_observation)
        finally:
            reader.stop()
            cleanup = asyncio.get_running_loop().run_in_executor(None, reader.close)
            cancelled = False
            while True:
                try:
                    await asyncio.shield(cleanup)
                    break
                except asyncio.CancelledError:
                    # CallSession can cancel twice while enforcing its stop
                    # deadline. Leave this task busy until native use ends.
                    cancelled = True
            if cancelled:
                raise asyncio.CancelledError


def protect_native_modules(modules: dict[str, Any]) -> dict[str, Any]:
    """Keep injected/test modules; manage the two default production readers."""
    from analysis.tasks.stream_arms import ProductionHandReader, StreamArms
    from analysis.tasks.stream_smile import ProductionFrameReader, StreamSmile

    protected = dict(modules)
    for name, task in modules.items():
        if type(task) is StreamArms and task._frame_reader is None:
            protected[name] = NativeStreamTask(task, partial(ProductionHandReader, mirrored=task.mirrored))
        elif type(task) is StreamSmile and task._frame_reader is None:
            protected[name] = NativeStreamTask(task, partial(ProductionFrameReader, mirrored=task.mirrored))
    return protected
