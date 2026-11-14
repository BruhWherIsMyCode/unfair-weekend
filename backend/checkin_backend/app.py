"""Python entrypoint: the call service HTTP/SSE surface for the parent and family clients."""

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from dataclasses import asdict
from secrets import compare_digest
from typing import Annotated, Any, Literal

from fastapi import Depends, FastAPI, Header, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .calls import Call, CallConflict, CallNotFound, CallService, CallState, EndReason
from .avatar import ClipGateway
from .attempt_clips import AttemptClips, clip_metadata
from .config import Settings
from .debug_probe import DebugDesk, DebugUnavailable
from .delivery import FamilyDelivery
from .hub import Device, DeviceHub, UnknownDevice
from .link import ClientLink, LinkRejected
from .live_avatar import LiveAvatarGateway
from .logging_config import configure_logging, log_context, log_event
from .media import MAX_ANSWER_BYTES, MediaRejected, check_answer, check_sample, decode_base64
from .people import Directory
from .provider_io import ProviderError
from .runner import choose_driver, run_answered_call
from .request_logging import RequestLoggingMiddleware
from .speech import SpeechGateway
from .store import NoStore, Store, now_ms

IdStr = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")]
KEEPALIVE_S = 15.0
log = logging.getLogger(__name__)


class DemoKeyRequired(Exception):
    pass


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        self.status = status
        self.code = code
        self.message = message


class Health(BaseModel):
    ok: Literal[True] = True


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DeviceIn(Strict):
    device_id: IdStr
    role: Literal["parent", "family"]
    person_id: IdStr | None = None
    family_id: IdStr | None = None


class StartCall(Strict):
    person_id: IdStr


class ErrorBody(Strict):
    code: str
    message: str = Field(max_length=300)


class BasicEvent(Strict):
    event_id: IdStr
    type: Literal["client.ready", "playback.completed", "command.failed", "hangup", "capture.started",
                  "command.cancelled"]
    command_id: IdStr | None
    error: ErrorBody | None


class ResourceEventIn(Strict):
    event_id: IdStr
    type: Literal["resource.changed"]
    command_id: IdStr | None
    resource: Literal["camera", "microphone"]
    state: Literal["available", "unavailable"]
    sequence: int = Field(ge=0)
    reason: Literal["permission_denied", "track_ended", "device_missing", "unknown"] | None


class SampleIn(Strict):
    sequence: int = Field(ge=0)
    captured_ms: int = Field(ge=0, le=20000)
    format: Literal["jpeg", "pcm_s16le_16000_mono"]
    data_base64: str = Field(min_length=4, max_length=174764)


class FinishIn(Strict):
    last_sequence: int = Field(ge=-1)
    captured_until_ms: int = Field(ge=0, le=20000)


def parse_client_event(body: Any) -> dict[str, Any]:
    if not isinstance(body, dict):
        raise ApiError(400, "invalid_request", "Event must be a JSON object.")
    try:
        if body.get("type") == "resource.changed":
            event = ResourceEventIn.model_validate(body).model_dump()
        else:
            event = BasicEvent.model_validate(body).model_dump()
    except ValidationError:
        raise ApiError(400, "invalid_request", "Event does not match the contract.") from None
    kind = event["type"]
    if kind == "resource.changed":
        if event["state"] == "available" and event["reason"] is not None:
            raise ApiError(400, "invalid_request", "An available resource has a null reason.")
        if event["state"] == "unavailable" and event["reason"] is None:
            raise ApiError(400, "invalid_request", "An unavailable resource needs a reason.")
        return event
    if kind in ("client.ready", "hangup"):
        if event["command_id"] is not None or event["error"] is not None:
            raise ApiError(400, "invalid_request", "This event has no command or error.")
    elif kind == "command.failed":
        if event["command_id"] is None or event["error"] is None:
            raise ApiError(400, "invalid_request", "command.failed needs a command and an error.")
    else:
        if event["command_id"] is None or event["error"] is not None:
            raise ApiError(400, "invalid_request", "This event needs a command and no error.")
    return event


def sse_frame(event_id: str, event_type: str, data: dict[str, Any]) -> bytes:
    payload = json.dumps(data, separators=(",", ":"))
    return f"id: {event_id}\nevent: {event_type}\ndata: {payload}\n\n".encode()


