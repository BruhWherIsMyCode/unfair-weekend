"""ClientLink: one connected call's command channel to the parent client.

It implements the CallAdapter protocol that Agent 3's CallSession expects (analysis/session/adapters.py):
say, listen, start_capture, cancel_capture, samples, publish, send_progress, and the optional say_text (the call
agent's own words, line_id `agent_remark`). Commands go to the parent
device over SSE (`command.execute`, `command.cancel`, `task.progress`); acknowledgments, answer uploads and
task samples come back through HTTP routes that call the `on_*` methods below.

Everything runs on one asyncio event loop. Client events are handled synchronously, so an acknowledgment and a
cancellation cannot interleave halfway.
"""

import asyncio
import hashlib
import json
import logging
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from .delivery import FamilyDelivery
from .avatar import ClipGateway
from .hub import DeviceHub
from .media import MAX_ATTEMPT_BYTES
from .logging_config import log_event
from .people import Person
from .speech import SpeechError, SpeechGateway, load_script, render_line
from .store import NoStore, Store
from .session_logging import SessionEventLog

log = logging.getLogger(__name__)

# Development only: when set, every task frame is also written to <dir>/<call_id>/<task>_<attempt_id>/<ms>.jpg and
# the family summary to <dir>/<call_id>/summary_<phase>.json, so a try can be replayed through the analysis later
# (python -m checkin_backend.replay_attempt). These are pictures of a person: point it outside the repo, delete after.
DEBUG_FRAMES_ENV = "DEBUG_FRAMES_DIR"

ANSWER_CAPTURE = {
    "media_kind": "audio",
    "duration_ms": 15000,
    "prepare_ms": 0,
    "stop_on_silence_ms": 1500,
    "format": None,
    "sample_period_ms": None,
}
PLAYBACK_GRACE_S = 10.0
CAPTURE_START_GRACE_S = 5.0
UPLOAD_GRACE_S = 10.0
SAMPLE_QUEUE_MAX = 64


class CallEnded(RuntimeError):
    pass


class PlaybackFailed(RuntimeError):
    pass


class LinkRejected(Exception):
    def __init__(self, code: str, message: str, status: int) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


class SessionEvents(Protocol):
    def handle_capture_started(self, attempt_id: str) -> str: ...
    def handle_cancel_ack(self, attempt_id: str) -> None: ...
    def handle_command_failed(self, attempt_id: str, error_code: str) -> None: ...
    def handle_resource_event(self, event: Any) -> str: ...
    def hang_up(self) -> None: ...


@dataclass(slots=True)
class Command:
    command_id: str
    kind: str
    payload: dict[str, Any]
    done: asyncio.Future[Any]
    audio: bytes | None = None
    upload_print: str | None = None
    finished: bool = False
    video: Path | None = None


@dataclass(slots=True)
class Capture:
    attempt_id: str
    task: str
    media_format: str
    queue: asyncio.Queue[dict[str, Any] | None]
    prints: dict[int, str] = field(default_factory=dict)
    last_sequence: int = -1
    total_bytes: int = 0
    closed: bool = False
    finish_print: str | None = None


def fingerprint(*parts: object) -> str:
    digest = hashlib.sha256()
    for part in parts:
        if isinstance(part, bytes):
            digest.update(part)
        else:
            digest.update(repr(part).encode())
        digest.update(b"|")
    return digest.hexdigest()


# Which task's frames show a confirmed sign. "arm" also covers the one-hand arm tasks.
CLIP_TASKS = {"face": "smile", "arm": "arm"}
MAX_CLIP_FRAMES_PER_ATTEMPT = 100


