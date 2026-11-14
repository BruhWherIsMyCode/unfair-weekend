"""Runtime configuration; credentials are read only by the app factory."""

import math
import os
from dataclasses import dataclass, field

from .logging_config import LoggingSettings


def positive_seconds(value: float, name: str) -> None:
    if isinstance(value, bool) or not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be a finite positive number")


@dataclass(frozen=True, slots=True)
class Settings:
    demo_key: str = field(repr=False)
    ring_timeout_s: float = 30.0
    call_timeout_s: float = 180.0
    call_runner: str = "auto"
    # SQLite file for durable call records (storage/migrations). None or "" keeps everything in memory.
    database_path: str | None = None
    # Embedders can keep their existing logging; the environment factory enables our process logging.
    logging: LoggingSettings | None = None
    # Explicit opt-in for retaining camera task attempts as MP4. Needs the shared database.
    clip_storage_dir: str | None = None
    clip_retention_days: float = 7.0

    def __post_init__(self) -> None:
        if not self.demo_key or self.demo_key.isspace():
            raise ValueError("DEMO_KEY is required")
        positive_seconds(self.ring_timeout_s, "RING_TIMEOUT_S")
        positive_seconds(self.call_timeout_s, "CALL_TIMEOUT_S")
        if self.call_runner not in ("auto", "analysis", "fake", "scripted"):
            raise ValueError("CALL_RUNNER must be auto, analysis, fake or scripted")
        positive_seconds(self.clip_retention_days, "CLIP_RETENTION_DAYS")
        positive_seconds(self.clip_retention_days * 86_400_000, "CLIP_RETENTION_DAYS")
        if self.clip_storage_dir and not self.database_path:
            raise ValueError("CLIP_STORAGE_DIR requires DATABASE_PATH")

    @classmethod
    def from_environment(cls) -> "Settings":
        return cls(
            demo_key=os.environ.get("DEMO_KEY", ""),
            ring_timeout_s=float(os.environ.get("RING_TIMEOUT_S", "30")),
            call_timeout_s=float(os.environ.get("CALL_TIMEOUT_S", "180")),
            call_runner=os.environ.get("CALL_RUNNER", "auto"),
            database_path=os.environ.get("DATABASE_PATH", ".cache/checkin.sqlite3"),
            logging=LoggingSettings.from_environment(),
            clip_storage_dir=os.environ.get("CLIP_STORAGE_DIR", "").strip() or None,
            clip_retention_days=float(os.environ.get("CLIP_RETENTION_DAYS", "7")),
        )
