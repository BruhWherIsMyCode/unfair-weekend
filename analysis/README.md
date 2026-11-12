# Face tool (first version)

## Quick start (no keys)

```
git pull
python -m analysis.get_models
python -m analysis.check --person NAME
```

(Run from the repo root, with the virtual environment from "Setup" below active.)

The window tells the person what to do in big text at the top; nobody presses keys. The only key is `q` (stop; the summary still prints). The person is asked to:

- **Face part, about 3 minutes.** Look at the camera and give a big normal smile when it says SMILE (5 times), then act a droop on their LEFT side (5 times), then on their RIGHT side (5 times).
- **Hand part, 3-4 minutes.** Hold the LEFT hand up next to the face, palm to the camera, and keep it still (5 times); then do it again acting weak, letting the hand sink and the palm turn in (5 times); then the same for the RIGHT hand. Between trials the text says "Lower your hand".

Just follow the big text. If a trial does not count (face or hand not clear), the text says why and asks again, up to twice, then moves on.

**The end screen.** The banner says "Finished. Results below." and plain verdict lines are shown on the picture: for the face, per reading, whether the acted droop on each side separates from the normal smiles; for the hands, one line per hand. Press any key to close (it also closes by itself after 60 s). Both summaries are then printed together with the CSV and summary file paths in `analysis/runs/`.

**`--mirrored` symptom.** If the text says "I see your other hand. If you ARE raising your LEFT hand, ..." while you are raising the hand you were asked to, left and right are swapped: close the window and start again with `--mirrored` (or without it, if you had it on).

**Quick run:** `python -m analysis.check --person NAME --per-label 3 --per-mode 3`. Other switches: `--only face` or `--only hands`, `--hold SECONDS`, `--camera N`.

**Old key-driven mode:** `python -m analysis.live_face --manual` and `python -m analysis.live_hand --manual` (the keys are described below). The guided windows can also be run on their own: `python -m analysis.live_face --person NAME`, `python -m analysis.live_hand --person NAME`.

Written, not run yet: the guided mode has not been tried with a camera. All the timings (`GUIDE_*` in `config.py`) are first guesses.

A video of a person smiling goes in. Numbers describing how level the two mouth corners are come out, and they are compared with that person's own usual range. Nothing is trained: Google MediaPipe Face Landmarker finds 478 points on the face, and our own arithmetic does the rest.

Status: written, **not run yet**. No camera was available when it was written. Every threshold is a first guess (see `config.py`).

## Setup

You need Python 3.9 or newer. Run everything from the repo root.

**Windows**
```
python -m venv .venv
.venv\Scripts\activate
pip install -r analysis/requirements.txt
python -m analysis.get_models
```

**macOS (Apple chip) and Linux**
```
python3 -m venv .venv
source .venv/bin/activate
pip install -r analysis/requirements.txt
python -m analysis.get_models
```

The current mediapipe release has no build for Intel Macs. Use a Windows, Linux or Apple-chip machine.

`get_models` downloads the model file into `analysis/models/` (not committed).

## The droop test

This answers one question: does an acted one-sided droop move a reading clearly outside the spread of normal smiles?

