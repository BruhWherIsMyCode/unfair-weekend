"""Clip tool: a video file in, a JSON reading out.

    python -m analysis.measure_clip CLIP [--mirrored] [--task smile] [--attempt 1]
        [--primary corner_height] [--person-file analysis/data/NAME.json]
        [--label normal|acted|live] [--save]

Conventions: "left"/"right" are HER left/right; positive = her LEFT side
lower or weaker. Use --mirrored if the clip came from a mirroring camera.
"""
from __future__ import annotations

import argparse
import json
import sys

from analysis import baseline as baseline_mod
from analysis import config
from analysis.contract_shape import tool_result
from analysis.face_metric import clip_reading, frame_reading


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Measure a smile clip.")
    parser.add_argument("clip")
    parser.add_argument("--mirrored", action="store_true")
    parser.add_argument("--task", default="smile")
    parser.add_argument("--attempt", type=int, default=1)
    parser.add_argument("--primary", default="corner_height", choices=list(config.METRICS))
    parser.add_argument("--person-file", default=None)
    parser.add_argument("--label", default="live", choices=list(baseline_mod.VALID_LABELS))
    parser.add_argument("--save", action="store_true")
    args = parser.parse_args(argv)

    if args.save and not args.person_file:
        print("--save needs --person-file", file=sys.stderr)
        return 2
    if not config.MODEL_PATH.exists():
        print("Model file missing. Run:  python -m analysis.get_models", file=sys.stderr)
        return 1

    import cv2
    from analysis.face_points import FacePointsExtractor

    cap = cv2.VideoCapture(args.clip)
    if not cap.isOpened():
        print("Cannot open clip: %s" % args.clip, file=sys.stderr)
        return 1
    fps = cap.get(cv2.CAP_PROP_FPS)
    if not fps or fps != fps or fps <= 0:
        fps = config.DEFAULT_FPS

    readings = []
    with FacePointsExtractor(config.MODEL_PATH) as extractor:
        i = 0
        last_ts = -1
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            ts = max(int(i * 1000.0 / fps), last_ts + 1)
            last_ts = ts
            readings.append(frame_reading(extractor.process(frame, ts, mirrored=args.mirrored)))
            i += 1
    cap.release()

    clip = clip_reading(readings)
    person = baseline_mod.load_person(args.person_file) if args.person_file else {"sessions": []}
    base = baseline_mod.baseline_for(person, args.primary)
    print(json.dumps(tool_result(args.task, args.attempt, clip, args.primary, base), indent=2))

    if args.save:
        if clip.quality == "usable":
            baseline_mod.add_session(args.person_file, clip.readings(), args.label)
            print("Saved session (label %s) to %s" % (args.label, args.person_file), file=sys.stderr)
        else:
            print("Clip unusable; not saved.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
