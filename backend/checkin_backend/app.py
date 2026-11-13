"""Python entrypoint. Network call routes await canonical contract publication."""

from contextlib import asynccontextmanager
from secrets import compare_digest
from typing import Literal

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .calls import CallService
from .config import Settings


class DemoKeyRequired(Exception):
    pass


class Health(BaseModel):
    ok: Literal[True] = True


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings if settings is not None else Settings.from_environment()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        calls = CallService(
            ring_timeout_s=settings.ring_timeout_s,
            call_timeout_s=settings.call_timeout_s,
        )
        app.state.calls = calls
        try:
            yield
        finally:
            await calls.aclose()

    app = FastAPI(
        title="Check-in backend",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    async def require_demo_key(request: Request) -> None:
        candidate = request.headers.get("X-Demo-Key")
        if candidate is None or not compare_digest(
            candidate.encode("utf-8"), settings.demo_key.encode("utf-8")
        ):
            raise DemoKeyRequired

    @app.exception_handler(DemoKeyRequired)
    async def unauthorized(request: Request, exc: DemoKeyRequired) -> JSONResponse:
        return JSONResponse(
            status_code=401,
            content={"code": "unauthorized", "message": "A valid demo key is required."},
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/v1/health", response_model=Health, dependencies=[Depends(require_demo_key)])
    async def health() -> Health:
        return Health()

    return app
