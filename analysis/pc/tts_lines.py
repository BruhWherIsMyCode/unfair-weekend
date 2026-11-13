"""Line audio for `--tts elevenlabs`: every line the call can play is synthesized ONCE at server start.

    analysis/runs/tts_cache/<voice_id>/<line_id>.pcm   raw PCM 16 kHz mono int16 (git-ignored, reused when present)
    analysis/runs/tts_cache/<voice_id>/<line_id>.txt   the text that was spoken; a changed text (or name) re-synthesizes

so a restart costs nothing. The result is base64 audio per line id, attached to the `say` message. Any failure is
logged and that line (and every line after the first failure) goes out as text only; a call never depends on it.
"""
from __future__ import annotations

import base64
from pathlib import Path
from typing import Any, Callable, Dict

from analysis.pc.link import MAX_AUDIO_B64_CHARS
from analysis.pc.remote_adapter import line_text

NOT_SPOKEN = ("sentence",)  # the sentence text itself is only a reference for the metric, never a line


def lines_to_speak(script: Dict[str, str], person_name: str) -> Dict[str, str]:
    """line id -> the text with {name} filled, for every line the call can play."""
    return {line_id: line_text(line_id, script, person_name) for line_id in script
            if line_id not in NOT_SPOKEN and not line_id.startswith("_")}


def _safe(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in name) or "voice"


def prepare_line_audio(script: Dict[str, str], person_name: str, voice_id: str, cache_root: Path,
                       synth: Callable[[str], bytes], out: Callable[[str], Any] = print) -> Dict[str, str]:
    """line id -> base64 PCM for the lines that could be prepared. `synth(text)` returns PCM bytes."""
    folder = Path(cache_root) / _safe(voice_id)
    audio: Dict[str, str] = {}
    made = reused = 0
    for line_id, text in lines_to_speak(script, person_name).items():
        pcm_path, txt_path = folder / ("%s.pcm" % _safe(line_id)), folder / ("%s.txt" % _safe(line_id))
        pcm = None
        try:
            if pcm_path.exists() and txt_path.exists() and txt_path.read_text("utf-8") == text:
                pcm = pcm_path.read_bytes()
                reused += 1
        except OSError:
            pcm = None
        if pcm is None:
            try:
                pcm = synth(text)
            except Exception as exc:  # one failure: the rest goes out as text (do not burn requests on a bad key)
                out("tts: line %s could not be synthesized (%s); lines without audio are sent as text only"
                    % (line_id, type(exc).__name__ if not hasattr(exc, "status") else "HTTP %s" % exc.status))
                break
            try:
                folder.mkdir(parents=True, exist_ok=True)
                pcm_path.write_bytes(pcm)
                txt_path.write_text(text, "utf-8")
            except OSError:
                out("tts: could not write the cache for line %s" % line_id)
            made += 1
        encoded = base64.b64encode(pcm).decode("ascii")
        if len(encoded) > MAX_AUDIO_B64_CHARS:
            out("tts: line %s is too long to send (%d KB); text only" % (line_id, len(encoded) // 1024))
            continue
        audio[line_id] = encoded
    out("tts: %d lines ready (%d new, %d from the cache) with voice %s" % (len(audio), made, reused, voice_id))
    return audio
