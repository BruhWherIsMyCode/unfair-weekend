"""The hand test: a live webcam window.

Run from the repo root:
    python -m analysis.live_hand [--camera 0] [--mirrored] [--person NAME] [--hold SECONDS]
        [--no-angle-check]

Default (guided): the test runs by itself with big on-screen instructions.
      Keys: q = stop (summary still printed), m = hand points. --per-mode N = fewer trials.
--manual (old mode), keys: 1 / 2 = her left / her right hand.  n / w = normal / acted weak.
      SPACE = start a trial (settle, then hold).  m = hand points.  q = quit + summary.

Conventions: "left"/"right" are HER left/right. The picture shown is NOT a
mirror. With --mirrored the camera image itself is already mirrored, and it is
flipped back before measuring. If the panel says "wrong hand" when you raise
the hand you were asked to, restart with (or without) --mirrored.
"""
from __future__ import annotations

import argparse
import csv
import dataclasses
import statistics
import sys
import time
from datetime import datetime

from analysis import config
from analysis.guide import Guide, Signal, hand_plan, verdict_lines
from analysis.hand_metric import hand_clip_reading, hand_frame_reading, seen_text_hand
from analysis.hand_summary import format_summary, summarise

CSV_FIELDS = ["trial", "hand", "mode", "quality", "hand_sink", "hand_height", "palm_turn",
              "hand_shake", "dropped", "frames_usable", "frames_total", "reason", "notes"]


LAST_RESULT: dict = {}  # filled by main(): "summary", "csv", "summary_path" (used by analysis.check)


