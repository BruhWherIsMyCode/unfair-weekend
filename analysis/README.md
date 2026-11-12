# Face tool (first version)

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

Add `--record-trials` to also save each trial as a video in `analysis/recordings/` (not committed) for testing the clip tool. Never commit recordings of people.

**Reading the summary.** For each of the three readings you get a table per label and a verdict for each acted label. `separates` on the correct side means every acted trial lay beyond every normal trial: a pass. `overlaps` means some did not; the count says how many. `best metric` is the reading with the widest worst-case gap compared with the spread of normal smiles.

## The clip tool

```
python -m analysis.measure_clip CLIP.mp4 [--mirrored] [--primary corner_height] \
    [--person-file analysis/data/NAME.json] [--label normal|acted|live] [--save]
```

It prints the tool result as JSON. With `--save` and a person file it adds the session to that person's record. Sessions labelled `acted` are stored but never enter the baseline. Unusable clips are never saved.

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
