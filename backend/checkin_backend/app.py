"""Python entrypoint: the call service HTTP/SSE surface for the parent and family clients."""

import asyncio
import json
from contextlib import asynccontextmanager
from dataclasses import asdict
from secrets import compare_digest
from typing import Annotated, Any, Literal

from fastapi import Depends, FastAPI, Header, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .calls import Call, CallConflict, CallNotFound, CallService, CallState, EndReason
from .avatar import ClipGateway
from .config import Settings
from .delivery import FamilyDelivery
from .hub import Device, DeviceHub, UnknownDevice
from .link import ClientLink, LinkRejected
from .media import MAX_ANSWER_BYTES, MediaRejected, check_answer, check_sample, decode_base64
from .people import Directory
from .runner import choose_driver, run_answered_call
from .speech import SpeechGateway

IdStr = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")]
KEEPALIVE_S = 15.0


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
    captured_ms: int = Field(ge=0, le=15000)
    format: Literal["jpeg", "pcm_s16le_16000_mono"]
    data_base64: str = Field(min_length=4, max_length=174764)


class FinishIn(Strict):
    last_sequence: int = Field(ge=-1)
    captured_until_ms: int = Field(ge=0, le=15000)


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
                 speech: SpeechGateway, delivery: FamilyDelivery, clips: ClipGateway) -> None:
        self.settings = settings
        self.calls = calls
        self.hub = hub
        self.people = people
        self.speech = speech
        self.delivery = delivery
        self.clips = clips
        self.links: dict[str, ClientLink] = {}
        self.results: dict[str, dict[str, Any] | None] = {}
        self.tasks: set[asyncio.Task[Any]] = set()
        self.driver = choose_driver(settings.call_runner, settings.database_path)

    def _spawn(self, coro: Any) -> None:
        task = asyncio.create_task(coro)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    def start(self, person_id: str) -> Call:
        person = self.people.get(person_id)
        if person is None:
            raise ApiError(404, "not_found", "Unknown person.")
        if not self.hub.parents_of(person_id):
            raise ApiError(503, "client_unavailable", "No parent device is registered for this person.")
        call = self.calls.start(person_id)
        self.hub.send_many(
            self.hub.parents_of(person_id),
            "call.incoming",
            {"event_id": f"ring_{call.call_id}", "call_id": call.call_id, "caller_name": "Daily check-in",
             "person_name": person.name, "ring_timeout_s": self.settings.ring_timeout_s},
            event_id=f"ring_{call.call_id}",
            still_active=lambda: self.calls.get(call.call_id).state == CallState.RINGING,
        )
        self._spawn(self._watch(call))
        return call

    def answer(self, call_id: str) -> dict[str, Any]:
        call = self.calls.answer(call_id)
        person = self.people.get(call.person_id)
        if call_id not in self.links:
            link = ClientLink(call_id=call_id, person=person, hub=self.hub, speech=self.speech,
                              delivery=self.delivery, clips=self.clips)
            self.links[call_id] = link
            self._spawn(self._run(link))
        return {"call_id": call_id, "person_name": person.name, "voiced": self.speech.voiced,
                "deadline_s": self.settings.call_timeout_s}

    async def _run(self, link: ClientLink) -> None:
        result = await run_answered_call(link, self.driver)
        self.results[link.call_id] = result
        call = self.calls.get(link.call_id)
        if not call.terminal:
            self.calls.end(link.call_id, EndReason.COMPLETED if result is not None else EndReason.CONNECTION_LOST)

    async def _watch(self, call: Call) -> None:
        ended = await self.calls.wait_ended(call.call_id)
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
               people: Directory | None = None, clips: ClipGateway | None = None) -> FastAPI:
    settings = settings if settings is not None else Settings.from_environment()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if settings.database_path:  # once, before any call: the shared schema (backend/storage)
            from contextlib import closing
            from pathlib import Path

            from storage import apply_migrations, connect_database

            Path(settings.database_path).parent.mkdir(parents=True, exist_ok=True)
            with closing(connect_database(settings.database_path)) as db:
                apply_migrations(db)
        calls = CallService(ring_timeout_s=settings.ring_timeout_s, call_timeout_s=settings.call_timeout_s)
        hub = DeviceHub()
        gateway = speech if speech is not None else SpeechGateway.from_environment()
        avatar = clips if clips is not None else ClipGateway.from_environment()
        directory = people if people is not None else Directory.from_environment()
        delivery = FamilyDelivery(hub)
        desk = CallDesk(settings, calls, hub, directory, gateway, delivery, avatar)
        app.state.calls = calls
        app.state.hub = hub
        app.state.desk = desk
        try:
            yield
        finally:
            hub.close()
            await desk.aclose()
            await calls.aclose()
            if speech is None:
                await gateway.aclose()
            if clips is None:
                await avatar.aclose()

    app = FastAPI(title="Check-in backend", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

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
        call = desk.calls.get(call_id)
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
            if not call.terminal:
                if call.state == CallState.RINGING:
                    desk.calls.decline(call_id)
                else:
                    desk.calls.end(call_id, EndReason.HANGUP)
            return Response(status_code=204)
        if call.terminal:
            raise ApiError(410, "call_ended", "The call has ended.")
        desk.link(call_id).on_event(event)
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

    @app.get("/v1/families/{family_id}/messages", dependencies=auth)
    async def messages(family_id: str, request: Request) -> list[dict[str, Any]]:
        desk = desk_of(request)
        return [asdict(m) for m in desk.delivery.for_family(family_id, desk.people.all())]

    return app