def _fmt(v) -> str:
    return "None" if v is None else "%+.3f" % v


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Hand test with a live webcam window.")
    parser.add_argument("--camera", type=int, default=0)
    parser.add_argument("--mirrored", action="store_true", help="the camera image is mirrored")
    parser.add_argument("--person", default="test")
    parser.add_argument("--hold", type=float, default=None,
                        help="seconds of the hold (default: config.HAND_HOLD_S)")
    parser.add_argument("--no-angle-check", action="store_true",
                        help="do not drop frames on head angle (accepted for symmetry with live_face)")
    parser.add_argument("--manual", action="store_true",
                        help="old key-driven mode (1/2 hand, n/w mode, SPACE starts a trial)")
    parser.add_argument("--per-mode", type=int, default=None,
                        help="guided mode: trials per hand and mode (default: config.GUIDE_HAND_PER_MODE)")
    args = parser.parse_args(argv)
    if args.no_angle_check:
        config.CHECK_HEAD_ANGLES = False
    if args.hold is not None:
        config.HAND_HOLD_S = args.hold
    guided = not args.manual
    LAST_RESULT.clear()

    for path in (config.MODEL_PATH, config.HAND_MODEL_PATH):
        if not path.exists():
            print("Model file missing: %s\nRun:  python -m analysis.get_models" % path)
            return 1

    import cv2  # imported here so --help works without the libraries
    from analysis import hand_display
    from analysis.hand_points import HandFrameReader

    cap = cv2.VideoCapture(args.camera)
    if not cap.isOpened():
        print("Cannot open camera %d. Try --camera 1." % args.camera)
        return 1

    config.RUNS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    base = "hand_%s_%s" % (args.person, stamp)
    csv_path = config.RUNS_DIR / (base + ".csv")
    summary_path = config.RUNS_DIR / (base + "_summary.txt")

    hand = "left"
    mode = "normal"
    trials = []
    trial = None
    t0 = time.monotonic()
    last_ts = -1
    show_points = True

    def finish_trial(tr):
        clip = hand_clip_reading(tr["readings"], tr["hold_start_ms"], tr["hold_end_ms"])
        n = len(trials) + 1
        row = {
            "trial": n, "hand": tr["hand"], "mode": tr["mode"], "quality": clip.quality,
            "hand_sink": clip.hand_sink, "hand_height": clip.hand_height,
            "palm_turn": clip.palm_turn, "hand_shake": clip.hand_shake,
            "dropped": clip.dropped, "frames_usable": clip.frames_usable,
            "frames_total": clip.frames_total, "reason": "; ".join(clip.reasons),
            "notes": "; ".join(clip.notes),
        }
        trials.append(row)
        print("trial %d | %s | %s | %s | sink %s | height %s | turn %s | shake %s %s" % (
            n, tr["hand"], tr["mode"], clip.quality, _fmt(clip.hand_sink), _fmt(clip.hand_height),
            _fmt(clip.palm_turn), _fmt(clip.hand_shake),
            ("| " + row["reason"]) if row["reason"] else ""))
        new_file = not csv_path.exists()
        with open(csv_path, "a", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
            if new_file:
                w.writeheader()
            w.writerow(row)
        return row

    guide = Guide(hand_plan(args.per_mode), "hand") if guided else None
    done_since = None

    title = "Hand test"
    cv2.namedWindow(title, cv2.WINDOW_NORMAL | cv2.WINDOW_KEEPRATIO)
    cv2.resizeWindow(title, 1200, 700)

    with HandFrameReader() as reader:
        while True:
            ok, frame = cap.read()
            if not ok:
                print("Camera gave no frame; stopping.")
                break
            ts = int((time.monotonic() - t0) * 1000)
            if ts <= last_ts:
                ts = last_ts + 1
            last_ts = ts

            obs, hands = reader.process(frame, ts, mirrored=args.mirrored)
            if guided and guide.current is not None:
                hand = guide.current.side
                mode = guide.current.label
            expected = trial["hand"] if trial is not None else hand
            hr = hand_frame_reading(obs, hands, expected)

            phase_text = None
            start_height = None
            sink = None
            turn = None
            view = None
            if guided:
                now = time.monotonic()
                view = guide.update(now, Signal(ready=hr.usable, hand_seen_any=bool(hands),
                                                wrong_side=hr.wrong_side, face_ok=hr.face_ok))
                if view.collect:
                    if trial is None:
                        # the guide measured from guide.measure_start_s; convert to the ts clock
                        start_ms = (guide.hold_start_s - t0) * 1000.0
                        trial = {"hand": guide.current.side, "mode": guide.current.label,
                                 "start": guide.measure_start_s, "readings": [],
                                 "hold_start_ms": start_ms,
                                 "hold_end_ms": start_ms + config.HAND_HOLD_S * 1000.0}
                    trial["readings"].append(hr)
                    if view.phase == guide.names[1]:  # HOLD: live sink / turn numbers
                        early = [f for f in trial["readings"]
                                 if f.usable and trial["hold_start_ms"] <= f.timestamp_ms
                                 < trial["hold_start_ms"] + config.HAND_WINDOW_S * 1000.0]
                        if early:
                            start_height = statistics.median(f.height for f in early)
                            ratios = [f.palm_width_ratio for f in early if f.palm_width_ratio is not None]
                            if hr.usable:
                                sink = hr.height - start_height
                                if ratios and hr.palm_width_ratio is not None:
                                    rs = statistics.median(ratios)
                                    if rs > 1e-9:
                                        turn = (rs - hr.palm_width_ratio) / rs
                elif view.finished_measuring:
                    if trial is not None:
                        row = finish_trial(trial)
                        trial = None
                        seen = ""
                        if row["quality"] == "usable":
                            seen = seen_text_hand(row["hand_sink"], row["palm_turn"],
                                                  bool(row["dropped"]))
                        guide.trial_finished(row["quality"] == "usable", row["reason"],
                                             seen_text=seen)
                    else:  # no frame was collected (camera stalled): count it as unusable
                        guide.trial_finished(False, "too few usable frames")
                if view.done:
                    if done_since is None:
                        done_since = now
                        guide.summary_lines = tuple(
                            verdict_lines(format_summary(summarise(trials)), "hand"))
                    view = dataclasses.replace(view, summary_lines=guide.summary_lines)
            elif trial is not None:
                elapsed = time.monotonic() - trial["start"]
                total = config.HAND_SETTLE_S + config.HAND_HOLD_S
                if elapsed >= total:
                    finish_trial(trial)
                    trial = None
                else:
                    trial["readings"].append(hr)
                    if elapsed < config.HAND_SETTLE_S:
                        phase_text = "GET READY  %.1f" % (config.HAND_SETTLE_S - elapsed)
                    else:
                        phase_text = "HOLD  %.1f" % (total - elapsed)
                        # start height / ratio: first window of the hold, usable frames so far
                        early = [f for f in trial["readings"]
                                 if f.usable and trial["hold_start_ms"] <= f.timestamp_ms
                                 < trial["hold_start_ms"] + config.HAND_WINDOW_S * 1000.0]
                        if early:
                            start_height = statistics.median(f.height for f in early)
                            ratios = [f.palm_width_ratio for f in early if f.palm_width_ratio is not None]
                            if hr.usable:
                                sink = hr.height - start_height
                                if ratios and hr.palm_width_ratio is not None:
                                    rs = statistics.median(ratios)
                                    if rs > 1e-9:
                                        turn = (rs - hr.palm_width_ratio) / rs

            shown = hand_display.render(
                frame, obs, hr, mirrored=args.mirrored, show_points=show_points,
                hand=expected, mode=trial["mode"] if trial is not None else mode, trials=trials,
                phase_text=phase_text, trial_active=trial is not None,
                start_height=start_height, sink=sink, turn=turn, guide=view)
            cv2.imshow(title, shown)
            key = cv2.waitKey(1) & 0xFF
            if key == ord("q"):
                break
            if key == ord("m"):
                show_points = not show_points
            elif guided:
                if done_since is not None:
                    waited = time.monotonic() - done_since
                    if (key != 255 and waited > 1.0) or waited >= config.GUIDE_DONE_CLOSE_S:
                        break
            elif trial is None and key == ord("1"):
                hand = "left"
            elif trial is None and key == ord("2"):
                hand = "right"
            elif trial is None and key == ord("n"):
                mode = "normal"
            elif trial is None and key == ord("w"):
                mode = "weak"
            elif key == ord(" ") and trial is None:
                now = time.monotonic()
                start_ms = (now - t0) * 1000.0 + config.HAND_SETTLE_S * 1000.0
                trial = {
                    "hand": hand, "mode": mode, "start": now, "readings": [],
                    "hold_start_ms": start_ms,
                    "hold_end_ms": start_ms + config.HAND_HOLD_S * 1000.0,
                }

    cap.release()
    cv2.destroyAllWindows()

    text = format_summary(summarise(trials))
    print("\n" + text)
    if guided and guide.notes:
        print("\nNotes: " + "; ".join(guide.notes))
    with open(summary_path, "w", encoding="utf-8") as fh:
        fh.write(text + "\n")
    if trials:
        print("\nSaved: %s\n       %s" % (csv_path, summary_path))
    LAST_RESULT.update({"summary": text, "csv": csv_path, "summary_path": summary_path,
                        "trials": len(trials)})
    return 0


if __name__ == "__main__":
    sys.exit(main())
