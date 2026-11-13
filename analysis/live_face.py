"""The droop test: a live webcam window.

Run from the repo root:
    python -m analysis.live_face [--camera 0] [--mirrored] [--person NAME] [--record-trials]

Default (guided): the test runs by itself with big on-screen instructions.
      Keys: q = stop (summary still printed), m = mesh dots. Add --per-label N for fewer trials.
--manual (old mode), keys: n / l / r = label the next trials normal / acted_left / acted_right.
      SPACE = start a trial (1.0 s RELAX, then 3.0 s SMILE).  m = mesh dots.  q = quit + summary.

Conventions: "left"/"right" are HER left/right. Positive reading = her LEFT
side lower or weaker. The picture shown is NOT flipped (not a mirror): your
left is on the screen's right. With --mirrored the camera image itself is
already mirrored, and it is flipped back before measuring.
"""
from __future__ import annotations

import argparse
import csv
import dataclasses
import statistics
import sys
import time
from collections import deque
from datetime import datetime

from analysis import config
from analysis import angle_summary
from analysis.droop_summary import format_summary, summarise
from analysis.face_metric import (
    clip_reading, frame_reading, matches_asked, mismatch_warning, seen_side, seen_text_face)
from analysis.guide import Guide, Signal, angle_plan, face_plan, verdict_lines

LABEL_KEYS = {ord("n"): "normal", ord("l"): "acted_left", ord("r"): "acted_right"}
CSV_FIELDS = ["trial", "label", "pose", "quality", "corner_height", "corner_lift", "smile_score",
              "corner_height_3d", "yaw_deg", "pitch_deg", "seen", "matches_asked",
              "frames_usable", "frames_total", "reason"]


LAST_RESULT: dict = {}  # filled by main(): "summary", "csv", "summary_path" (used by analysis.check)


