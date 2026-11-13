"""Task tool for clips: a video file in, the result JSON out.

    python -m analysis.run_task --task hand_left CLIP.mp4 [--person NAME] [--attempt 1]
        [--mirrored] [--save --label normal|acted|live] [--hold START_S END_S]
"""
from __future__ import annotations

import argparse
import json
import sys

from analysis import baseline as baseline_mod
from analysis import config
from analysis.tasks import TASKS, run_task_with_values


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Measure one task in a video clip.")
    parser.add_argument("clip")
    parser.add_argument("--task", required=True, choices=sorted(TASKS))
    parser.add_argument("--person", default=None)
    parser.add_argument("--attempt", type=int, default=1)
    parser.add_argument("--mirrored", action="store_true")
    parser.add_argument("--save", action="store_true")
    parser.add_argument("--label", default="live", choices=list(baseline_mod.VALID_LABELS))
    parser.add_argument("--hold", type=float, nargs=2, metavar=("START_S", "END_S"), default=None,
                        help="hand tasks: hold window in seconds (default: whole clip after the settle time)")
    parser.add_argument("--no-angle-check", action="store_true", help="do not drop frames on head angle")
    args = parser.parse_args(argv)
    if args.no_angle_check:
        config.CHECK_HEAD_ANGLES = False
    if args.save and not args.person:
        print("--save needs --person", file=sys.stderr)
        return 2

    hold = (args.hold[0] * 1000.0, args.hold[1] * 1000.0) if args.hold else None
    try:
        result, values = run_task_with_values(args.task, args.clip, args.person, args.attempt,
                                              args.mirrored, hold)
    except (OSError, FileNotFoundError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))

    if args.save:
        if result["quality"] == "usable":
            path = baseline_mod.person_path(args.person)
            baseline_mod.add_session(path, values, args.label)
            print("Saved session (label %s) to %s" % (args.label, path), file=sys.stderr)
        else:
            print("Clip unusable; not saved.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
