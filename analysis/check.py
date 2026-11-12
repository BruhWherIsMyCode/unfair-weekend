"""One command: the guided face test, then the guided hand test.

Run from the repo root:
    python -m analysis.check --person NAME [--mirrored] [--camera N] [--hold S]
        [--only face|hands] [--per-label N] [--per-mode N] [--no-angle-check]

Both windows run by themselves with big on-screen text; the only key is q.
At the end both summaries are printed together with the files they were saved to.
"""
from __future__ import annotations

import argparse
import sys

from analysis import config


def _common(args) -> list:
    out = ["--camera", str(args.camera), "--person", args.person]
    if args.mirrored:
        out.append("--mirrored")
    if args.no_angle_check:
        out.append("--no-angle-check")
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Guided face test, then guided hand test.")
    parser.add_argument("--person", required=True)
    parser.add_argument("--mirrored", action="store_true", help="the camera image is mirrored")
    parser.add_argument("--camera", type=int, default=0)
    parser.add_argument("--hold", type=float, default=None,
                        help="seconds of the hand hold (default: config.HAND_HOLD_S)")
    parser.add_argument("--only", choices=("face", "hands"), default=None,
                        help="run only the face test or only the hand test")
    parser.add_argument("--per-label", type=int, default=None,
                        help="face trials per label (default: config.GUIDE_FACE_PER_LABEL)")
    parser.add_argument("--per-mode", type=int, default=None,
                        help="hand trials per hand and mode (default: config.GUIDE_HAND_PER_MODE)")
    parser.add_argument("--no-angle-check", action="store_true",
                        help="do not drop frames on head angle")
    args = parser.parse_args(argv)

    # imported here so --help works without the camera libraries
    from analysis import live_face, live_hand

    results = []  # (title, LAST_RESULT copy)
    notes = []

    if args.only in (None, "face"):
        face_args = _common(args)
        if args.per_label is not None:
            face_args += ["--per-label", str(args.per_label)]
        code = live_face.main(face_args)
        if code != 0:
            return code
        results.append(("FACE TEST", dict(live_face.LAST_RESULT)))

    if args.only in (None, "hands"):
        if not config.HAND_MODEL_PATH.exists():
            msg = ("The hand model is missing, so the hand test was skipped.\n"
                   "Get it with:  python -m analysis.get_models\n"
                   "then run again (add --only hands to run just the hand test).")
            if args.only == "hands":
                print(msg)
                return 1
            notes.append(msg)
        else:
            hand_args = _common(args)
            if args.hold is not None:
                hand_args += ["--hold", str(args.hold)]
            if args.per_mode is not None:
                hand_args += ["--per-mode", str(args.per_mode)]
            code = live_hand.main(hand_args)
            if code != 0:
                return code
            results.append(("HAND TEST", dict(live_hand.LAST_RESULT)))

    print("\n" + "=" * 60)
    for title, res in results:
        print("%s\n%s" % (title, "-" * len(title)))
        print(res.get("summary", "(no summary)"))
        if res.get("trials"):
            print("Saved: %s\n       %s\n" % (res.get("csv"), res.get("summary_path")))
        else:
            print("No trials were recorded, nothing saved.\n")
    for note in notes:
        print(note)
    return 0


if __name__ == "__main__":
    sys.exit(main())