def _fmt(v) -> str:
    return "None" if v is None else "%+.3f" % v


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Droop test with a live webcam window.")
    parser.add_argument("--camera", type=int, default=0)
    parser.add_argument("--mirrored", action="store_true", help="the camera image is mirrored")
    parser.add_argument("--person", default="test")
    parser.add_argument("--record-trials", action="store_true", help="save each trial as mp4")
    parser.add_argument("--no-angle-check", action="store_true",
                        help="do not drop frames on head angle (use if a frontal face shows 'head turned/tipped')")
    parser.add_argument("--manual", action="store_true",
                        help="old key-driven mode (n/l/r label, SPACE starts a trial)")
    parser.add_argument("--per-label", type=int, default=None,
                        help="guided mode: trials per label (default: config.GUIDE_FACE_PER_LABEL)")
    parser.add_argument("--angle", action="store_true",
                        help="the angle test: crooked-phone poses against acted droops (guided)")
    parser.add_argument("--per-pose", type=int, default=None,
                        help="angle test: trials per pose (default: config.GUIDE_ANGLE_PER_POSE)")
    args = parser.parse_args(argv)
    if args.no_angle_check or args.angle:
        # the angle test wants to SEE the effect of the angle, so frames are not dropped for it
        config.CHECK_HEAD_ANGLES = False
    angle_mode = args.angle
    guided = angle_mode or not args.manual
    LAST_RESULT.clear()

    if not config.MODEL_PATH.exists():
        print("Model file missing. Run:  python -m analysis.get_models")
        return 1

    import cv2  # imported here so --help works without the libraries
    from analysis import display
    from analysis.face_points import FacePointsExtractor

    cap = cv2.VideoCapture(args.camera)
    if not cap.isOpened():
        print("Cannot open camera %d. Try --camera 1." % args.camera)
        return 1

    config.RUNS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    base = "%s_%s" % (args.person, stamp)
    csv_path = config.RUNS_DIR / (base + ".csv")
    summary_path = config.RUNS_DIR / (base + "_summary.txt")
    if args.record_trials:
        config.RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)

    label = "normal"
    trials = []
    jitter_buf = deque(maxlen=config.JITTER_WINDOW)
    trial = None  # dict while a trial runs
    t0 = time.monotonic()
    last_ts = -1
    show_mesh = False

    def finish_trial(tr):
        if tr["writer"] is not None:
            tr["writer"].release()
        clip = clip_reading(tr["readings"])
        n = len(trials) + 1
        usable = clip.quality == "usable"
        seen = seen_side(clip.corner_height) if usable else None
        match = matches_asked(tr["label"], clip.corner_height) if usable else None
        row = {
            "trial": n, "label": tr["label"], "pose": tr.get("pose", ""), "quality": clip.quality,
            "corner_height": clip.corner_height, "corner_lift": clip.corner_lift,
            "smile_score": clip.smile_score, "corner_height_3d": clip.corner_height_3d,
            "yaw_deg": clip.yaw_deg, "pitch_deg": clip.pitch_deg,
            "seen": seen or "",
            "matches_asked": "" if match is None else ("yes" if match else "no"),
            "frames_usable": clip.frames_usable,
            "frames_total": clip.frames_total, "reason": "; ".join(clip.reasons),
        }
        trials.append(row)
        print("trial %d | %s | %s | %s | corner_height %s | corner_lift %s | smile_score %s "
              "| corner_height_3d %s | yaw %s pitch %s | seen %s %s" % (
                  n, tr["label"], tr.get("pose", "") or "-", clip.quality,
                  _fmt(clip.corner_height), _fmt(clip.corner_lift), _fmt(clip.smile_score),
                  _fmt(clip.corner_height_3d), _fmt(clip.yaw_deg), _fmt(clip.pitch_deg),
                  row["seen"] or "-", ("| " + row["reason"]) if row["reason"] else ""))
        new_file = not csv_path.exists()
        with open(csv_path, "a", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
            if new_file:
                w.writeheader()
            w.writerow(row)
        return row

    guide = None
    cur = None  # guided mode: the trial being measured
    done_since = None
    if guided:
        plan = angle_plan(args.per_pose) if angle_mode else face_plan(args.per_label)
        guide = Guide(plan, "face")

    # Resizable window that keeps its shape: the full layout is ~1500x780 and
    # may not fit a laptop screen. Drag the corner to shrink it.
    cv2.namedWindow("Droop test", cv2.WINDOW_NORMAL | cv2.WINDOW_KEEPRATIO)
    cv2.resizeWindow("Droop test", 1200, 624)

    with FacePointsExtractor(config.MODEL_PATH) as extractor:
        while True:
            ok, frame = cap.read()
            if not ok:
                print("Camera gave no frame; stopping.")
                break
            ts = int((time.monotonic() - t0) * 1000)
            if ts <= last_ts:
                ts = last_ts + 1
            last_ts = ts

            obs = extractor.process(frame, ts, mirrored=args.mirrored)
            fr = frame_reading(obs)
            if fr is not None and fr.usable:
                jitter_buf.append(fr.corner_height)

            h, w = frame.shape[:2]

            # Trial state machine
            phase_text = None
            view = None
            if guided:
                now = time.monotonic()
                ready = fr is not None and fr.usable
                view = guide.update(now, Signal(ready=ready, face_ok=ready))
                if view.collect:
                    if cur is None:
                        writer = None
                        if args.record_trials:
                            path = config.RECORDINGS_DIR / ("%s_trial%02d_%s.mp4" % (
                                base, len(trials) + 1, guide.current.label))
                            writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"),
                                                     config.RECORD_FPS, (w, h))
                        cur = {"label": guide.current.label, "pose": guide.current.pose,
                               "readings": [], "writer": writer}
                    cur["readings"].append(fr)
                    if cur["writer"] is not None:
                        cur["writer"].write(frame)
                elif view.finished_measuring:
                    if cur is not None:
                        row = finish_trial(cur)
                        cur = None
                        seen_text, warn = "", ""
                        if row["quality"] == "usable":
                            seen_text = seen_text_face(row["corner_height"])
                            warn = mismatch_warning(row["label"], row["corner_height"]) or ""
                        guide.trial_finished(row["quality"] == "usable", row["reason"],
                                             seen_text=seen_text, warning=warn)
                    else:  # no frame was collected (camera stalled): count it as unusable
                        guide.trial_finished(False, "too few usable frames")
                if view.done:
                    if done_since is None:
                        done_since = now
                        if angle_mode:
                            guide.summary_lines = tuple(
                                angle_summary.format_lines(angle_summary.summarise(trials)))
                        else:
                            text_now = format_summary(summarise(trials))
                            guide.summary_lines = tuple(verdict_lines(text_now, "face"))
                    view = dataclasses.replace(view, summary_lines=guide.summary_lines)
                    label = "normal"
                elif guide.current is not None:
                    label = guide.current.label
            elif trial is not None:
                elapsed = time.monotonic() - trial["start"]
                total = config.RELAX_SECONDS + config.SMILE_SECONDS
                if elapsed >= total:
                    finish_trial(trial)
                    trial = None
                else:
                    trial["readings"].append(fr)
                    if trial["writer"] is not None:
                        trial["writer"].write(frame)
                    if elapsed < config.RELAX_SECONDS:
                        phase_text = "RELAX  %.1f" % (config.RELAX_SECONDS - elapsed)
                    else:
                        phase_text = "SMILE  %.1f" % (total - elapsed)

            jit = statistics.pstdev(jitter_buf) if len(jitter_buf) >= 2 else None
            shown = display.render(frame, obs, fr, mirrored=args.mirrored, show_mesh=show_mesh,
                                   label=label, trials=trials, jitter=jit, phase_text=phase_text,
                                   guide=view)
            cv2.imshow("Droop test", shown)
            key = cv2.waitKey(1) & 0xFF
            if key == ord("q"):
                break
            if key == ord("m"):
                show_mesh = not show_mesh
            elif guided:
                if done_since is not None:
                    waited = time.monotonic() - done_since
                    if (key != 255 and waited > 1.0) or waited >= config.GUIDE_DONE_CLOSE_S:
                        break
            elif key in LABEL_KEYS:
                label = LABEL_KEYS[key]
            elif key == ord(" ") and trial is None:
                writer = None
                if args.record_trials:
                    path = config.RECORDINGS_DIR / ("%s_trial%02d_%s.mp4" % (base, len(trials) + 1, label))
                    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"),
                                             config.RECORD_FPS, (w, h))
                trial = {"label": label, "start": time.monotonic(), "readings": [], "writer": writer}

    for open_trial in (trial, cur):
        if open_trial is not None and open_trial["writer"] is not None:
            open_trial["writer"].release()
    cap.release()
    cv2.destroyAllWindows()

    if angle_mode:
        text = angle_summary.format_summary(angle_summary.summarise(trials))
    else:
        text = format_summary(summarise(trials))
    print("\n" + text)
    if guided and guide.notes:
        print("\nNotes: " + "; ".join(guide.notes))
    config.RUNS_DIR.mkdir(parents=True, exist_ok=True)
    with open(summary_path, "w", encoding="utf-8") as fh:
        fh.write(text + "\n")
    print("\nSaved: %s\n       %s" % (csv_path, summary_path))
    LAST_RESULT.update({"summary": text, "csv": csv_path, "summary_path": summary_path,
                        "trials": len(trials), "angle": angle_mode,
                        "verdict_lines": angle_summary.format_lines(angle_summary.summarise(trials))
                        if angle_mode else []})
    return 0


if __name__ == "__main__":
    sys.exit(main())
