"""The microphone: the ONLY module that opens it. 16 kHz mono int16 through `sounddevice` (imported lazily).

    record_until_silence(max_s, silence_s, start_timeout_s) -> Recording (WAV bytes, duration, whether speech was heard)
    EndpointDetector                a PURE energy-based voice activity detector over int16 frames (tested with
                                    synthetic arrays; no audio device needed)
    play_pcm16k(pcm_bytes)          plays raw 16 kHz mono int16 audio (blocking)
    microphone_status()             ("available", None) or ("unavailable", reason)

The detector: a frame (30 ms) is speech when its RMS (fraction of full scale) is above max(MIN_THRESHOLD,
NOISE_FACTOR x the quietest of the first frames). Speech starts after START_FRAMES speech frames in a row; it ends after
`silence_s` of quiet frames; it also ends at `max_s`, or when nobody starts within `start_timeout_s`. The numbers are
first guesses and have never been tried on a real microphone.

STATUS: the detector is tested; opening the microphone and playing sound were never run in the build session.
"""
from __future__ import annotations

import io
import math
import wave
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Iterator, List, Optional, Tuple

import numpy as np

SAMPLE_RATE = 16000
FRAME_MS = 30
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000  # 480
MIN_THRESHOLD = 0.015  # RMS as a fraction of full scale; about -36 dBFS
NOISE_FACTOR = 3.0
NOISE_FRAMES = 6  # the first 180 ms estimate the room noise
START_FRAMES = 3  # speech must last 90 ms to count
PREROLL_FRAMES = 10  # 300 ms kept before the first speech frame


