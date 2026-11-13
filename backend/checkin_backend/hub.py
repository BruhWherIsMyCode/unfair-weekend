"""Device registry and per-device server-sent event streams.

A device is a phone: `parent` (the person who is called) or `family`. Every event sent to a device is kept in a
short history, so a client that reconnects with `Last-Event-ID` receives what it missed. A client that connects
without it receives only events that are still active (a ringing call, a command that has not finished), so an
old command is never executed twice after a fresh start.
"""

import asyncio
import itertools
import re
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

ROLES = ("parent", "family")
ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,128}")
LISTENER_QUEUE_MAX = 256


def valid_id(value: object) -> bool:
    return isinstance(value, str) and ID_PATTERN.fullmatch(value) is not None


@dataclass(frozen=True, slots=True)
class Device:
    device_id: str
    role: str
    person_id: str | None
    family_id: str | None


@dataclass(slots=True)
class Event:
    seq: int
    event_id: str
    type: str
    data: dict[str, Any]
    still_active: Callable[[], bool] | None

    def active(self) -> bool:
        if self.still_active is None:
            return False
        return bool(self.still_active())


class UnknownDevice(LookupError):
    pass


class DeviceHub:
    def __init__(self, history: int = 200) -> None:
        self._devices: dict[str, Device] = {}
        self._history: dict[str, deque[Event]] = {}
        self._listeners: dict[str, set[asyncio.Queue[Event | None]]] = {}
        self._seq = itertools.count(1)
        self._history_size = history

    def register(self, device: Device) -> None:
        if device.role not in ROLES:
            raise ValueError("role must be parent or family")
        self._devices[device.device_id] = device
        self._history.setdefault(device.device_id, deque(maxlen=self._history_size))

    def get(self, device_id: str) -> Device:
        try:
            return self._devices[device_id]
        except KeyError:
            raise UnknownDevice(device_id) from None

    def parents_of(self, person_id: str) -> list[str]:
        found = []
        for device in self._devices.values():
            if device.role == "parent" and device.person_id == person_id:
                found.append(device.device_id)
        return found

    def families_of(self, family_id: str) -> list[str]:
        found = []
        for device in self._devices.values():
            if device.role == "family" and device.family_id == family_id:
                found.append(device.device_id)
        return found

    def send(
        self,
        device_id: str,
        event_type: str,
        data: dict[str, Any],
        *,
        event_id: str | None = None,
        still_active: Callable[[], bool] | None = None,
    ) -> Event:
        self.get(device_id)
        seq = next(self._seq)
        if event_id is None:
            event_id = data.get("event_id") or f"ev_{seq}"
        event = Event(seq, event_id, event_type, data, still_active)
        self._history[device_id].append(event)
        for queue in list(self._listeners.get(device_id, ())):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                self._drop(device_id, queue)
        return event

    def send_many(self, device_ids: list[str], event_type: str, data: dict[str, Any], **kwargs: Any) -> int:
        for device_id in device_ids:
            self.send(device_id, event_type, data, **kwargs)
        return len(device_ids)

    def subscribe(
        self, device_id: str, last_event_id: str | None = None
    ) -> tuple[list[Event], asyncio.Queue[Event | None]]:
        self.get(device_id)
        history = list(self._history[device_id])
        replay = self._replay(history, last_event_id)
        queue: asyncio.Queue[Event | None] = asyncio.Queue(maxsize=LISTENER_QUEUE_MAX)
        self._listeners.setdefault(device_id, set()).add(queue)
        return replay, queue

    def unsubscribe(self, device_id: str, queue: asyncio.Queue[Event | None]) -> None:
        self._listeners.get(device_id, set()).discard(queue)

    def close(self) -> None:
        for device_id, queues in self._listeners.items():
            for queue in list(queues):
                self._drop(device_id, queue)

    def _replay(self, history: list[Event], last_event_id: str | None) -> list[Event]:
        if last_event_id is not None:
            for index, event in enumerate(history):
                if event.event_id == last_event_id:
                    return history[index + 1 :]
        return [event for event in history if event.active()]

    def _drop(self, device_id: str, queue: asyncio.Queue[Event | None]) -> None:
        self._listeners.get(device_id, set()).discard(queue)
        while not queue.empty():
            queue.get_nowait()
        queue.put_nowait(None)