class CallDesk:
    """Glue between connection state, client links and the session runner. One per app."""

    def __init__(self, settings: Settings, calls: CallService, hub: DeviceHub, people: Directory,
                 speech: SpeechGateway, delivery: FamilyDelivery, clips: ClipGateway,
                 store: Store | None = None, attempt_clips: AttemptClips | None = None) -> None:
        self.settings = settings
        self.store = store if store is not None else NoStore()
        self.attempt_clips = attempt_clips
        self.calls = calls
        self.hub = hub
        self.people = people
        self.speech = speech
        self.delivery = delivery
        self.clips = clips
        self.links: dict[str, ClientLink] = {}
        self.results: dict[str, dict[str, Any] | None] = {}
        self.tasks: set[asyncio.Task[Any]] = set()
        self.driver = choose_driver(settings.call_runner, settings.database_path,
                                    allow_fallback=settings.claude_fallback)

    def _spawn(self, coro: Any, call_id: str) -> None:
        task = asyncio.create_task(coro)
        self.tasks.add(task)

        def finished(done: asyncio.Task[Any]) -> None:
            self.tasks.discard(done)
            if not done.cancelled() and (exc := done.exception()) is not None:
                log.error("call.background_failed", extra={"event_fields": {"call_id": call_id}},
                          exc_info=(type(exc), exc, exc.__traceback__))

        task.add_done_callback(finished)

    def start(self, person_id: str) -> Call:
        person = self.people.get(person_id)
        if person is None:
            raise ApiError(404, "not_found", "Unknown person.")
        if not self.hub.parents_of(person_id):
            raise ApiError(503, "client_unavailable", "No parent device is registered for this person.")
        call = self.calls.start(person_id)
        devices = self.hub.parents_of(person_id)
        ring = {"event_id": f"ring_{call.call_id}", "call_id": call.call_id, "caller_name": "Daily check-in",
                "person_name": person.name, "ring_timeout_s": self.settings.ring_timeout_s}
        self.store.record_call_started(call.call_id, person, devices, self.settings.ring_timeout_s, ring)
        self.hub.send_many(
            devices,
            "call.incoming",
            ring,
            event_id=f"ring_{call.call_id}",
            still_active=lambda: self.calls.get(call.call_id).state == CallState.RINGING,
        )
        self.store.record_ring_sent(call.call_id, devices)
        self._spawn(self._watch(call), call.call_id)
        return call

    def answer(self, call_id: str) -> dict[str, Any]:
        call = self.calls.answer(call_id)
        person = self.people.get(call.person_id)
        if call_id not in self.links:
            self.store.record_call_answered(call_id, self.settings.call_timeout_s)
            link = ClientLink(call_id=call_id, person=person, hub=self.hub, speech=self.speech,
                              delivery=self.delivery, clips=self.clips, store=self.store,
                              attempt_clips=self.attempt_clips)
            self.links[call_id] = link
            self._spawn(self._run(link), call_id)
        return {"call_id": call_id, "person_name": person.name, "voiced": self.speech.voiced,
                "deadline_s": self.settings.call_timeout_s}

    async def _run(self, link: ClientLink) -> None:
        # These tasks outlive the answer HTTP request. Subsequent logs belong to the call.
        with log_context(request_id=None, call_id=link.call_id):
            result = await run_answered_call(link, self.driver)
        self.results[link.call_id] = result
        call = self.calls.get(link.call_id)
        if not call.terminal:
            self.calls.end(link.call_id, EndReason.COMPLETED if result is not None else EndReason.CONNECTION_LOST)

    async def _watch(self, call: Call) -> None:
        ended = await self.calls.wait_ended(call.call_id)
        self.store.record_call_ended(call.call_id, ended.state.value, ended.reason.value if ended.reason else None)
        person = self.people.get(call.person_id)
        devices = self.hub.parents_of(call.person_id)
        self.hub.send_many(devices, "call.ended",
                           {"event_id": f"end_{call.call_id}", "call_id": call.call_id,
                            "reason": ended.reason.value if ended.reason else None},
                           event_id=f"end_{call.call_id}")
        link = self.links.get(call.call_id)
        if link is not None:
            if link.session is not None and not link.ended:
                link.session.hang_up()
            link.close()
            # Alert frames remain in the short-lived shelf; optional MP4 jobs own their bounded copies.
            link.release_frames()
        if ended.state == CallState.NO_ANSWER and person is not None:
            self.delivery.publish(call.call_id, person, "no_answer", "final")

    async def aclose(self) -> None:
        for link in self.links.values():
            link.close()
        for task in list(self.tasks):
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)

    def link(self, call_id: str) -> ClientLink:
        self.calls.get(call_id)
        link = self.links.get(call_id)
        if link is None:
            raise ApiError(409, "conflict", "The call has not been answered.")
        return link


