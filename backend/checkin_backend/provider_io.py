"""Bounded provider requests and private, atomic generated-asset writes."""

import asyncio
import logging
import os
import tempfile
from collections.abc import Awaitable, Callable
from pathlib import Path
from time import monotonic
from typing import Any, TypeVar

import httpx

from .logging_config import log_event

log = logging.getLogger(__name__)

T = TypeVar("T")


class ProviderError(RuntimeError):
    """A message that contains no provider response body or credentials."""


class ProviderHTTPError(ProviderError):
    """Status/selected headers for internal retry decisions; the message stays redacted."""

    def __init__(self, provider: str, status_code: int, *, retry_after: str | None = None,
                 location: str | None = None) -> None:
        super().__init__(f"{provider} returned HTTP {status_code}")
        self.status_code = status_code
        self.retry_after = retry_after
        self.location = location


async def provider_bytes(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    provider: str,
    limit: int,
    timeout_s: float,
    **kwargs: Any,
) -> bytes:
    started = monotonic()
    log_event(log, logging.DEBUG, "provider.request_started", provider=provider, method=method)
    try:
        async with asyncio.timeout(timeout_s):
            async with client.stream(method, url, **kwargs) as response:
                if response.status_code != 200:
                    raise ProviderHTTPError(
                        provider, response.status_code,
                        retry_after=response.headers.get("Retry-After"),
                        location=response.headers.get("Location"),
                    )
                chunks = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(chunks) + len(chunk) > limit:
                        raise ProviderError(f"{provider} response exceeds the size limit")
                    chunks.extend(chunk)
                log_event(log, logging.INFO, "provider.request_completed", provider=provider, method=method,
                          status=200, response_bytes=len(chunks), duration_ms=round((monotonic() - started) * 1000, 2))
                return bytes(chunks)
    except ProviderError as exc:
        status = getattr(exc, "status_code", None)
        level = logging.DEBUG if provider == "Simli video" and status in (307, 404, 429) else logging.ERROR
        log_event(log, level, "provider.request_failed", provider=provider, method=method, status=status,
                  error_type=type(exc).__name__, duration_ms=round((monotonic() - started) * 1000, 2))
        raise
    except (httpx.HTTPError, TimeoutError):
        log_event(log, logging.ERROR, "provider.connection_failed", provider=provider, method=method, exc_info=True,
                  duration_ms=round((monotonic() - started) * 1000, 2))
        raise ProviderError(f"{provider} could not be reached within the time limit") from None


def private_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


class PendingAssets:
    """Coalesce generation; cancelling a consumer does not cancel another's asset."""

    def __init__(self) -> None:
        self.tasks: dict[str, asyncio.Task[Any]] = {}

    async def get(self, key: str, generate: Callable[[], Awaitable[T]]) -> T:
        if key not in self.tasks:
            task = asyncio.create_task(generate())
            self.tasks[key] = task

            def finished(done: asyncio.Task[Any]) -> None:
                if self.tasks.get(key) is done:
                    self.tasks.pop(key)
                if not done.cancelled():
                    done.exception()

            task.add_done_callback(finished)
        return await asyncio.shield(self.tasks[key])

    async def aclose(self) -> None:
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