class ClientLink:
    def __init__(
        self,
        *,
        call_id: str,
        person: Person,
        hub: DeviceHub,
        speech: SpeechGateway,
        delivery: FamilyDelivery,
        script: dict[str, str] | None = None,
        clips: ClipGateway | None = None,
        store: Store | None = None,
    ) -> None:
        self.call_id = call_id
        self.store = store if store is not None else NoStore()
        self.person = person
        self.hub = hub
        self.speech = speech
        self.delivery = delivery
        self.clips = clips
        self.script = script if script is not None else load_script()
        self.session: SessionEvents | None = None
        self.ready = asyncio.Event()
        self.resources: dict[str, str] = {}
        self.ended = False
        self.log: list[dict[str, Any]] = []
        self._commands: dict[str, Command] = {}
        self._captures: dict[str, Capture] = {}
        self._events: dict[str, str] = {}
        self._pending_resource_events: list[dict[str, Any]] = []
        # Camera frames per attempt, in memory only, for the family's alert clip (delivery.ClipShelf).
        self._frames: dict[str, tuple[str, list[tuple[int, bytes]]]] = {}
        root = os.environ.get(DEBUG_FRAMES_ENV, "").strip()
        self._frames_dir = Path(root) / call_id if root else None

    # ---- helpers -------------------------------------------------------------------------------

    def _note(self, kind: str, **fields: Any) -> None:
        entry = {"kind": kind}
        entry.update(fields)
        self.log.append(entry)
        level = logging.WARNING if kind.endswith("_failed") or kind == "command_timeout" else logging.INFO
        log_event(log, level, f"client.{kind}", call_id=self.call_id,
                  **{key: value for key, value in fields.items() if key != "error"})

    def _devices(self) -> list[str]:
        return self.hub.parents_of(self.person.person_id)

    def _push(self, event_type: str, data: dict[str, Any], command: Command | None = None) -> None:
        still_active = None
        if command is not None:
            still_active = lambda: not command.finished and not self.ended  # noqa: E731
        self.hub.send_many(self._devices(), event_type, data, event_id=data["event_id"], still_active=still_active)

    def _new_command(self, kind: str, payload: dict[str, Any], command_id: str | None = None) -> Command:
        if self.ended:
            raise CallEnded("call ended")
        command_id = command_id or f"cmd_{uuid4().hex[:16]}"
        if command_id in self._commands:
            raise LinkRejected("conflict", "Command ID already used in this call.", 409)
        loop = asyncio.get_running_loop()
        command = Command(command_id, kind, payload, loop.create_future())
        self._commands[command_id] = command
        return command

    def _execute(self, command: Command, deadline_s: float) -> None:
        self._note("command_sent", command_id=command.command_id, command_kind=command.kind, deadline_s=deadline_s)
        self.store.record_command(self.call_id, command.payload, deadline_s)
        self._push(
            "command.execute",
            {
                "event_id": f"exec_{command.command_id}",
                "call_id": self.call_id,
                "type": "command.execute",
                "command": command.payload,
            },
            command,
        )

    def _settle(self, command: Command, result: Any = None, error: BaseException | None = None) -> None:
        command.finished = True
        if command.done.done():
            return
        if error is not None:
            command.done.set_exception(error)
            command.done.exception()
        else:
            command.done.set_result(result)

    async def _await(self, command: Command, timeout_s: float) -> Any:
        try:
            return await asyncio.wait_for(asyncio.shield(command.done), timeout_s)
        except TimeoutError:
            self._note("command_timeout", command_id=command.command_id, command_kind=command.kind,
                       timeout_s=timeout_s)
            self._settle(command, error=PlaybackFailed("timeout"))
            self._cancel_on_client(command.command_id, "timeout")
            raise PlaybackFailed(f"{command.kind} {command.command_id} timed out") from None

    def _cancel_on_client(self, command_id: str, reason: str) -> None:
        if self.ended and reason != "call_ended":
            return
        self.store.record_command_cancel(self.call_id, command_id, reason)
        self.hub.send_many(
            self._devices(),
            "command.cancel",
            {
                "event_id": f"cancel_{command_id}",
                "call_id": self.call_id,
                "type": "command.cancel",
                "command_id": command_id,
                "reason": reason,
            },
            event_id=f"cancel_{command_id}",
        )

    # ---- CallAdapter -----------------------------------------------------------------------------

    async def say(self, line_id: str) -> None:
        await self._say(line_id, render_line(self.script, line_id, self.person.name))

    async def say_text(self, text: str) -> None:
        """The call agent's own words (Agent 3's optional `say_text` capability, already checked by the call code).

        Like `say`, but the line id is `agent_remark` and the caption is the given text. The speech gateway generates
        the voice on the fly (cached by text like any other line; caption-only without a key). A video clip is
        attached only if one is already ready, never generated during the call."""
        text = " ".join(str(text).split())[:400]
        if not text:
            raise PlaybackFailed("empty text")
        await self._say("agent_remark", text)

    async def _say(self, line_id: str, text: str) -> None:
        try:
            prepared = await self.speech.prepare(text)
        except SpeechError as exc:
            self._note("speech_failed", line=line_id, error=str(exc), error_type=type(exc).__name__)
            raise PlaybackFailed(str(exc)) from None
        command = self._new_command("say", {})
        command.audio = prepared.audio
        if prepared.voiced and self.clips is not None:
            command.video = self.clips.ready(prepared.audio)
        command.payload = {
            "command_id": command.command_id,
            "kind": "say",
            "line_id": line_id,
            "text": text,
            "audio_path": f"/v1/calls/{self.call_id}/speech/{command.command_id}",
            "duration_ms": prepared.duration_ms,
        }
        if command.video is not None:
            command.payload["video_path"] = f"/v1/calls/{self.call_id}/video/{command.command_id}"
        self._note("say", line=line_id, command_id=command.command_id, voiced=prepared.voiced)
        wait_s = prepared.duration_ms / 1000 + PLAYBACK_GRACE_S
        self._execute(command, wait_s)
        await self._await(command, wait_s)

    async def listen(self, question_id: str, timeout_s: float) -> str | None:
        question = question_id.removeprefix("q_")
        command = self._new_command("capture", {})
        command.payload = {
            "command_id": command.command_id,
            "kind": "capture",
            "purpose": {"kind": "answer", "question_id": question},
            "settings": dict(ANSWER_CAPTURE),
            "transport": "answer_upload",
        }
        self._note("listen", question=question, command_id=command.command_id)
        wait_s = max(timeout_s, ANSWER_CAPTURE["duration_ms"] / 1000) + UPLOAD_GRACE_S
        self._execute(command, wait_s)
        try:
            upload = await self._await(command, wait_s)
        except PlaybackFailed:
            return None
        if upload is None:
            return None
        audio, content_type = upload
        try:
            text = await self.speech.transcribe(audio, content_type)
        except SpeechError as exc:
            self._note("transcription_failed", command_id=command.command_id, error=str(exc),
                       error_type=type(exc).__name__)
            return None
        finally:
            del audio
        self._note("heard", command_id=command.command_id, heard=text is not None)
        return text

    async def start_capture(self, attempt_id: str, task: str, settings: dict[str, Any]) -> None:
        if attempt_id in self._captures:
            raise LinkRejected("conflict", "Attempt already started.", 409)
        command = self._new_command("capture", {}, command_id=attempt_id)
        command.payload = {
            "command_id": attempt_id,
            "kind": "capture",
            "purpose": {"kind": "task", "task": task},
            "settings": dict(settings),
            "transport": "sample_post",
        }
        self._captures[attempt_id] = Capture(
            attempt_id, task, settings["format"], asyncio.Queue(maxsize=SAMPLE_QUEUE_MAX)
        )
        self._note("capture", attempt_id=attempt_id, task=task)
        window_s = (settings.get("prepare_ms", 0) + settings.get("duration_ms", 0)) / 1000
        self._execute(command, window_s + CAPTURE_START_GRACE_S + UPLOAD_GRACE_S)

    async def cancel_capture(self, attempt_id: str, reason: str) -> None:
        capture = self._captures.get(attempt_id)
        command = self._commands.get(attempt_id)
        if command is not None:
            self._settle(command, None)
        if capture is not None:
            self._close_capture(capture)
        self._note("cancel", attempt_id=attempt_id, reason=reason)
        self._cancel_on_client(attempt_id, reason)

    def samples(self, attempt_id: str) -> AsyncIterator[dict[str, Any]]:
        capture = self._captures[attempt_id]

        async def stream() -> AsyncIterator[dict[str, Any]]:
            while True:
                item = await capture.queue.get()
                if item is None:
                    return
                yield item

        return stream()

    async def publish(self, summary: dict[str, Any], phase: str, message_id: str | None = None) -> dict[str, Any]:
        kind = summary.get("status", "incomplete")
        clip = self.alert_frames(summary.get("signs")) if kind == "change_noticed" else None
        self._save_debug(f"summary_{phase}.json", json.dumps(summary, indent=1, default=str).encode())
        message = self.delivery.publish(
            self.call_id, self.person, kind, phase, summary.get("signs"), summary.get("concern"), summary=summary,
            clip=clip,
        )
        return {"message_id": message.message_id, "delivery": "stored"}

    def alert_frames(self, signs: list[dict[str, Any]] | None) -> list[list[tuple[int, bytes]]] | None:
        """The camera frames of every attempt at the task behind the first confirmed face or arm sign, oldest first:
        both tries, as the family message says. None for speech or when no frames were kept."""
        for sign in signs or []:
            if sign.get("confirmed") is not True:
                continue
            prefix = CLIP_TASKS.get(sign.get("sign"))
            if prefix is None:
                continue
            attempts = [frames for task, frames in self._frames.values() if task.startswith(prefix) and frames]
            if attempts:
                return attempts
        return None

    def release_frames(self) -> None:
        self._frames.clear()

    async def send_progress(self, event: dict[str, Any]) -> None:
        if self.ended:
            return
        self.hub.send_many(self._devices(), "task.progress", event, event_id=event["event_id"])

    # ---- client input ---------------------------------------------------------------------------

    def speech_audio(self, command_id: str) -> bytes:
        return self.playback_command(command_id).audio

    def playback_command(self, command_id: str) -> Command:
        command = self._commands.get(command_id)
        if command is None or command.kind != "say":
            raise LinkRejected("not_found", "No such speech command.", 404)
        if command.finished or command.audio is None:
            raise LinkRejected("expired", "This line is no longer active.", 410)
        return command

    def video_asset(self, command_id: str) -> Path:
        command = self.playback_command(command_id)
        if command.video is None:
            raise LinkRejected("not_found", "This line has no prepared video.", 404)
        return command.video

    def on_event(self, event: dict[str, Any]) -> None:
        event_id = event["event_id"]
        print_ = fingerprint(sorted(event.items()))
        seen = self._events.get(event_id)
        if seen is not None:
            if seen != print_:
                raise LinkRejected("conflict", "Event ID reused with different content.", 409)
            return
        self._events[event_id] = print_
        kind = event["type"]
        self._note("client_event", type=kind, command_id=event.get("command_id"),
                   resource=event.get("resource"), state=event.get("state"))
        if kind == "client.ready":
            self.ready.set()
            return
        if kind == "hangup":
            if self.session is not None:
                self.session.hang_up()
            self.close()
            return
        if kind == "resource.changed":
            self.resources[event["resource"]] = event["state"]
            if self.session is None:
                self._pending_resource_events.append(event)
            else:
                self.session.handle_resource_event(event)
            return
        command_id = event["command_id"]
        command = self._commands.get(command_id)
        if command is None:
            raise LinkRejected("not_found", "Unknown command for this call.", 404)
        if kind == "playback.completed":
            if command.kind != "say":
                raise LinkRejected("invalid_request", "Only a speech command can finish playback.", 400)
            self._settle(command, True)
            return
        if kind == "capture.started":
            if self.session is not None and command_id in self._captures:
                self.session.handle_capture_started(command_id)
            return
        if kind == "command.cancelled":
            self._settle(command, None)
            if self.session is not None and command_id in self._captures:
                self.session.handle_cancel_ack(command_id)
            return
        if kind == "command.failed":
            code = (event.get("error") or {}).get("code", "capture_failed")
            if command_id in self._captures:
                self._settle(command, None)
                self._close_capture(self._captures[command_id])
                if self.session is not None:
                    self.session.handle_command_failed(command_id, code)
                return
            if command.kind == "capture":
                self._settle(command, None)
                return
            self._settle(command, error=PlaybackFailed(code))
            return
        raise LinkRejected("invalid_request", "Unknown event type.", 400)

    def attach(self, session: SessionEvents) -> None:
        self.session = session
        previous = getattr(session, "log", None)
        if isinstance(previous, list) and not isinstance(previous, SessionEventLog):
            session.log = SessionEventLog(previous, self.call_id)
        pending = self._pending_resource_events
        self._pending_resource_events = []
        for event in pending:
            session.handle_resource_event(event)

    def initial_resources(self) -> dict[str, str]:
        return dict(self.resources)

    def on_answer_upload(self, command_id: str, audio: bytes, content_type: str) -> None:
        command = self._commands.get(command_id)
        if command is None or command.kind != "capture" or command.payload["transport"] != "answer_upload":
            raise LinkRejected("not_found", "No such answer recording command.", 404)
        print_ = fingerprint(audio, content_type)
        if command.upload_print is not None:
            if command.upload_print == print_:
                return
            raise LinkRejected("conflict", "A different recording was already uploaded.", 409)
        if command.finished:
            raise LinkRejected("expired", "This recording command is closed.", 410)
        command.upload_print = print_
        self.store.record_command_done(self.call_id, command_id, "completed")
        self._settle(command, (audio, content_type))

    def on_sample(self, attempt_id: str, sequence: int, captured_ms: int, media_format: str, data: bytes) -> None:
        capture = self._active_capture(attempt_id)
        if media_format != capture.media_format:
            raise LinkRejected("unsupported_media", "Sample format does not match the capture command.", 415)
        print_ = fingerprint(captured_ms, data)
        seen = capture.prints.get(sequence)
        if seen is not None:
            if seen == print_:
                return
            raise LinkRejected("conflict", "Sample sequence reused with different content.", 409)
        if sequence <= capture.last_sequence:
            raise LinkRejected("conflict", "Sample sequence is older than the last accepted one.", 409)
        if capture.total_bytes + len(data) > MAX_ATTEMPT_BYTES:
            raise LinkRejected("too_large", "This attempt has reached its input limit.", 413)
        sample = {"sequence": sequence, "captured_ms": captured_ms, "format": media_format, "data": data}
        try:
            capture.queue.put_nowait(sample)
        except asyncio.QueueFull:
            raise LinkRejected("unavailable", "Analysis is behind; retry this sample.", 503) from None
        capture.prints[sequence] = print_
        capture.last_sequence = sequence
        capture.total_bytes += len(data)
        if media_format == "jpeg":
            task, frames = self._frames.setdefault(attempt_id, (capture.task, []))
            if len(frames) < MAX_CLIP_FRAMES_PER_ATTEMPT:
                frames.append((captured_ms, data))
            self._save_debug(f"{capture.task}_{attempt_id}/{captured_ms:05d}.jpg", data)

    def _save_debug(self, name: str, data: bytes) -> None:
        """DEBUG_FRAMES_DIR only (see DEBUG_FRAMES_ENV); a failed write never affects the call."""
        if self._frames_dir is None:
            return
        try:
            path = self._frames_dir / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        except OSError as exc:
            log.warning("debug frame not saved (%s)", type(exc).__name__)

    def on_finish(self, attempt_id: str, last_sequence: int, captured_until_ms: int) -> None:
        capture = self._captures.get(attempt_id)
        if capture is None:
            raise LinkRejected("not_found", "Unknown attempt for this call.", 404)
        print_ = fingerprint(last_sequence, captured_until_ms)
        if capture.finish_print is not None:
            if capture.finish_print == print_:
                return
            raise LinkRejected("conflict", "A different finish was already accepted.", 409)
        if capture.closed:
            raise LinkRejected("stream_closed", "This attempt is no longer accepting input.", 410)
        if last_sequence != capture.last_sequence:
            raise LinkRejected("conflict", "last_sequence does not match the last accepted sample.", 409)
        capture.finish_print = print_
        self._note("capture_finished", attempt_id=attempt_id, samples=len(capture.prints),
                   total_bytes=capture.total_bytes, captured_until_ms=captured_until_ms)
        self.store.record_command_done(self.call_id, attempt_id, "completed")
        self._close_capture(capture)
        command = self._commands.get(attempt_id)
        if command is not None:
            self._settle(command, None)

    def _active_capture(self, attempt_id: str) -> Capture:
        capture = self._captures.get(attempt_id)
        if capture is None:
            raise LinkRejected("not_found", "Unknown attempt for this call.", 404)
        if capture.closed or self.ended:
            raise LinkRejected("stream_closed", "This attempt is no longer accepting input.", 410)
        return capture

    def _close_capture(self, capture: Capture) -> None:
        if capture.closed:
            return
        capture.closed = True
        while True:
            try:
                capture.queue.put_nowait(None)
                return
            except asyncio.QueueFull:
                capture.queue.get_nowait()

    def close(self) -> None:
        if self.ended:
            return
        self.ended = True
        for command in self._commands.values():
            if not command.finished:
                self._settle(command, error=CallEnded("call ended"))
        for capture in self._captures.values():
            self._close_capture(capture)
        self.ready.set()