def create_app(settings: Settings | None = None, *, speech: SpeechGateway | None = None,
               people: Directory | None = None, clips: ClipGateway | None = None,
               live: LiveAvatarGateway | None = None) -> FastAPI:
    settings = settings if settings is not None else Settings.from_environment()
    if settings.logging is not None:
        configure_logging(settings.logging, secrets=(settings.demo_key,))
    if settings.call_runner == "analysis" or not settings.claude_fallback:
        choose_driver(settings.call_runner, allow_fallback=settings.claude_fallback)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        log_event(log, logging.INFO, "application.starting", call_runner=settings.call_runner,
                  database_enabled=bool(settings.database_path), claude_fallback=settings.claude_fallback)
        calls = CallService(ring_timeout_s=settings.ring_timeout_s, call_timeout_s=settings.call_timeout_s)
        hub = DeviceHub()
        gateway = speech if speech is not None else SpeechGateway.from_environment()
        avatar = clips if clips is not None else ClipGateway.from_environment()
        live_avatar = live if live is not None else LiveAvatarGateway.from_environment(
            call_timeout_s=settings.call_timeout_s)
        directory = people if people is not None else Directory.from_environment()
        store = Store(settings.database_path) if settings.database_path else NoStore()
        await asyncio.to_thread(store.open, directory.all())
        recordings = None
        if settings.clip_storage_dir:
            recordings = AttemptClips(settings.clip_storage_dir, store, settings.clip_retention_days)
            try:
                await recordings.open()
            except BaseException:
                await asyncio.to_thread(store.close)
                raise
        delivery = FamilyDelivery(hub, store)
        desk = CallDesk(settings, calls, hub, directory, gateway, delivery, avatar, store, recordings)
        app.state.calls = calls
        app.state.hub = hub
        app.state.desk = desk
        app.state.store = store
        app.state.live = live_avatar
        # Camera debug runs the analysis for the demo person; the phone's own person setting is not needed.
        demo_person = next(iter(directory.all()))
        app.state.debug = DebugDesk(demo_person.person_id, demo_person)
        try:
            log_event(log, logging.INFO, "application.started", voiced=gateway.voiced,
                      live_avatar=live_avatar.available)
            yield
        finally:
            log.info("application.stopping")
            hub.close()
            await desk.aclose()
            await calls.aclose()
            if recordings is not None:
                await recordings.aclose()
            await asyncio.to_thread(store.close)
            if speech is None:
                await gateway.aclose()
            if clips is None:
                await avatar.aclose()
            if live is None:
                await live_avatar.aclose()
            app.state.debug.close()
            log.info("application.stopped")

    app = FastAPI(title="Check-in backend", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(RequestLoggingMiddleware)

    async def require_demo_key(request: Request) -> None:
        candidate = request.headers.get("X-Demo-Key")
        if candidate is None or not compare_digest(candidate.encode("utf-8"), settings.demo_key.encode("utf-8")):
            raise DemoKeyRequired

    @app.exception_handler(DemoKeyRequired)
    async def unauthorized(request: Request, exc: DemoKeyRequired) -> JSONResponse:
        return JSONResponse(status_code=401, content={"code": "unauthorized", "message": "A valid demo key is required."},
                            headers={"Cache-Control": "no-store"})

    def error(status: int, code: str, message: str) -> JSONResponse:
        return JSONResponse(status_code=status, content={"code": code, "message": message},
                            headers={"Cache-Control": "no-store"})

    @app.exception_handler(ApiError)
    async def api_error(request: Request, exc: ApiError) -> JSONResponse:
        return error(exc.status, exc.code, exc.message)

    @app.exception_handler(LinkRejected)
    async def link_rejected(request: Request, exc: LinkRejected) -> JSONResponse:
        return error(exc.status, exc.code, str(exc))

    @app.exception_handler(MediaRejected)
    async def media_rejected(request: Request, exc: MediaRejected) -> JSONResponse:
        return error(exc.status, exc.code, str(exc))

    @app.exception_handler(CallNotFound)
    async def call_not_found(request: Request, exc: CallNotFound) -> JSONResponse:
        return error(404, "not_found", "Unknown call.")

    @app.exception_handler(CallConflict)
    async def call_conflict(request: Request, exc: CallConflict) -> JSONResponse:
        return error(409, "conflict", str(exc))

    @app.exception_handler(UnknownDevice)
    async def unknown_device(request: Request, exc: UnknownDevice) -> JSONResponse:
        return error(404, "not_found", "Unknown device. Register it first.")

    from fastapi.exceptions import RequestValidationError

    @app.exception_handler(RequestValidationError)
    async def invalid(request: Request, exc: RequestValidationError) -> JSONResponse:
        return error(400, "invalid_request", "Request does not match the contract.")

    auth = [Depends(require_demo_key)]

    def desk_of(request: Request) -> CallDesk:
        return request.app.state.desk

    @app.get("/v1/health", response_model=Health, dependencies=auth)
    async def health() -> Health:
        return Health()

    @app.post("/v1/devices", status_code=204, dependencies=auth)
    async def register(body: DeviceIn, request: Request) -> Response:
        desk = desk_of(request)
        if body.role == "parent" and (body.person_id is None or desk.people.get(body.person_id) is None):
            raise ApiError(400, "invalid_request", "A parent device needs a known person_id.")
        if body.role == "family" and body.family_id is None:
            raise ApiError(400, "invalid_request", "A family device needs a family_id.")
        desk.hub.register(Device(body.device_id, body.role, body.person_id, body.family_id))
        family_id = desk.people.get(body.person_id).family_id if body.role == "parent" else body.family_id
        desk.store.record_device(body.device_id, body.role, body.person_id, family_id)
        log_event(log, logging.INFO, "device.registered", device_id=body.device_id, role=body.role)
        return Response(status_code=204)

    @app.get("/v1/devices/{device_id}/events", dependencies=auth)
    async def events(device_id: str, request: Request,
                     last_event_id: Annotated[str | None, Header()] = None) -> StreamingResponse:
        hub = desk_of(request).hub
        replay, queue = hub.subscribe(device_id, last_event_id)

        async def stream():
            try:
                yield b"retry: 1000\n\n"
                for event in replay:
                    yield sse_frame(event.event_id, event.type, event.data)
                while True:
                    try:
                        event = await asyncio.wait_for(queue.get(), KEEPALIVE_S)
                    except TimeoutError:
                        yield b": keepalive\n\n"
                        continue
                    if event is None:
                        return
                    yield sse_frame(event.event_id, event.type, event.data)
            finally:
                hub.unsubscribe(device_id, queue)

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

    @app.post("/v1/calls", dependencies=auth)
    async def start_call(body: StartCall, request: Request) -> JSONResponse:
        call = desk_of(request).start(body.person_id)
        return JSONResponse(status_code=201, content={"call_id": call.call_id})

    @app.get("/v1/calls/{call_id}", dependencies=auth)
    async def call_status(call_id: str, request: Request) -> dict[str, Any]:
        desk = desk_of(request)
        try:
            call = desk.calls.get(call_id)
        except CallNotFound:
            # Not in this process (e.g. after a restart): answer from the stored history.
            stored = await desk.store.call_record(call_id)
            if stored is None:
                raise
            return stored
        message = desk.delivery.for_call(call_id)
        link = desk.links.get(call_id)
        return {"call_id": call_id, "person_id": call.person_id, "state": call.state.value,
                "reason": call.reason.value if call.reason else None,
                "message": asdict(message) if message else None,
                "summary": desk.results.get(call_id),
                "log": link.log[-50:] if link else []}

    @app.post("/v1/calls/{call_id}/answer", dependencies=auth)
    async def answer(call_id: str, request: Request) -> dict[str, Any]:
        return desk_of(request).answer(call_id)

    @app.post("/v1/calls/{call_id}/decline", status_code=204, dependencies=auth)
    async def decline(call_id: str, request: Request) -> Response:
        desk_of(request).calls.decline(call_id)
        return Response(status_code=204)

    @app.post("/v1/calls/{call_id}/client-events", status_code=204, dependencies=auth)
    async def client_event(call_id: str, request: Request) -> Response:
        desk = desk_of(request)
        try:
            body = await request.json()
        except ValueError:
            raise ApiError(400, "invalid_request", "Body must be JSON.") from None
        event = parse_client_event(body)
        call = desk.calls.get(call_id)
        if event["type"] == "hangup":
            link = desk.links.get(call_id)
            if link is not None:
                link.on_event(event)
            desk.store.record_client_event(call_id, event)
            if not call.terminal:
                if call.state == CallState.RINGING:
                    desk.calls.decline(call_id)
                else:
                    desk.calls.end(call_id, EndReason.HANGUP)
            return Response(status_code=204)
        if call.terminal:
            raise ApiError(410, "call_ended", "The call has ended.")
        desk.link(call_id).on_event(event)
        desk.store.record_client_event(call_id, event)
        return Response(status_code=204)

    @app.get("/v1/calls/{call_id}/speech/{command_id}", dependencies=auth)
    async def speech_audio(call_id: str, command_id: str, request: Request) -> Response:
        audio = desk_of(request).link(call_id).speech_audio(command_id)
        return Response(content=audio, media_type="application/octet-stream",
                        headers={"Cache-Control": "no-store", "X-Sample-Rate": "16000"})

    @app.get("/v1/calls/{call_id}/video/{command_id}", dependencies=auth)
    async def video_clip(call_id: str, command_id: str, request: Request) -> FileResponse:
        path = desk_of(request).link(call_id).video_asset(command_id)
        return FileResponse(path, media_type="video/mp4", headers={"Cache-Control": "no-store"})

    @app.post("/v1/calls/{call_id}/avatar/live", dependencies=auth)
    async def live_avatar_session(call_id: str, request: Request) -> JSONResponse:
        # Provisional extension: a Simli live session for the answered call. The phone falls back to prepared
        # clips and voice on any error here, so 503 is an ordinary answer, not a failed call.
        link = desk_of(request).link(call_id)
        if link.ended:
            raise ApiError(410, "call_ended", "The call has ended.")
        gateway: LiveAvatarGateway = request.app.state.live
        if not gateway.available:
            raise ApiError(503, "avatar_unavailable", "The live avatar is not enabled.")
        try:
            session = await gateway.session()
        except ProviderError:
            log_event(log, logging.WARNING, "avatar.live_unavailable", call_id=call_id, exc_info=True)
            raise ApiError(503, "avatar_unavailable", "The live avatar could not be started.") from None
        return JSONResponse(session, headers={"Cache-Control": "no-store"})

    @app.post("/v1/calls/{call_id}/captures/{command_id}", status_code=204, dependencies=auth)
    async def answer_upload(call_id: str, command_id: str, request: Request) -> Response:
        link = desk_of(request).link(call_id)
        data = bytearray()
        async for chunk in request.stream():
            if len(data) + len(chunk) > MAX_ANSWER_BYTES:
                raise ApiError(413, "too_large", "Answer audio is larger than 10 MiB.")
            data.extend(chunk)
        data = bytes(data)
        media_type = check_answer(request.headers.get("content-type"), data)
        link.on_answer_upload(command_id, data, media_type)
        return Response(status_code=204)

    @app.post("/v1/calls/{call_id}/attempts/{attempt_id}/samples", status_code=204, dependencies=auth)
    async def sample(call_id: str, attempt_id: str, body: SampleIn, request: Request) -> Response:
        link = desk_of(request).link(call_id)
        data = decode_base64(body.data_base64)
        check_sample(body.format, data)
        link.on_sample(attempt_id, body.sequence, body.captured_ms, body.format, data)
        return Response(status_code=204)

    @app.post("/v1/calls/{call_id}/attempts/{attempt_id}/finish", status_code=202, dependencies=auth)
    async def finish(call_id: str, attempt_id: str, body: FinishIn, request: Request) -> Response:
        desk_of(request).link(call_id).on_finish(attempt_id, body.last_sequence, body.captured_until_ms)
        return Response(status_code=202)

    @app.get("/v1/clips/{clip_id}", dependencies=auth)
    async def alert_clip(clip_id: str, request: Request) -> JSONResponse:
        clip = desk_of(request).delivery.clips.get(clip_id)
        if clip is None:
            raise ApiError(404, "not_found", "This clip does not exist or has expired.")
        return JSONResponse(clip.as_json(), headers={"Cache-Control": "no-store"})

    @app.get("/v1/calls/{call_id}/clips", dependencies=auth)
    async def call_clips(call_id: str, request: Request) -> JSONResponse:
        desk = desk_of(request)
        try:
            desk.calls.get(call_id)
        except CallNotFound:
            if await desk.store.call_record(call_id) is None:
                raise
        rows = await desk.store.call_clips(call_id) if desk.store.enabled else []
        return JSONResponse({"call_id": call_id, "clips": [clip_metadata(row) for row in rows]},
                            headers={"Cache-Control": "no-store"})

    @app.get("/v1/clips/{clip_id}/video", dependencies=auth)
    async def attempt_video(clip_id: str, request: Request) -> Response:
        desk = desk_of(request)
        archive = desk.attempt_clips
        row = await desk.store.clip_record(clip_id) if archive is not None else None
        if row is None:
            raise ApiError(404, "not_found", "This video does not exist or recording is disabled.")
        if row["expires_at_ms"] <= now_ms() or row["state"] == "expired":
            raise ApiError(410, "expired", "This video has expired.")
        if row["state"] == "queued":
            return JSONResponse({"code": "clip_pending", "message": "This video is being prepared."},
                                status_code=202, headers={"Retry-After": "1", "Cache-Control": "no-store"})
        if row["state"] != "ready":
            raise ApiError(409, "clip_unavailable", "This video could not be prepared.")
        try:
            path = archive.path(clip_id)
            exists = await asyncio.to_thread(path.is_file)
        except ValueError:
            exists = False
        if not exists:
            raise ApiError(404, "not_found", "This video is no longer on the server.")
        return FileResponse(path, media_type="video/mp4", headers={
            "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
            "Content-Disposition": f'inline; filename="{clip_id}.mp4"',
        })

    class DebugRunIn(BaseModel):
        model_config = ConfigDict(extra="forbid")
        task: Literal["smile", "arms"]
        mirrored: bool | None = None
        purpose: Literal["check", "teach"] = "check"

    class DebugSampleIn(BaseModel):
        model_config = ConfigDict(extra="forbid")
        sequence: int = Field(ge=0)
        captured_ms: int = Field(ge=0, le=60000)
        data_base64: str
        since: int = Field(default=0, ge=0)

    def debug_run(request: Request, run_id: str):
        run = request.app.state.debug.get(run_id)
        if run is None:
            raise ApiError(404, "not_found", "Unknown or expired debug run.")
        return run

    @app.post("/v1/debug/runs", status_code=201, dependencies=auth)
    async def debug_start(body: DebugRunIn, request: Request) -> dict[str, Any]:
        # Camera debug: Agent 3's real module on the phone's frames, outside any call. Nothing is stored.
        try:
            run = request.app.state.debug.start(body.task, body.mirrored, body.purpose)
        except DebugUnavailable as exc:
            raise ApiError(503, "analysis_unavailable", str(exc)) from None
        return {"run_id": run.run_id, "task": run.task, "capture": run.capture, "cues": run.cues,
                "purpose": run.purpose, "usual_source": run.usual_source}

    def debug_verdict(request: Request, task: str) -> dict[str, Any]:
        if task not in ("smile", "arms"):
            raise ApiError(404, "not_found", "Unknown task.")
        try:
            return request.app.state.debug.verdict(task)
        except ImportError:
            raise ApiError(503, "analysis_unavailable", "Agent 3's analysis is not installed here.") from None

    @app.get("/v1/debug/series/{task}", dependencies=auth)
    async def debug_series(task: str, request: Request) -> dict[str, Any]:
        # What a real call would do with this screen's check tries of `task`, and what the family would get.
        return debug_verdict(request, task)

    @app.post("/v1/debug/series/{task}/reset", dependencies=auth)
    async def debug_series_reset(task: str, request: Request) -> dict[str, Any]:
        if task in ("smile", "arms"):
            request.app.state.debug.reset(task)
        return debug_verdict(request, task)

    @app.post("/v1/debug/runs/{run_id}/samples", dependencies=auth)
    async def debug_sample(run_id: str, body: DebugSampleIn, request: Request) -> dict[str, Any]:
        run = debug_run(request, run_id)
        data = decode_base64(body.data_base64)
        check_sample("jpeg", data)
        run.add(body.sequence, body.captured_ms, data)
        return run.snapshot(body.since)

    @app.post("/v1/debug/runs/{run_id}/finish", dependencies=auth)
    async def debug_finish(run_id: str, request: Request) -> dict[str, Any]:
        run = debug_run(request, run_id)
        await run.finish()
        return run.snapshot()

    @app.get("/v1/families/{family_id}/messages", dependencies=auth)
    async def messages(family_id: str, request: Request) -> list[dict[str, Any]]:
        desk = desk_of(request)
        if desk.store.enabled:
            # Stored messages survive restarts; the in-memory list only covers this process.
            return await desk.store.family_messages(family_id)
        return [asdict(m) for m in desk.delivery.for_family(family_id, desk.people.all())]

    return app
