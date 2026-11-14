"""Optional per-attempt MP4 archive. Files outlive the process; SQLite owns their identity and expiry.

The call only submits bounded JPEG buffers. One worker encodes them off the event loop, using ffmpeg on
stdin: no JPEG files are written. Five-fps playback follows captured_ms, holding a frame across gaps.
Capture completion describes transport only; it says nothing about analysis quality or baseline eligibility.
"""

import asyncio
import logging
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from uuid import uuid4

from .logging_config import log_event
from .store import Store, iso, now_ms

log = logging.getLogger(__name__)
FRAME_PERIOD_MS = 200
MAX_PENDING_CLIPS = 4
ENCODE_TIMEOUT_S = 20
CLIP_ID = re.compile(r"video_[0-9a-f]{32}\Z")


@dataclass(frozen=True, slots=True)
class EncodeJob:
    clip_id: str
    call_id: str
    frames: tuple[tuple[int, bytes], ...]
    duration_ms: int


def playback_frames(frames: tuple[tuple[int, bytes], ...], duration_ms: int) -> bytes:
    """Use the most recent source frame at each 200 ms tick; preserve pauses and dropped-frame gaps."""
    ordered = sorted(frames, key=lambda item: item[0])
    index = 0
    output = []
    for tick in range(0, duration_ms, FRAME_PERIOD_MS):
        while index + 1 < len(ordered) and ordered[index + 1][0] <= tick:
            index += 1
        output.append(ordered[index][1])
    return b"".join(output)


