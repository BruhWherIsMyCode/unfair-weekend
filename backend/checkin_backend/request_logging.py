"""ASGI request logs that preserve streaming and never read the request body."""

import asyncio
import logging
import re
from time import monotonic
from uuid import uuid4

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .logging_config import log_context, log_event

log = logging.getLogger(__name__)
ID = re.compile(r"[A-Za-z0-9_-]{1,128}")
QUIET_ROUTES = {"/v1/health", "/v1/calls/{call_id}/attempts/{attempt_id}/samples",
                "/v1/calls/{call_id}/speech/{command_id}", "/v1/calls/{call_id}/video/{command_id}"}


class RequestLoggingMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = uuid4().hex
        started = monotonic()
        status = None
        disconnected = False

        def fields() -> dict:
            route = scope.get("route")
            params = scope.get("path_params", {})
            return {
                "method": scope["method"], "route": getattr(route, "path", "unmatched"),
                **{name: value for name, value in params.items()
                   if name in ("call_id", "command_id", "attempt_id", "device_id")
                   and isinstance(value, str) and ID.fullmatch(value)},
            }

        async def receive_logged() -> Message:
            nonlocal disconnected
            message = await receive()
            if message["type"] == "http.disconnect":
                disconnected = True
            return message

        async def send_logged(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                if status >= 400:
                    level = logging.ERROR if status >= 500 else logging.WARNING
                    log_event(log, level, "http.rejected", status=status, **fields())
                elif message.get("headers") and any(
                    key.lower() == b"content-type" and value.startswith(b"text/event-stream")
                    for key, value in message["headers"]
                ):
                    log_event(log, logging.INFO, "http.stream_opened", status=status, **fields())
            await send(message)

        with log_context(request_id=request_id):
            try:
                await self.app(scope, receive_logged, send_logged)
            except asyncio.CancelledError:
                log_event(log, logging.INFO, "http.cancelled", status=status,
                          duration_ms=round((monotonic() - started) * 1000, 2), **fields())
                raise
            except Exception:
                log_event(log, logging.ERROR, "http.failed", exc_info=True, status=status or 500,
                          duration_ms=round((monotonic() - started) * 1000, 2), **fields())
                raise
            else:
                info = fields()
                level = logging.DEBUG if info["route"] in QUIET_ROUTES else logging.INFO
                log_event(log, level, "http.completed", status=status, disconnected=disconnected,
                          duration_ms=round((monotonic() - started) * 1000, 2), **info)