1. Run `python -m analysis.live_face --person yourname`. Add `--mirrored` if you know your camera mirrors.
2. Sit at arm's length, in good light, face straight at the camera.
3. **Wink check first.** Wink your LEFT eye. The window should say `blink: HER LEFT`, and the `HER L` labels should sit on your left (the screen's right: the picture is not a mirror). If it says RIGHT, restart with or without `--mirrored`. If the window says `NOT usable: head turned/tipped too far` while you face the camera straight, the head-angle maths is wrong: restart with `--no-angle-check` and tell the team.
4. Sit relaxed for a few seconds and look at `jitter`. That is how much the number wobbles by itself.
5. Press `n`, then `SPACE`, and do a normal smile when it says SMILE. Do this 10 times.
6. Press `l`, then do 10 acted droops on your left (the left corner stays down while the right smiles). Press `r` and do 10 on your right.
7. Press `q`. The summary prints and is saved next to the CSV in `analysis/runs/`.

**The window ("Droop test").** Left: the camera picture (not a mirror), with the four measured points. A cyan line runs through the two eye points and a second line through the two mouth points (green when the frame is usable, red when not). With a level face the two lines are parallel; a droop tilts the mouth line. During a trial the big `RELAX` / `SMILE` countdown shows at the bottom. Right: a side panel in plain words: face OK or why not, mouth level, smile, smile balance, head, steadiness, wink check, then the test label, trial counts, the last trial and the keys. Press `m` to show or hide a faint dot for every one of the 478 face points. The **SO FAR** block at the bottom of the panel is a live tally: per reading, the range of normal trials and how many acted left / right trials lie outside it (`OK` = all outside, `X` = some overlap), so you see during the test whether the droops separate.

Add `--record-trials` to also save each trial as a video in `analysis/recordings/` (not committed) for testing the clip tool. Never commit recordings of people.

**Reading the summary.** For each of the three readings you get a table per label and a verdict for each acted label. `separates` on the correct side means every acted trial lay beyond every normal trial: a pass. `overlaps` means some did not; the count says how many. `best metric` is the reading with the widest worst-case gap compared with the spread of normal smiles.

## The clip tool

```
python -m analysis.measure_clip CLIP.mp4 [--mirrored] [--primary corner_height] \
    [--person-file analysis/data/NAME.json] [--label normal|acted|live] [--save]
```

It prints the tool result as JSON. With `--save` and a person file it adds the session to that person's record. Sessions labelled `acted` are stored but never enter the baseline. Unusable clips are never saved.

## Hand test

**Why one hand.** The older person holds the phone in one hand during the call, so "raise both arms" is impossible. The avatar says: "Hold your free hand up next to your face, palm towards me, and keep it still." About 5 seconds. Then: "Now swap the phone to the other hand and do the same." A weak arm sinks, the palm turns inward, or she cannot lift it as high as usual. Each hand is compared with HER OWN usual for that hand, never with the other hand.

**What is measured, in plain words.** Everything is measured against her face (the eye line and the distance between the eyes), never against the picture, so moving the phone does not count. Four candidate readings, all oriented so that a larger value is more of a problem:

- `hand_sink`: how far the palm centre sank between the first and the last second of the hold, in eye distances.
- `hand_height`: how far below the eye line the palm was held at the start (a weak arm may not lift as high as usual).
- `palm_turn`: how much narrower the palm looked at the end than at the start (the palm turning inward).
- `hand_shake`: how much the height wobbles around a straight line.

If the hand is seen at the start and then drops out of view while the face is still there, it counts as a big sink (`config.DROP_SINK_VALUE`) with the note `hand dropped out of view`. That is how an acted weak arm will probably look on a handheld phone.

**Run it.**

```
python -m analysis.get_models
python -m analysis.live_hand --person NAME
```

`get_models` now downloads two files (face and hand); one failing does not stop the other. The hand model URL was written from memory and is unverified. If the download fails, it says so.

**Trial plan, per hand:** 10 normal trials, then 10 acted weak trials (let the hand sink slowly, drop it, or turn the palm in). Keys: `1` her left hand, `2` her right hand, `n` normal, `w` acted weak, `SPACE` start (1 s to get ready, then a 5 s hold), `m` hand points, `q` quit and print the summary. Add `--hold SECONDS` to change the hold.

**The panel** (plain words, top to bottom): face OK or why not; which hand is seen (the library's own label in grey, for reference only); hand height against the eye line; sink so far during a trial; palm turn; the note that it is measured against her face; the test block (which hand, normal or weak, counts per hand and mode, the last trial, the keys); and SO FAR, the live tally per hand and per reading: the range of normal trials and how many acted weak trials lie above it (`OK` = all above, `X` = some overlap). On the picture: the eye line (cyan), the hand points, a grey tick at the palm's start height and a green tick at its current height, so a sink shows as the gap between the two. On quit, the trials go to a CSV and the summary to a text file in `analysis/runs/`, with one verdict line per hand.

**`--mirrored`.** If the panel says `wrong hand` when you raise the hand you were asked to, the left/right is swapped: restart with `--mirrored` (or without it, if you had it on).

**Clips.**

```
python -m analysis.run_task --task hand_left CLIP.mp4 [--person NAME] [--attempt 1] [--mirrored] \
    [--save --label normal|acted|live] [--hold START_S END_S]
```

Tasks: `smile`, `hand_left`, `hand_right`. The result has the usual shape plus `status`: `as_usual`, `different_from_usual`, `unusable` or `learning`. For hands, `reading.sign` is `arm`, comparisons are one-sided (only above the usual range counts), and `reading.side` is the hand's side (her left or right). Each hand has its own baseline (`hand_left.hand_sink`, ...). `acted` never enters a baseline.

**Known limits.** The 5 s hold is short by the owner's choice; real drift tests use 10 s, so a slow drift may not show (`HAND_HOLD_S` in `config.py`). Every number is a first guess. Written, not run: no camera was available.

## The three readings

- `corner_height`: her left mouth corner's distance below the eye line minus her right's, at the smile peak, in units of eye distance.
- `corner_lift`: how far each corner rose from the relaxed face to the peak; right minus left.
- `smile_score`: MediaPipe's right smile score minus its left, at the peak.

## Conventions

- Left and right always mean **her** left and **her** right (the person in the video).
- A **positive** value means her **left** side is lower or weaker; negative means her right; zero is level.
- If the camera image is mirrored, use `--mirrored`: the frame is flipped back before detection.

## Not known yet

- Whether an acted droop separates from normal smiles (the droop test answers this).
- How fast it runs on the laptop and on the server.
- How it behaves on older faces, with glasses, or in dim light.
- The head-angle convention (the window shows yaw, pitch, roll; check by turning your head).
- Whether the landmark indices 33/263 (eyes) and 61/291 (mouth) are the right points (the wink check and the labels on screen show it).

## Tests

```
python -m pytest analysis/tests -q
```

Not run yet. The tests use made-up points: no camera, no mediapipe, no video.
