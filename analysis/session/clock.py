"""An injectable monotonic clock. Every watchdog and wait in the session goes through it.

RealClock uses time.monotonic and asyncio.sleep. FakeClock is for tests and the scripted demo: time
moves only when `advance` is awaited, so no test spends wall-clock seconds.
"""
from __future__ import annotations

import asyncio
import heapq
import itertools
import time
from typing import List, Tuple


class RealClock:
    def now(self) -> float:
        return time.monotonic()

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(max(0.0, seconds))


class FakeClock:
    """Manual clock. `await clock.advance(dt)` moves time forward, waking sleepers in deadline order."""

    SETTLE_ROUNDS = 25  # event-loop turns given to woken tasks after each wake-up

    def __init__(self, start: float = 0.0) -> None:
        self._now = float(start)
        self._sleepers: List[Tuple[float, int, "asyncio.Future"]] = []
        self._counter = itertools.count()

    def now(self) -> float:
        return self._now

    async def sleep(self, seconds: float) -> None:
        if seconds <= 0:
            await asyncio.sleep(0)
            return
        fut = asyncio.get_running_loop().create_future()
        heapq.heappush(self._sleepers, (self._now + seconds, next(self._counter), fut))
        await fut

    async def settle(self) -> None:
        for _ in range(self.SETTLE_ROUNDS):
            await asyncio.sleep(0)

    async def advance(self, seconds: float) -> None:
        target = self._now + seconds
        await self.settle()
        while self._sleepers and self._sleepers[0][0] <= target:
            deadline, _, fut = heapq.heappop(self._sleepers)
            self._now = max(self._now, deadline)
            if not fut.done():
                fut.set_result(None)
            await self.settle()
        self._now = target
        await self.settle()
