"""Process logging: JSON lines, bounded files and context shared by async call work."""

import json
import logging
import os
import sys
import traceback
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

LOG_DIR = Path(__file__).resolve().parents[1] / ".cache" / "logs"
_context: ContextVar[dict[str, Any]] = ContextVar("log_context", default={})
_targets = (logging.getLogger(), logging.getLogger("uvicorn"), logging.getLogger("uvicorn.error"))


@dataclass(frozen=True, slots=True)
class LoggingSettings:
    level: str = "INFO"
    file: str | None = str(LOG_DIR / "backend.log")
    error_file: str | None = str(LOG_DIR / "errors.log")
    max_bytes: int = 10 * 1024 * 1024
    backup_count: int = 3
    console: bool = True

    def __post_init__(self) -> None:
        if self.level not in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
            raise ValueError("LOG_LEVEL must be DEBUG, INFO, WARNING, ERROR or CRITICAL")
        for value, name in ((self.max_bytes, "LOG_MAX_BYTES"), (self.backup_count, "LOG_BACKUP_COUNT")):
            if type(value) is not int or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        if (self.file and self.error_file
                and Path(self.file).expanduser().resolve() == Path(self.error_file).expanduser().resolve()):
            raise ValueError("LOG_FILE and LOG_ERROR_FILE must be different files")

    @classmethod
    def from_environment(cls) -> "LoggingSettings":
        console = os.environ.get("LOG_CONSOLE", "on").strip().lower()
        if console not in ("on", "off"):
            raise ValueError("LOG_CONSOLE must be on or off")
        return cls(
            level=os.environ.get("LOG_LEVEL", "INFO").strip().upper(),
            file=os.environ.get("LOG_FILE", str(LOG_DIR / "backend.log")).strip() or None,
            error_file=os.environ.get("LOG_ERROR_FILE", str(LOG_DIR / "errors.log")).strip() or None,
            max_bytes=int(os.environ.get("LOG_MAX_BYTES", "10485760")),
            backup_count=int(os.environ.get("LOG_BACKUP_COUNT", "3")),
            console=console == "on",
        )


@contextmanager
def log_context(**fields: Any) -> Iterator[None]:
    token = _context.set({**_context.get(), **fields})
    try:
        yield
    finally:
        _context.reset(token)


def log_event(logger: logging.Logger, level: int, event: str, *, exc_info: bool = False, **fields: Any) -> None:
    logger.log(level, event, extra={"event_fields": fields}, exc_info=exc_info)


class JsonFormatter(logging.Formatter):
    def __init__(self, secrets: tuple[str, ...] = ()) -> None:
        super().__init__()
        self._secrets = sorted({s for value in secrets for s in (value, value.strip()) if s}, key=len, reverse=True)

    def _redact(self, value: Any) -> Any:
        if isinstance(value, str):
            for secret in self._secrets:
                value = value.replace(secret, "[redacted]")
        elif isinstance(value, dict):
            return {key: self._redact(item) for key, item in value.items()}
        elif isinstance(value, (list, tuple)):
            return [self._redact(item) for item in value]
        return value

    def format(self, record: logging.LogRecord) -> str:
        fields = {**_context.get(), **getattr(record, "event_fields", {})}
        entry = {key: value for key, value in fields.items() if value is not None}
        message = record.getMessage()
        if record.name == "asyncio":
            # asyncio appends repr(task/future), which can include exception messages and arguments.
            message = message.split("\n", 1)[0]
        entry.update(
            timestamp=datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            level=record.levelname, logger=record.name, message=message, process=record.process,
        )
        if record.exc_info and record.exc_info[0] is not None:
            # Exception messages, source lines and locals may contain request data or tokens.
            entry["exception"] = {
                "type": record.exc_info[0].__name__,
                "stack": [{"file": frame.filename, "line": frame.lineno, "function": frame.name}
                          for frame in traceback.extract_tb(record.exc_info[2])],
            }
        return json.dumps(self._redact(entry), ensure_ascii=False, separators=(",", ":"), default=lambda _: "[omitted]")


class _ApplicationRecords(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        # Exclude HTTP client's header/body debugging even at LOG_LEVEL=DEBUG.
        return record.name.split(".", 1)[0] in ("checkin_backend", "analysis", "uvicorn", "asyncio")


class _PrivateRotatingFileHandler(RotatingFileHandler):
    def _open(self):
        fd = os.open(self.baseFilename, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
        return os.fdopen(fd, self.mode, encoding=self.encoding, errors=self.errors)


def configure_logging(settings: LoggingSettings, *, secrets: tuple[str, ...] = ()) -> None:
    """Replace only our handlers. Uvicorn/pytest/embedding applications keep their own handlers.

    Call in the app factory (after Uvicorn configures its loggers), once per process. Files append across restarts.
    The rotating handlers are for one backend process; multiple workers need separate paths or a log collector.
    """
    handlers: list[logging.Handler] = []
    try:
        for filename, level in ((settings.file, settings.level), (settings.error_file, "ERROR")):
            if filename:
                path = Path(filename).expanduser()
                path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                handler = _PrivateRotatingFileHandler(
                    path, maxBytes=settings.max_bytes, backupCount=settings.backup_count, encoding="utf-8",
                )
                handler.setLevel(level)
                handlers.append(handler)
        if settings.console:
            handler = logging.StreamHandler(sys.stderr)
            handler.setLevel(settings.level)
            handlers.append(handler)
    except Exception:
        for handler in handlers:
            handler.close()
        raise

    previous = set()
    for target in _targets:
        for handler in list(target.handlers):
            if getattr(handler, "_checkin_handler", False):
                target.removeHandler(handler)
                previous.add(handler)
    for handler in previous:
        handler.close()

    formatter = JsonFormatter((*secrets, *(
        os.environ.get(name, "") for name in
        ("DEMO_KEY", "ELEVENLABS_API_KEY", "SIMLI_API_KEY", "SMILI_API_KEY", "ANTHROPIC_API_KEY")
    )))
    for handler in handlers:
        handler._checkin_handler = True
        handler.setFormatter(formatter)
        handler.addFilter(_ApplicationRecords())
        _targets[0].addHandler(handler)
        # Uvicorn normally stops propagation at its own logger. Its console stays under its control.
        for target in _targets[1:]:
            if isinstance(handler, logging.FileHandler) and not target.propagate:
                target.addHandler(handler)
    for name in ("checkin_backend", "analysis"):
        logging.getLogger(name).setLevel(settings.level)
    # Our request logger omits query strings and reduces sample/health traffic to DEBUG.
    logging.getLogger("uvicorn.access").disabled = True
