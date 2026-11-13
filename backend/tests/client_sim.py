"""A scripted parent client that speaks the real HTTP/SSE protocol. Used by tests and by hand:

    uv run --locked --extra dev python -m tests.client_sim http://127.0.0.1:8000 p_demo

It answers the ring, acknowledges playback after the announced duration (scaled), uploads a tiny WAV for each
answer, streams synthetic JPEG frames / silent PCM for tasks and can drop the camera mid-task.
"""

import asyncio
import base64
import json
import struct
import sys
from dataclasses import dataclass, field
from typing import Any

import httpx


def tiny_jpeg(width: int = 8, height: int = 8) -> bytes:
    sof = b"\xff\xc0" + struct.pack(">HBHHB", 11, 8, height, width, 1) + b"\x01\x11\x00"
    return b"\xff\xd8" + sof + b"\xff\xd9"


def tiny_wav(seconds: float = 0.2) -> bytes:
    pcm = bytes(int(16000 * seconds) * 2)
    header = b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVEfmt " + struct.pack(
        "<IHHIIHH", 16, 1, 1, 16000, 32000, 2, 16) + b"data" + struct.pack("<I", len(pcm))
    return header + pcm


@dataclass
class SimClient:
    base: str
    key: str
    person_id: str = "p_demo"
    device_id: str = "dev_parent"
    time_scale: float = 0.01
    drop_camera_on: str | None = None
    drop_after_samples: int = 5
    restore_camera_after_s: float | None = 0.2
    hang_up_after: str | None = None
    seen: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    said: list[str] = field(default_factory=list)
    call_id: str | None = None
    _event_n: int = 0
    _resource_seq: int = 0
    _dropped: bool = False
    _cancelled: set[str] = field(default_factory=set)
    _tasks: set = field(default_factory=set)

    def _id(self, prefix: str) -> str:
        self._event_n += 1
        return f"{prefix}_{self._event_n}"

    async def post_event(self, http: httpx.AsyncClient, **event: Any) -> httpx.Response:
        event.setdefault("event_id", self._id("ev"))
        if event["type"] != "resource.changed":
            event.setdefault("command_id", None)
            event.setdefault("error", None)
        r = await http.post(f"/v1/calls/{self.call_id}/client-events", json=event)
        return r

    async def resource(self, http: httpx.AsyncClient, resource: str, state: str, command_id: str | None = None):
        self._resource_seq += 1
        await self.post_event(http, type="resource.changed", command_id=command_id, resource=resource, state=state,
                              sequence=self._resource_seq, reason=None if state == "available" else "track_ended")

    async def run(self, stop_on_end: bool = True) -> None:
        headers = {"X-Demo-Key": self.key}
        async with httpx.AsyncClient(base_url=self.base, headers=headers, timeout=30) as http:
            r = await http.post("/v1/devices", json={"device_id": self.device_id, "role": "parent",
                                                     "person_id": self.person_id})
            r.raise_for_status()
            try:
                async with http.stream("GET", f"/v1/devices/{self.device_id}/events", timeout=None) as stream:
                    event_type = None
                    async for line in stream.aiter_lines():
                        if line.startswith("event: "):
                            event_type = line[7:]
                        elif line.startswith("data: "):
                            data = json.loads(line[6:])
                            self.seen.append((event_type, data))
                            done = await self.handle(http, event_type, data)
                            if done and stop_on_end:
                                return
            finally:
                for task in list(self._tasks):
                    task.cancel()
                await asyncio.gather(*self._tasks, return_exceptions=True)

    async def handle(self, http: httpx.AsyncClient, event_type: str, data: dict[str, Any]) -> bool:
        if event_type == "call.incoming":
            self.call_id = data["call_id"]
            r = await http.post(f"/v1/calls/{self.call_id}/answer")
            r.raise_for_status()
            await self.resource(http, "camera", "available")
            await self.resource(http, "microphone", "available")
            await self.post_event(http, type="client.ready")
            return False
        if event_type == "call.ended":
            return True
        if event_type == "command.cancel":
            self._cancelled.add(data["command_id"])
            await self.post_event(http, type="command.cancelled", command_id=data["command_id"])
            return False
        if event_type != "command.execute":
            return False
        command = data["command"]
        task = asyncio.create_task(self.execute_quietly(http, command))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return False

    async def execute_quietly(self, http: httpx.AsyncClient, command: dict[str, Any]) -> None:
        try:
            await self.execute(http, command)
        except httpx.HTTPError as exc:
            self.seen.append(("client_error", {"command_id": command["command_id"], "error": repr(exc)}))

    async def execute(self, http: httpx.AsyncClient, command: dict[str, Any]) -> None:
        cid = command["command_id"]
        if command["kind"] == "say":
            audio = await http.get(command["audio_path"])
            audio.raise_for_status()
            self.said.append(command["line_id"])
            await asyncio.sleep(command["duration_ms"] / 1000 * self.time_scale)
            await self.post_event(http, type="playback.completed", command_id=cid)
            if self.hang_up_after == command["line_id"]:
                await self.post_event(http, type="hangup")
            return
        purpose = command["purpose"]
        if purpose["kind"] == "answer":
            r = await http.post(f"/v1/calls/{self.call_id}/captures/{cid}", content=tiny_wav(),
                                headers={"Content-Type": "audio/wav"})
            r.raise_for_status()
            return
        await self.stream_task(http, cid, purpose["task"], command["settings"])

    async def stream_task(self, http: httpx.AsyncClient, attempt_id: str, task: str, settings: dict[str, Any]):
        await self.post_event(http, type="capture.started", command_id=attempt_id)
        period = settings["sample_period_ms"]
        count = settings["duration_ms"] // period
        last = -1
        for i in range(count):
            if attempt_id in self._cancelled:
                return
            if task == self.drop_camera_on and not self._dropped and i == self.drop_after_samples:
                self._dropped = True
                await self.resource(http, "camera", "unavailable", attempt_id)
                if self.restore_camera_after_s is not None:
                    await asyncio.sleep(self.restore_camera_after_s)
                    await self.resource(http, "camera", "available")
                return
            if settings["format"] == "jpeg":
                payload = tiny_jpeg()
            else:
                payload = bytes(8000)
            r = await http.post(f"/v1/calls/{self.call_id}/attempts/{attempt_id}/samples",
                                json={"sequence": i, "captured_ms": i * period, "format": settings["format"],
                                      "data_base64": base64.b64encode(payload).decode()})
            if r.status_code == 410:
                return
            r.raise_for_status()
            last = i
            await asyncio.sleep(period / 1000 * self.time_scale * 10)
        await http.post(f"/v1/calls/{self.call_id}/attempts/{attempt_id}/finish",
                        json={"last_sequence": last, "captured_until_ms": max(0, last) * period})


async def main() -> None:
    base, person = sys.argv[1], sys.argv[2]
    import os

    sim = SimClient(base=base, key=os.environ["DEMO_KEY"], person_id=person, time_scale=1.0)
    task = asyncio.create_task(sim.run())
    await asyncio.sleep(0.5)
    async with httpx.AsyncClient(base_url=base, headers={"X-Demo-Key": sim.key}) as http:
        r = await http.post("/v1/calls", json={"person_id": person})
        print("start:", r.status_code, r.text)
    await task
    print("said:", sim.said)


if __name__ == "__main__":
    asyncio.run(main())