class AttemptClips:
    def __init__(self, root: str | Path, store: Store, retention_days: float) -> None:
        self.root = Path(root).expanduser().resolve()
        self.store = store
        self.retention_ms = max(1, round(retention_days * 86_400_000))
        self.queue: asyncio.Queue[EncodeJob | None] = asyncio.Queue(maxsize=MAX_PENDING_CLIPS)
        self.worker: asyncio.Task | None = None
        self.cleaner: asyncio.Task | None = None
        self.ffmpeg = ""

    async def open(self) -> None:
        def prepare() -> str:
            binary = shutil.which("ffmpeg")
            if binary is None:
                raise ValueError("CLIP_STORAGE_DIR needs ffmpeg with the libx264 encoder installed")
            result = subprocess.run([binary, "-hide_banner", "-encoders"], capture_output=True, timeout=5)
            if result.returncode or b"libx264" not in result.stdout:
                raise ValueError("CLIP_STORAGE_DIR needs ffmpeg with the libx264 encoder installed")
            self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
            for path in self.root.glob("video_*.part"):
                path.unlink(missing_ok=True)
            return binary

        self.ffmpeg = await asyncio.to_thread(prepare)
        await self.cleanup()
        self.worker = asyncio.create_task(self._work(), name="attempt-video-encoder")
        self.cleaner = asyncio.create_task(self._cleanup_loop(), name="attempt-video-expiry")

    def submit(self, *, call_id: str, attempt_id: str, task: str, session_label: str,
               capture_status: str, frames: list[tuple[int, bytes]], recorded_at_ms: int,
               captured_until_ms: int | None) -> str | None:
        if not frames or self.worker is None:
            return None
        clip_id = f"video_{uuid4().hex}"
        end = min(20_200, max(captured_until_ms or 0, max(t for t, _ in frames) + FRAME_PERIOD_MS))
        duration_ms = math.ceil(end / FRAME_PERIOD_MS) * FRAME_PERIOD_MS
        stamp = max(now_ms(), recorded_at_ms)
        self.store.record_clip_queued(
            clip_id=clip_id, call_id=call_id, attempt_id=attempt_id, task=task, session_label=session_label,
            capture_status=capture_status, duration_ms=duration_ms, frame_count=len(frames),
            recorded_at_ms=recorded_at_ms, created_at_ms=stamp, expires_at_ms=stamp + self.retention_ms,
        )
        try:
            self.queue.put_nowait(EncodeJob(clip_id, call_id, tuple(frames), duration_ms))
        except asyncio.QueueFull:
            self.store.record_clip_failed(clip_id, "encoder_busy")
            log_event(log, logging.WARNING, "clip.encoder_busy", call_id=call_id, clip_id=clip_id)
        return clip_id

    async def _work(self) -> None:
        while True:
            job = await self.queue.get()
            try:
                if job is None:
                    return
                try:
                    row = await self.store.clip_record(job.clip_id)
                    if row is None:
                        raise RuntimeError("Clip metadata was not saved")
                    if row["expires_at_ms"] <= now_ms():
                        self.store.record_clip_expired(job.clip_id)
                        continue
                    size = await asyncio.to_thread(self._encode, job)
                except Exception as exc:
                    code = "encoder_timeout" if isinstance(exc, subprocess.TimeoutExpired) else "encode_failed"
                    self.store.record_clip_failed(job.clip_id, code)
                    log_event(log, logging.WARNING, "clip.encode_failed", call_id=job.call_id,
                              clip_id=job.clip_id, error_type=type(exc).__name__)
                else:
                    self.store.record_clip_ready(job.clip_id, size)
                    log_event(log, logging.INFO, "clip.ready", call_id=job.call_id, clip_id=job.clip_id,
                              byte_count=size)
            finally:
                self.queue.task_done()
                job = None

    def _encode(self, job: EncodeJob) -> int:
        output = self.path(job.clip_id)
        fd, temporary = tempfile.mkstemp(prefix=job.clip_id + "_", suffix=".part", dir=self.root)
        os.close(fd)
        partial = Path(temporary)
        try:
            result = subprocess.run([
                self.ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                "-threads", "1", "-f", "image2pipe", "-framerate", "5", "-c:v", "mjpeg", "-i", "pipe:0",
                "-an", "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2", "-c:v", "libx264", "-threads", "1",
                "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
                "-f", "mp4", str(partial),
            ], input=playback_frames(job.frames, job.duration_ms), capture_output=True, timeout=ENCODE_TIMEOUT_S)
            if result.returncode or partial.stat().st_size == 0:
                raise RuntimeError("Video encoding failed")
            with partial.open("rb") as file:
                os.fsync(file.fileno())
            partial.replace(output)
            return output.stat().st_size
        finally:
            partial.unlink(missing_ok=True)

    def path(self, clip_id: str) -> Path:
        if not CLIP_ID.fullmatch(clip_id):
            raise ValueError("Invalid clip ID")
        path = self.root / (clip_id + ".mp4")
        if path.is_symlink():
            raise ValueError("Clip file must not be a symlink")
        return path

    async def flush(self) -> None:
        await self.queue.join()
        await self.store.flush()

    async def cleanup(self) -> None:
        stamp = now_ms()
        for clip in await self.store.clips_for_cleanup(stamp):
            try:
                await asyncio.to_thread(self.path(clip["clip_id"]).unlink, missing_ok=True)
            except (OSError, ValueError) as exc:
                log_event(log, logging.WARNING, "clip.cleanup_failed", clip_id=clip["clip_id"],
                          error_type=type(exc).__name__)
                continue
            if clip["expires_at_ms"] <= stamp:
                self.store.record_clip_expired(clip["clip_id"])

    async def _cleanup_loop(self) -> None:
        while True:
            await asyncio.sleep(60)
            try:
                await self.cleanup()
            except Exception as exc:
                log_event(log, logging.WARNING, "clip.cleanup_failed", error_type=type(exc).__name__)

    async def aclose(self) -> None:
        if self.cleaner is not None:
            self.cleaner.cancel()
            await asyncio.gather(self.cleaner, return_exceptions=True)
        if self.worker is not None:
            await self.queue.put(None)
            await self.worker
            self.worker = None
        await self.store.flush()


def clip_metadata(row: dict) -> dict:
    state = "expired" if row["expires_at_ms"] <= now_ms() else row["state"]
    return {key: row[key] for key in (
        "clip_id", "call_id", "attempt_id", "task", "session_label", "capture_status", "duration_ms",
        "frame_count", "byte_count", "error_code",
    )} | {
        "state": state, "recorded_at": iso(row["recorded_at_ms"]), "created_at": iso(row["created_at_ms"]),
        "expires_at": iso(row["expires_at_ms"]),
        "video_url": f"/v1/clips/{row['clip_id']}/video" if state == "ready" else None,
    }
