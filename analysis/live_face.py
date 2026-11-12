"""The droop test: a live webcam window.

Run from the repo root:
    python -m analysis.live_face [--camera 0] [--mirrored] [--person NAME] [--record-trials]

Keys: n / l / r = label the next trials normal / acted_left / acted_right.
      SPACE = start a trial (1.0 s RELAX, then 3.0 s SMILE).  q = quit + summary.

Conventions: "left"/"right" are HER left/right. Positive reading = her LEFT
side lower or weaker. The picture shown is NOT flipped (not a mirror): your
left is on the screen's right. With --mirrored the camera image itself is
already mirrored, and it is flipped back before measuring.
"""
from __future__ import annotations

import argparse
import csv
import statistics
import sys
import time
from collections import deque
from datetime import datetime

from analysis import config
from analysis.droop_summary import format_summary, summarise
from analysis.face_metric import clip_reading, frame_reading

LABEL_KEYS = {ord("n"): "normal", ord("l"): "acted_left", ord("r"): "acted_right"}
CSV_FIELDS = ["trial", "label", "quality", "corner_height", "corner_lift", "smile_score",
              "frames_usable", "frames_total", "reason"]


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
    args = parser.parse_args(argv)
    if args.no_angle_check:
        config.CHECK_HEAD_ANGLES = False

    if not config.MODEL_PATH.exists():
        print("Model file missing. Run:  python -m analysis.get_models")
        return 1

    import cv2  # imported here so --help works without the libraries
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
    font = cv2.FONT_HERSHEY_SIMPLEX

    def put(img, text, y, color=(255, 255, 255), scale=0.6):
        cv2.putText(img, text, (10, y), font, scale, (0, 0, 0), 3, cv2.LINE_AA)
        cv2.putText(img, text, (10, y), font, scale, color, 1, cv2.LINE_AA)

    def finish_trial(tr):
        if tr["writer"] is not None:
            tr["writer"].release()
        clip = clip_reading(tr["readings"])
        n = len(trials) + 1
        row = {
            "trial": n, "label": tr["label"], "quality": clip.quality,
            "corner_height": clip.corner_height, "corner_lift": clip.corner_lift,
            "smile_score": clip.smile_score, "frames_usable": clip.frames_usable,
            "frames_total": clip.frames_total, "reason": "; ".join(clip.reasons),
        }
        trials.append(row)
        print("trial %d | %s | %s | corner_height %s | corner_lift %s | smile_score %s %s" % (
            n, tr["label"], clip.quality, _fmt(clip.corner_height), _fmt(clip.corner_lift),
            _fmt(clip.smile_score), ("| " + row["reason"]) if row["reason"] else ""))
        new_file = not csv_path.exists()
        with open(csv_path, "a", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
            if new_file:
                w.writeheader()
            w.writerow(row)

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

            shown = frame.copy()  # NOT flipped
            h, w = shown.shape[:2]

            # Points are in the un-mirrored coordinates; map back onto the shown picture.
            if obs.face_found:
                for name, (x, y) in obs.points.items():
                    sx = (w - x) if args.mirrored else x
                    cv2.circle(shown, (int(sx), int(y)), 4, (0, 255, 0), -1)
                    text = "HER " + ("L" if "left" in name else "R") + (" eye" if "eye" in name else " mouth")
                    cv2.putText(shown, text, (int(sx) + 6, int(y) - 6), font, 0.45, (0, 255, 255), 1, cv2.LINE_AA)

            # Trial state machine
            phase_text = None
            if trial is not None:
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

            put(shown, "not a mirror: your left is on screen right" if not args.mirrored
                else "camera is mirrored (flipped back for measuring)", 20, (200, 200, 200), 0.5)
            put(shown, "label: %s   trials: %d   [n/l/r label, SPACE trial, q quit]" % (label, len(trials)),
                42, (0, 200, 255), 0.5)
            if fr is not None and fr.corner_height is not None:
                put(shown, "corner_height: %s   smile_strength: %.2f" % (_fmt(fr.corner_height), fr.smile_strength), 70)
            else:
                put(shown, "corner_height: -", 70)
            angles = [obs.yaw_deg, obs.pitch_deg, obs.roll_deg]
            put(shown, "yaw/pitch/roll: " + " / ".join("-" if a is None else "%.0f" % a for a in angles)
                + "  [convention unverified]", 94)
            status = "usable" if (fr is not None and fr.usable) else ("NOT usable: %s" % (fr.reason if fr else "?"))
            put(shown, status, 118, (0, 255, 0) if status == "usable" else (0, 0, 255))
            bl, br = obs.blendshapes.get("eyeBlinkLeft", 0.0), obs.blendshapes.get("eyeBlinkRight", 0.0)
            if obs.face_found and abs(bl - br) >= config.WINK_MIN_DIFF:
                put(shown, "blink: HER %s" % ("LEFT" if bl > br else "RIGHT"), 142, (255, 200, 0))
            else:
                put(shown, "blink: -", 142)
            jit = statistics.pstdev(jitter_buf) if len(jitter_buf) >= 2 else None
            put(shown, "jitter (sd of corner_height, last %d): %s" % (config.JITTER_WINDOW, "-" if jit is None else "%.4f" % jit), 166)
            if phase_text:
                cv2.putText(shown, phase_text, (10, h - 20), font, 1.5, (0, 0, 255), 3, cv2.LINE_AA)

            cv2.imshow("droop test", shown)
            key = cv2.waitKey(1) & 0xFF
            if key == ord("q"):
                break
            if key in LABEL_KEYS:
                label = LABEL_KEYS[key]
            elif key == ord(" ") and trial is None:
                writer = None
                if args.record_trials:
                    path = config.RECORDINGS_DIR / ("%s_trial%02d_%s.mp4" % (base, len(trials) + 1, label))
                    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"),
                                             config.RECORD_FPS, (w, h))
                trial = {"label": label, "start": time.monotonic(), "readings": [], "writer": writer}

    if trial is not None and trial["writer"] is not None:
        trial["writer"].release()
    cap.release()
    cv2.destroyAllWindows()

    text = format_summary(summarise(trials))
    print("\n" + text)
    config.RUNS_DIR.mkdir(parents=True, exist_ok=True)
    with open(summary_path, "w", encoding="utf-8") as fh:
        fh.write(text + "\n")
    print("\nSaved: %s\n       %s" % (csv_path, summary_path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
