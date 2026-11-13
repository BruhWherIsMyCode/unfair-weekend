"""Runtime configuration; credentials are read only by the app factory."""

import math
import os
from dataclasses import dataclass, field


def positive_seconds(value: float, name: str) -> None:
    if isinstance(value, bool) or not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be a finite positive number")


@dataclass(frozen=True, slots=True)
class Settings:
    demo_key: str = field(repr=False)
    ring_timeout_s: float = 30.0
    call_timeout_s: float = 180.0

    def __post_init__(self) -> None:
        if not self.demo_key or self.demo_key.isspace():
            raise ValueError("DEMO_KEY is required")
        positive_seconds(self.ring_timeout_s, "RING_TIMEOUT_S")
        positive_seconds(self.call_timeout_s, "CALL_TIMEOUT_S")

    @classmethod
    def from_environment(cls) -> "Settings":
        return cls(
            demo_key=os.environ.get("DEMO_KEY", ""),
            ring_timeout_s=float(os.environ.get("RING_TIMEOUT_S", "30")),
            call_timeout_s=float(os.environ.get("CALL_TIMEOUT_S", "180")),
        )