class MicrophoneUnavailable(Exception):
    """The microphone is missing or blocked. `reason` is a short plain word."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def frame_rms(frame: Any) -> float:
    """RMS of an int16 frame as a fraction of full scale (0..1)."""
    x = np.asarray(frame, dtype=np.float64)
    if x.size == 0:
        return 0.0
    return float(math.sqrt(float(np.mean(x * x)))) / 32768.0


@dataclass
class Endpoint:
    done: bool
    reason: Optional[str]  # "silence", "max_length", "no_speech" when done
    speech_started: bool
    speech_start_frame: Optional[int]  # index of the first frame of the speech


class EndpointDetector:
    """Feed frames one at a time; `feed` returns the state after each. PURE: no clock, no audio device."""

    def __init__(self, max_s: float, silence_s: float, start_timeout_s: float, frame_ms: int = FRAME_MS) -> None:
        self.max_frames = max(1, int(round(max_s * 1000 / frame_ms)))
        self.silence_frames = max(1, int(round(silence_s * 1000 / frame_ms)))
        self.start_timeout_frames = max(1, int(round(start_timeout_s * 1000 / frame_ms)))
        self._noise: List[float] = []
        self.threshold = MIN_THRESHOLD
        self.index = -1
        self._loud_run = 0
        self._quiet_run = 0
        self.speech_start_frame: Optional[int] = None
        self.done = False
        self.reason: Optional[str] = None

    def feed(self, frame: Any) -> Endpoint:
        if self.done:
            return self.state()
        self.index += 1
        level = frame_rms(frame)
        if len(self._noise) < NOISE_FRAMES:
            self._noise.append(level)
            if len(self._noise) == NOISE_FRAMES:
                self.threshold = max(MIN_THRESHOLD, NOISE_FACTOR * min(self._noise))
        loud = level >= self.threshold
        if self.speech_start_frame is None:
            self._loud_run = self._loud_run + 1 if loud else 0
            if self._loud_run >= START_FRAMES:
                self.speech_start_frame = self.index - START_FRAMES + 1
                self._quiet_run = 0
            elif self.index + 1 >= self.start_timeout_frames:
                self._finish("no_speech")
        else:
            self._quiet_run = 0 if loud else self._quiet_run + 1
            if self._quiet_run >= self.silence_frames:
                self._finish("silence")
        if not self.done and self.index + 1 >= self.max_frames:
            self._finish("max_length")
        return self.state()

    def _finish(self, reason: str) -> None:
        self.done = True
        self.reason = reason

    def state(self) -> Endpoint:
        return Endpoint(self.done, self.reason, self.speech_start_frame is not None, self.speech_start_frame)


def detect(frames: Iterable[Any], max_s: float, silence_s: float, start_timeout_s: float) -> Tuple[Endpoint, int]:
    """Run the detector over frames. Returns (final state, number of frames consumed)."""
    detector = EndpointDetector(max_s, silence_s, start_timeout_s)
    used = 0
    state = detector.state()
    for frame in frames:
        used += 1
        state = detector.feed(frame)
        if state.done:
            break
    return state, used


def to_wav_bytes(samples: Any) -> bytes:
    """int16 mono samples -> WAV bytes (stdlib wave)."""
    data = np.asarray(samples, dtype="<i2")
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(data.tobytes())
    return buffer.getvalue()


@dataclass
class Recording:
    wav: Optional[bytes]  # None when no speech was heard
    duration_s: float  # how long the microphone was open
    speech_heard: bool
    reason: Optional[str]  # why recording ended


def _microphone_frames() -> Iterator[Any]:
    """Frames from the real microphone (blocking reads of FRAME_SAMPLES). Raises MicrophoneUnavailable."""
    try:
        import sounddevice as sd  # lazy: only the client program needs it
    except ImportError:
        raise MicrophoneUnavailable("sounddevice_not_installed") from None
    try:
        stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16", blocksize=FRAME_SAMPLES)
        stream.start()
    except Exception as exc:  # no device, permission denied, device busy
        raise MicrophoneUnavailable(_reason_word(exc)) from None
    try:
        while True:
            data, _ = stream.read(FRAME_SAMPLES)
            yield np.asarray(data).reshape(-1)
    except Exception as exc:
        raise MicrophoneUnavailable(_reason_word(exc)) from None
    finally:
        try:
            stream.stop()
            stream.close()
        except Exception:
            pass


def _reason_word(exc: Exception) -> str:
    text = str(exc).lower()
    if "permission" in text or "denied" in text or "access" in text:
        return "permission_denied"
    if "no default" in text or "no device" in text or "invalid device" in text:
        return "device_missing"
    return "device_error"


def record_until_silence(max_s: float, silence_s: float, start_timeout_s: float,
                         on_level: Optional[Callable[[float], Any]] = None,
                         frames: Optional[Iterable[Any]] = None) -> Recording:
    """Record until she stops talking. `frames` (int16 arrays) replaces the microphone in tests.

    Returns a Recording with WAV bytes of the speech (with a short lead-in) or no WAV when nobody spoke."""
    source: Iterable[Any] = frames if frames is not None else _microphone_frames()
    detector = EndpointDetector(max_s, silence_s, start_timeout_s)
    kept: List[Any] = []
    for frame in source:
        arr = np.asarray(frame, dtype=np.int16).reshape(-1)
        kept.append(arr)
        state = detector.feed(arr)
        if on_level is not None:
            on_level(min(1.0, frame_rms(arr) * 8.0))  # a bar of 0..1 for the window
        if state.done:
            break
    state = detector.state()
    duration = len(kept) * FRAME_MS / 1000.0
    reason = state.reason or "stream_ended"
    if not state.speech_started or state.speech_start_frame is None:
        return Recording(None, duration, False, reason)
    first = max(0, state.speech_start_frame - PREROLL_FRAMES)
    return Recording(to_wav_bytes(np.concatenate(kept[first:])), duration, True, reason)


def microphone_status() -> Tuple[str, Optional[str]]:
    """("available", None) when an input device exists, else ("unavailable", reason)."""
    try:
        import sounddevice as sd
    except ImportError:
        return "unavailable", "sounddevice_not_installed"
    try:
        sd.query_devices(kind="input")
    except Exception as exc:
        return "unavailable", _reason_word(exc)
    return "available", None


def play_pcm16k(pcm: bytes) -> None:
    """Play raw 16 kHz mono int16 audio and return when it has finished. Errors are the caller's to catch (the
    line then simply counts as played)."""
    import sounddevice as sd

    sd.play(np.frombuffer(pcm[: len(pcm) // 2 * 2], dtype="<i2"), SAMPLE_RATE)
    sd.wait()
