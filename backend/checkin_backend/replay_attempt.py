"""Replay one saved try (DEBUG_FRAMES_DIR, see link.py) through Agent 3's real smile or arms module, frame by frame.

    uv run --no-sync python -m checkin_backend.replay_attempt <frames dir>/<call_id>/arms_<attempt_id> [options]

Prints per frame what the module saw (face size, head tilt, each hand's side of her face and distance from the
midline; for the smile its reading), the module's observations, its final result and, for arms, both hands' values,
compared with her usual. Use it to explain why a try was or was not measured, or outside her usual.

Options: --mirrored on|off (default: the server's UW_MIRRORED), --usual stored|none (stored: her usual as it is now;
the call froze it at its start, so after a normal call it may differ), --every N (print every Nth frame).
"""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

from .debug_probe import FrameProbe, plain
from .runner import ensure_analysis_path


class RecordingProbe(FrameProbe):
    """Keeps every frame the module reports, not only the latest."""

    def __init__(self) -> None:
        super().__init__()
        self.all: list[dict[str, Any]] = []

    def _keep(self, frame: dict[str, Any]) -> None:
        super()._keep(frame)
        self.all.append(frame)


def frames_of(folder: Path) -> list[tuple[int, bytes]]:
    return [(int(p.stem), p.read_bytes()) for p in sorted(folder.glob("*.jpg"), key=lambda p: int(p.stem))]


def arms_line(frame: dict[str, Any]) -> str:
    from analysis.debug_view import arms_phase_of

    face = "no face" if not frame.get("face") else (
        f"eyes {frame.get('eye_distance_px') or 0:.0f}px" + (" SMALL" if frame.get("face_small") else "")
        + (f" tilt {frame['roll_deg']:.0f}°" if frame.get("roll_deg") is not None else ""))
    hands = ", ".join(f"{(h.get('side') or '?').replace('her_', '')}@{h.get('eye_widths') or 0:.1f}"
                      for h in frame.get("hands") or []) or "no hand"
    return (f"{frame['ms']:6d} ms  {arms_phase_of(frame['ms']):10s} want {frame.get('expected') or '-':5s}  "
            f"{face:28s} hands: {hands}")


def smile_line(frame: dict[str, Any]) -> str:
    reading = frame.get("reading") or {}
    numbers = " ".join(f"{k}={reading[k]:.3f}" for k in ("smile_strength", "corner_height")
                       if isinstance(reading.get(k), (int, float)))
    state = "usable" if reading.get("usable") else f"unusable ({reading.get('reason')})"
    return f"{frame['ms']:6d} ms  face {'yes' if frame.get('face') else 'NO '}  {state:34s} {numbers}"


async def replay(folder: Path, mirrored: bool | None, usual: str, every: int) -> dict[str, Any]:
    ensure_analysis_path()
    from analysis.backend_entry import Settings, make_baseline_source
    from analysis.session.models import TaskContext
    from analysis.tasks.stream_arms import StreamArms
    from analysis.tasks.stream_smile import StreamSmile

    task, _, attempt_id = folder.name.partition("_")
    if task not in ("smile", "arms"):
        raise SystemExit(f"{folder.name}: expected a folder named smile_<attempt> or arms_<attempt>")
    frames = frames_of(folder)
    if not frames:
        raise SystemExit(f"{folder}: no frames")
    settings = Settings(os.environ, os.environ.get("DEMO_PERSON_ID", "p_demo"))
    mirror = settings.mirrored if mirrored is None else mirrored
    baseline: dict[str, Any] = {}
    if usual == "stored":
        source = make_baseline_source(settings.person_key, settings.data_dir, settings.baseline_needed)
        baseline = source.load_baselines().get(task, {})
    probe = RecordingProbe()
    module = (StreamSmile if task == "smile" else StreamArms)(mirrored=mirror, debug=probe)
    observations: list[dict[str, Any]] = []

    async def samples():
        for sequence, (ms, data) in enumerate(frames):
            yield {"sequence": sequence, "captured_ms": ms, "format": "jpeg", "data": data}

    def emit(observation: Any) -> None:
        observations.append(plain(observation.to_dict() if hasattr(observation, "to_dict") else observation))

    context = TaskContext(f"call_replay_{attempt_id}", attempt_id, task, baseline)
    result = await module.run(context, samples(), emit)
    result = plain(result.to_dict() if hasattr(result, "to_dict") else result)
    values = module.attempt_values(attempt_id) if hasattr(module, "attempt_values") else {}

    print(f"{task} {attempt_id}: {len(frames)} frames, {frames[0][0]}-{frames[-1][0]} ms, mirrored {mirror}, "
          f"usual {'ready' if baseline.get('ready') else 'not ready / none'}")
    line = arms_line if task == "arms" else smile_line
    for index, frame in enumerate(probe.all):
        if index % max(1, every) == 0:
            print("  " + line(frame))
    if task == "arms":
        print("where the hand was, per hold:", json.dumps(probe.tally()))
    print("observations:")
    for obs in observations:
        quality = obs.get("quality") or {}
        metrics = ", ".join(f"{m.get('metric_id')}={m.get('value'):.3f}" for m in obs.get("metrics") or []
                            if isinstance(m.get("value"), (int, float)))
        print(f"  {obs.get('elapsed_ms', 0):6d} ms {obs.get('phase', ''):10s} {quality.get('status')}"
              f" {','.join(quality.get('reasons') or [])} {metrics}")
    print("result:", json.dumps({k: result.get(k) for k in ("outcome", "quality", "performance", "reading")},
                                indent=1))
    if values:
        print("per-hand values:", json.dumps(plain(values)))
        for side in ("left", "right"):
            hand = baseline.get(f"hand_{side}") or {}
            if hand.get("ready"):
                print(f"  {side}: {values.get(side, float('nan')):.3f} vs usual "
                      f"{hand['usual_low']:.3f}-{hand['usual_high']:.3f}")
    return result


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("folder", type=Path)
    parser.add_argument("--mirrored", choices=("on", "off"))
    parser.add_argument("--usual", choices=("stored", "none"), default="stored")
    parser.add_argument("--every", type=int, default=1)
    args = parser.parse_args(argv)
    mirrored = None if args.mirrored is None else args.mirrored == "on"
    asyncio.run(replay(args.folder, mirrored, args.usual, args.every))


if __name__ == "__main__":
    sys.exit(main())
