"""The voice activity detector and the recorder, on synthetic int16 frames. No audio device."""
from __future__ import annotations

import io
import wave

import numpy as np

from analysis.pc import mic
from analysis.pc.mic import EndpointDetector, detect, frame_rms, record_until_silence


def frames(seconds, amplitude, freq=220.0, start_s=0.0):
    """30 ms frames of a sine of the given amplitude (0 = digital silence)."""
    n = int(round(seconds * 1000 / mic.FRAME_MS))
    out = []
    for i in range(n):
        t = (np.arange(mic.FRAME_SAMPLES) + (i * mic.FRAME_SAMPLES)) / mic.SAMPLE_RATE + start_s
        out.append((amplitude * np.sin(2 * np.pi * freq * t)).astype(np.int16))
    return out


def room(seconds, level=40):
    rng = np.random.default_rng(1)
    return [rng.integers(-level, level, mic.FRAME_SAMPLES).astype(np.int16) for _ in range(int(seconds * 1000 / mic.FRAME_MS))]


SPEECH = 8000


def test_frame_rms_is_a_fraction_of_full_scale():
    assert frame_rms(np.zeros(480, dtype=np.int16)) == 0.0
    assert abs(frame_rms(np.full(480, 16384, dtype=np.int16)) - 0.5) < 1e-6
    assert frame_rms(np.array([], dtype=np.int16)) == 0.0


def test_silence_only_ends_with_no_speech_at_the_start_timeout():
    state, used = detect(room(10), max_s=12, silence_s=1.5, start_timeout_s=5.0)
    assert state.done and state.reason == "no_speech" and not state.speech_started
    assert used == round(5.0 * 1000 / mic.FRAME_MS)


def test_silence_speech_silence_ends_after_the_silence_window():
    data = room(1.0) + frames(2.0, SPEECH) + room(3.0)
    state, used = detect(data, max_s=12, silence_s=1.5, start_timeout_s=5.0)
    assert state.done and state.reason == "silence" and state.speech_started
    assert state.speech_start_frame == len(room(1.0))  # the first speech frame
    quiet = round(1.5 * 1000 / mic.FRAME_MS)
    assert used == len(room(1.0)) + len(frames(2.0, SPEECH)) + quiet


def test_a_short_pause_inside_speech_does_not_end_it():
    data = room(0.5) + frames(1.0, SPEECH) + room(0.6) + frames(1.0, SPEECH) + room(2.0)
    state, used = detect(data, max_s=12, silence_s=1.5, start_timeout_s=5.0)
    assert state.reason == "silence" and used > len(room(0.5)) + 2 * len(frames(1.0, SPEECH)) + len(room(0.6))


def test_a_click_is_not_speech():
    data = room(1.0) + frames(0.06, 20000) + room(6.0)  # two frames only
    state, _ = detect(data, max_s=12, silence_s=1.5, start_timeout_s=5.0)
    assert state.reason == "no_speech" and not state.speech_started


def test_continuous_speech_stops_at_the_maximum_length():
    state, used = detect(room(0.5) + frames(20.0, SPEECH), max_s=12, silence_s=1.5, start_timeout_s=5.0)
    assert state.reason == "max_length" and used == round(12 * 1000 / mic.FRAME_MS)


def test_the_threshold_follows_a_noisy_room():
    detector = EndpointDetector(12, 1.5, 5.0)
    for f in frames(0.3, 1500, freq=50):  # a steady hum, about -33 dBFS
        detector.feed(f)
    assert detector.threshold > mic.MIN_THRESHOLD
    quiet = EndpointDetector(12, 1.5, 5.0)
    for f in room(0.3):
        quiet.feed(f)
    assert quiet.threshold == mic.MIN_THRESHOLD


def test_a_finished_detector_stays_finished():
    detector = EndpointDetector(1, 0.3, 0.3)
    for f in room(1.0):
        state = detector.feed(f)
    assert state.done
    assert detector.feed(frames(0.03, SPEECH)[0]).reason == state.reason


def test_record_until_silence_returns_wav_bytes_of_the_speech():
    levels = []
    data = room(1.0) + frames(2.0, SPEECH) + room(3.0)
    rec = record_until_silence(12, 1.5, 5.0, on_level=levels.append, frames=iter(data))
    assert rec.speech_heard and rec.reason == "silence" and rec.wav
    with wave.open(io.BytesIO(rec.wav), "rb") as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, 16000)
        lead = (len(room(1.0)) - mic.PREROLL_FRAMES) * mic.FRAME_SAMPLES  # the quiet start is cut, a lead-in kept
        assert w.getnframes() == (len(data) - len(room(3.0)) + round(1.5 * 1000 / mic.FRAME_MS) - lead // mic.FRAME_SAMPLES) * mic.FRAME_SAMPLES
    assert abs(rec.duration_s - (len(data) - len(room(3.0)) + 50) * 0.03) < 1e-6
    assert levels and max(levels) > 0.5 and min(levels) < 0.1 and all(0.0 <= v <= 1.0 for v in levels)


def test_record_until_silence_without_speech_has_no_wav():
    rec = record_until_silence(12, 1.5, 2.0, frames=iter(room(10)))
    assert rec.wav is None and rec.speech_heard is False and rec.reason == "no_speech"
    assert abs(rec.duration_s - 2.0) < 0.05


def test_the_source_running_out_is_reported():
    rec = record_until_silence(12, 1.5, 5.0, frames=iter(room(0.5)))
    assert rec.wav is None and rec.reason == "stream_ended"


def test_wav_helper_round_trips():
    samples = (np.arange(1600) % 100).astype(np.int16)
    with wave.open(io.BytesIO(mic.to_wav_bytes(samples)), "rb") as w:
        assert w.getnframes() == 1600 and np.array_equal(np.frombuffer(w.readframes(1600), dtype="<i2"), samples)
