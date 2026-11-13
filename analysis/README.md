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
- `corner_height_3d`: the same as `corner_height`, but measured in the face's own 3D frame (axes built from the eye corners and a forehead point, which do not move when smiling), so turning or tipping the head, or holding the phone low or to the side, should not count. Written, **not verified**: the forehead point (index 10) was written from memory.

## What the numbers mean

- **MOUTH LEVEL:** 0 = both mouth corners equally high. Off-centre = one corner lower. Everyone is a little uneven; we compare with your own usual.
- **MOUTH LEVEL (angle-corrected):** measured in the face's own 3D frame, so head turn should not count (unverified).
- **SMILE:** how strongly you are smiling right now (0 to 1).
- **SMILE BALANCE:** Google's own guess of which side smiles less. A crooked camera angle also moves it.
- **HEAD:** which way your head is turned; big turns make the other numbers less reliable.
- **STEADINESS:** how much the mouth-level number wobbles while you hold still; a droop must be much bigger than this.

**"I saw" after each trial.** The result card says what the tool measured, e.g. `I saw: your LEFT corner lower by 0.08`, `I saw: level`, `I saw: hand steady`, `I saw: hand sank 0.15, palm turned in 0.18`. If you were asked for a LEFT droop and the tool saw level or the other side, a yellow line says so and the trial is saved as you did it (`seen` and `matches_asked` columns in the CSV). Such trials are left out of the separation verdict and counted on their own line.

## The angle test

Does holding the phone crooked move a reading as much as a droop does? This is the main open question of the product.

```
python -m analysis.check --person NAME --only angle
```

Six parts, 3 trials each (`GUIDE_ANGLE_PER_POSE`; `--per-pose N`): normal smiles looking straight, chin up (phone held low), head turned to your LEFT, head turned to your RIGHT, then an acted droop on each side looking straight. The head-angle quality check is off during this test, and the panel shows each trial's median yaw and pitch. For each reading the summary gives `angle shift` (the largest move of a normal smile at a crooked pose), `droop size` (the smaller acted droop), their ratio, and a verdict: under 0.5 `angle OK`, 0.5 to 1.0 `angle RISKY`, above 1.0 `angle BREAKS IT`. The last line names the reading that a crooked phone moves least. Written, **not run**; every threshold is a first guess.

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

## v5: call session core (streamed tasks)

The first code for the merged Python backend (`backend/ARCHITECTURE_PLAN.md` sections 3-7). Pure Python + asyncio, imported by the backend, not a service. **Status: written, not run. The tests are written and have not been run; nothing here has been executed.**

**`analysis/session/`** (no mediapipe, no cv2)
- `limits.py`: every tunable from the plan in one place (1 s observations, 2 s framing, 2 s first sample and gap, 5 s no acknowledgment, 3 s analyzer stalled, 1 s cancel acknowledgment, window + 20 s finalize, 10 s recovery wait, 3 attempts per task, 180 s call).
- `models.py`: dataclasses for `TaskObservation`, `TaskInterrupt`, `TaskResult`, `ResourceEvent`, `SessionResult` and their parts. `to_dict()` validates and returns schema-exact dicts; `from_dict()` parses them.
- `clock.py`: `RealClock` and `FakeClock`. Every watchdog and wait goes through the clock.
- `attempt.py`: the attempt state machine (preparing, capturing, finalizing, completed; or stopping, then interrupted, failed or cancelled). Finalizes once; late samples and observations are refused; duplicates are ignored; conflicting reuse is rejected.
- `rules.py`: pure functions: interrupt decision, attempt budget, confirmation, session status, baseline eligibility.
- `call_session.py`: `CallSession`, the coordinator: `run_task`, `handle_resource_event`, `handle_capture_started`, `handle_cancel_ack`, `hang_up`, `recover_resource`, `finish`, plus an in-memory decision log (`session.log`).
- `adapters.py`: Protocols for what Agent 2 provides (`say`, `start_capture`, `cancel_capture`, `samples`, `publish`, `send_progress`) and `FakeAdapter`.
- `demo_call.py`: `python -m analysis.session.demo_call` runs the plan's section 7 example against the fakes (no camera, no Claude, no network). Written, not run.

**`analysis/tasks/`** (new files)
- `stream_base.py`: the module interface `definition()` and `async run(context, samples, emit_observation)`, plus helpers.
- `stream_smile.py`: the real streamed smile task. 5 s of JPEG frames, one observation about every second and on a quality change, final reading `corner_height` from the same `clip_reading` arithmetic as `tasks/smile.py`, compared with the frozen baseline. The frame reader is injected; the production reader wraps `FacePointsExtractor` and `frame_reading`.
- `jpeg.py`: the one cv2 call of the new code (JPEG bytes to image).
- `stream_arms.py`, `stream_sentence.py`: **stubs.** `run()` returns a schema-valid `failed` result with the reason `not_implemented_in_v5`. No both-arms or speech metric is guessed.
- `stream_registry.py`: `STREAM_TASKS`.

**Not built in v5:** the Claude tool loop and the fixed-script fallback (built in v6, see below). In v6 the arms task is also real (it was a stub in v5).

**Contract.** `contract/` holds draft 0.2.0 unchanged. Two deltas are proposed, not applied (see `contract/README.md`): the arms task as one free hand at a time while the script lines still say both arms, and the arms and sentence modules being stubs.

**Tests (written, not run):** `test_session_rules.py`, `test_attempt.py`, `test_call_session.py`, `test_stream_smile.py`, `test_session_contract.py`, with `session_helpers.py`. They use a fake clock, a fake adapter and made-up numbers; the JSON Schema checks skip unless `jsonschema`, `referencing` and `PyYAML` are installed.

## v6: Claude runs the call

**Status: written, not run. The tests are written and have not been run. The real Claude path (`claude_client.ClaudeClient`) has never been executed; nothing has talked to the Claude API.** The v5 "not built yet" call driver now exists, plus the real one-hand arms task.

**Files (`analysis/session/`)**
- `agent_tools.py`: `ToolHandlers`, the ONE set of steps both drivers use, so every guard applies to both. Tools: `say` (only `greet` first and once, and `setup_phone` once), `ask` (at most 2 per question; plays the line, then `adapter.listen`), `record_answer` (`q_medicine` must be yes/no/unclear, others at most 200 characters), `run_task` (plays `setup_phone` itself before the first video task), `recover_resource` (once per interruption), `finish_call` (refused while a required check still has attempts, an available resource and no settled result; allowed in the last 30 s, or with `reason="she_asked_to_stop"`). A refused step returns `{"error": "not allowed: ...", "allowed": [...]}` and changes nothing. After hang-up every tool returns `{"error": "call ended"}` and nothing reaches the client.
- `agent.py`: `ClaudeAgent`, the loop (proper Messages history, one action per turn, stale-answer handling, fallback switch). Fills `prompts/agent_system.md` (owner-written, not edited) with `{name}` and `{lines}` by `str.replace`.
- `claude_client.py`: the only module that talks to Claude (`anthropic` imported lazily), and `FakeClaudeClient` for tests and the demo.
- `fallback.py`: `FixedOrderDriver` and the pure `next_step(state)`.
- `run_call.py`: `run_call(session, adapter, mode, client)` returns the SessionResult dict.
- `adapters.py` gained `listen(question_id, timeout_s)` (Agent 2's speech-to-text; `None` = nothing heard). `limits.py` gained the v6 values (`ARMS_PHASES`, `LISTEN_TIMEOUT_S` 8 s, `CLAUDE_TIMEOUT_S` 6 s, `CLAUDE_MAX_TURNS` 30, ...).

**Choosing the driver.** `await run_call(session, adapter, "fixed")` is the default and needs no model. `"claude"` uses the tool loop; without a usable client it logs why (`agent_fallback`) and the fixed order runs the call. With `mode=None` the switch environment variable decides: `UW_CLAUDE=on` means claude, anything else fixed (this is the only place `UW_CLAUDE` is read). Other variables: `ANTHROPIC_API_KEY` (the only way the key is given; never logged), `UW_CLAUDE_MODEL` (default `claude-haiku-4-5`). `anthropic` is an optional package (see `requirements.txt`). Demo: `python -m analysis.session.demo_call --driver fixed|fake-claude|claude` (`claude` spends API credit and refuses to start without `--yes-real-claude`).

**What Claude sees.** The system prompt with her name and the fixed lines, the six tools, and after each step plain facts: for `run_task` the outcome, the quality status and reason slugs, the interruption code and whether it is recoverable, attempts left, `needs_another_reading`, `skip`, and resource states. **What Claude cannot see:** any measured value, her usual range, whether a reading was outside, a side, whether a change was confirmed, or anything about the family message. Confirmation and the message stay in `CallSession` code, so a confirmed change is published even if Claude fails right after. A free-text answer is never written to the log.

**The fallback.** For the rest of the call (never switching back) the fixed order takes over when: the client could not be built; a request fails or takes more than 6 s; a response has no tool call or is malformed; a tool call fails its schema (unknown tool counts) twice in a row; 3 refused steps in a row; more than 30 requests. A decision made before a resource change, interruption or hang-up (`CallSession.event_version`) is stale: it is answered with `{"stale": true, "state": ...}`, not executed, and Claude is asked again. The fixed order works from whatever state the call is in: greet, the three questions (ask once, re-ask once, record by keyword or the first 200 characters), smile, arms, sentence (each repeated while another reading is needed; after a recoverable interruption: recover once, repeat if restored, else skip), finish. The sentence check has no metric, so it is skipped without playing its instruction.

**The arms task (`tasks/stream_arms.py`, real now).** The person holds the phone in one hand. One 12 s window, two halves (`limits.ARMS_PHASES`): 0-1 s settle, 1-5 s hold her LEFT hand, 5-7 s swap (frames ignored), 7-8 s settle, 8-12 s hold her RIGHT hand. The task definition carries `cues: [{"at_ms": 5000, "line_id": "swap_hands"}]`; `CallSession` plays any task's cues at that offset after capture started, without blocking samples or watchdogs. Per hold frame: `HandFrameReader` plus `hand_metric.hand_frame_reading`, the final `hand_clip_reading` per hand gives `hand_sink`, compared with that hand's own frozen baseline (outside only above her usual high, as in `tasks/hand.py`). One reading is reported: the hand further outside, else the hand closest to its usual high (side only when outside). A hand with too few usable frames makes the whole attempt unusable with the reason `left_hand_not_seen` or `right_hand_not_seen`. Baseline for arms: `{"snapshot_id", "sessions", "needed", "ready", "hand_left": ..., "hand_right": ...}`, ready only when both hands are. `baseline_update()` stores `arms.hand_left.hand_sink` and `arms.hand_right.hand_sink`. Two outside readings confirm only on the same direction AND side: left hand outside then right hand outside is not confirmed.

**Tests (written, not run):** `test_stream_arms.py`, `test_agent_tools.py`, `test_fallback.py`, `test_agent.py` with `agent_helpers.py`, plus additive cases in `test_session_rules.py` and `test_call_session.py`. Fake clock, no network, no mediapipe. Offline command for the owner (not run): `python3 -m pytest analysis/tests -q`.

## v7: PC version (two programs)

Two separate programs joined by a websocket (`analysis/pc/protocol.md`, protocol version 1):

- **Client** (`python -m analysis.pc.client`): camera, measuring and a window. It shows the camera picture, the line the avatar says in big text, what it sees right now ("face not visible", "Raise your LEFT hand", "going fine"), a timer, and a result card at the end. It measures each frame locally with the existing MediaPipe readers and sends the readings to the server, plus a short status report every second. It decides nothing about the call.
- **Server** (`python -m analysis.pc.server`): all logic. The existing `CallSession` (watchdogs, attempts, confirmation rule, baselines, family message) and the call driver (Claude or the fixed order). It talks to Claude and never opens the camera. It runs the same stream task modules on the readings the client sends.
- Later a phone can take the client's place; the server only sees the link messages.

**Setup (Windows, macOS, Linux).** Python 3.10+. `python3 -m venv .venv`, activate it (`source .venv/bin/activate`, on Windows `.venv\Scripts\activate`), `pip install -r analysis/requirements.txt`, then `python -m analysis.get_models` once (downloads the MediaPipe model files). Optional: `pip install pyttsx3` (spoken lines), `pip install claude-agent-sdk` (subscription mode), `pip install anthropic` (API mode).

**Run.** Terminal 1: `python -m analysis.pc.server --person feodor --name Feodor`. Terminal 2: `python -m analysis.pc.client --server ws://127.0.0.1:8765 --person feodor` (add `--mirrored` for a mirrored front camera, `--camera 1` for another camera, `--auto-start`, `--tts system`). In the client window, SPACE starts a call, q hangs up (and quits when no call runs). When the avatar asks a question, type the answer in the window, Enter sends, Esc = no answer (until the speech build).

**Server flags.** `--claude off|subscription|api` (default `off` = the fixed-order driver), `--safety-floor on|off`, `--label live|normal|acted`, `--host`, `--port`, `--person`, `--name`.

- `--claude api`: `pip install anthropic`, `ANTHROPIC_API_KEY` set in the environment.
- `--claude subscription`: `pip install claude-agent-sdk` and the `claude` CLI installed and logged in to your account. The server removes `ANTHROPIC_API_KEY` from its own environment first (it prints "subscription mode: API key variable ignored") so the CLI cannot bill a key. No Claude Code tool can run (tools off, all built-in tools denied, one turn, no settings, no MCP, a temporary working folder). Model from `UW_CLAUDE_MODEL`, default `haiku`; one request may take 15 s.
- If Claude cannot be set up or fails, the fixed order runs the whole call (or the rest of it) and the reason is printed.
- **`--safety-floor`.** ON (the default) means two same-side outside readings are published to the family mid-call whatever Claude does. OFF is for experiments only: the confirmed sign stays in the summary but nothing is published mid-call and the status is not `change_noticed` (the log says `floor_off_suppressed`). `UW_SAFETY_FLOOR=off` also turns it off. **Keep it ON for the demo and any real use.**

**What Claude gets now.** `run_task` returns the facts plus `reading` (`value`, `usual_low`, `usual_high`, `outside_usual`, `distance`, `side`, `unit`; null when nothing usable was measured) and `baseline_ready`. `distance` is how far outside her usual in multiples of half her usual range (0 inside or while learning). New tool `alert_family(reason, note)` (`said_unwell`, `asked_for_help`, `confused_answers`, `reading_far_outside`, `other`; note cut at 200 characters; at most 2 per call; after the greeting) publishes a mid-call message at once and sets the status `concern_raised` (order: `change_noticed` > `concern_raised` > `incomplete` > `learning` > `as_usual`). The fixed-order driver never calls it. The required tasks are now smile and arms (`limits.REQUIRED_TASKS`); the sentence check has no measuring yet, so a full call with smile and arms within her usual ends `as_usual` (or `learning`).

**Where things go.** Each finished call: `analysis/runs/calls/<call_id>.json` (summary, family text, session log, timings) and a line in `analysis/runs/calls/family_messages.log`. Baseline values are appended to `analysis/data/<person>_calls.json` (a call labelled `acted`, or one with a confirmed change, never adds values). Both folders are git-ignored.

**What is real and what is a stand-in.** Real: the link, the session logic, the measuring code, the baseline store. Stand-ins: family delivery prints a framed block in the server terminal (the client shows the final family text on its result card); answers are typed until the speech build; the microphone is reported as unavailable.

**Offline demo (no camera, no network, no Claude):** `python -m analysis.pc.demo_link [--driver fake-claude]`. Server logic and a scripted client in one process over an in-memory link, a fake clock, made-up readings.

**Status.** The tests (400) and both demos ran offline with fake clients. **Never run: the camera client (`client.py`, `client_window.py`, `frame_source.WebcamSource`, `client_measure` with real MediaPipe), the websocket server path outside one loopback test, the real Claude API path, the subscription path with the real `claude_agent_sdk`, and pyttsx3.** The 1 s camera-blocked thresholds and the wording of the hints are first guesses.

## v8: speech

The PC version can now talk and listen. Her answers are spoken (not typed), the sentence check is real, and the avatar's lines can use an ElevenLabs voice. Protocol version 2 (`analysis/pc/protocol.md`); a version 1 client still works.

**Setup.** `pip install sounddevice` (microphone and line playback in the client; without it the client says so and answers stay typed). Put the ElevenLabs key in the **SERVER terminal only**: `export ELEVENLABS_API_KEY=...` (Windows: `set ELEVENLABS_API_KEY=...`). The key never goes in the client terminal, the repo, a `.env` that is committed, or over the link. Optional: `UW_TTS_VOICE_ID` (needed for `--tts elevenlabs`; the built-in default is an unverified placeholder and the server refuses to use it), `UW_TTS_MODEL` (default `eleven_multilingual_v2`), `UW_STT_LANGUAGE` (default `en`, read by the client).

**How it works.** The client records when a `listen` arrives (up to 8 s, it stops 1.5 s after she stops talking; nobody speaks within 5 s = no answer and **no request to ElevenLabs**). It then asks the server for a **single-use token**, calls ElevenLabs speech-to-text (`scribe_v2`, word timestamps) itself with that token, and sends the server the **text and word timings, never audio**. The server mints at most one token at a time and 10 per call. Speech-to-text fails or the server has no key: the answer is `null` with a short `stt_error`, the call goes on, and without a key the client falls back to typing (the server prints it) and the sentence check is skipped.

**The sentence check.** The avatar says "Please repeat this sentence after me: Today I am sitting by the window." The client records up to 12 s (stops 1.5 s after she stops), transcribes, and sends a `speech_result`. The code turns the word timings into numbers (`analysis/speech/sentence_metric.py`, pure): `duration_s` (first word start to last word end), `longest_pause_s`, `words_per_min`, `word_error_rate` (word edit distance to the expected sentence, lowercased, punctuation removed), `missing_words`, `extra_words`, `changed_words`, `heard`.

- **What the rule uses.** One number: `duration_s` (metric `speech_duration_v1`, unit `s`, sign `speech`, side always null), compared with her own usual (baseline key `sentence.duration_s`, at least +/- 0.5 s wide, ready after 5 calls). Outside = **longer** than her usual high only; faster is never outside. Two outside readings confirm a change and the safety floor publishes it, as for the other checks. Quality is `unusable` with `speech_to_text_failed` or `nothing_heard`.
- **What Claude sees.** In the `run_task` result for the sentence: the usual `reading` and, new, `speech`: `heard`, `word_error_rate`, `missing_words`, `changed_words`, `extra_words`, `longest_pause_s`, `words_per_min`. Claude may judge them (and call `alert_family`); the safety floor does not use them. The server terminal prints the same numbers for each attempt.
- **Required tasks.** `smile`, `arms` and `sentence` for a call when the client reports speech AND the server has a key; otherwise `smile` and `arms`. Decided per call and logged (`required_tasks`).

**Line voice: `--tts off|elevenlabs` (server).** With `elevenlabs`, at start-up the server speaks every line the call can play (script lines with the name filled, the PC line texts, `swap_hands`) once into `analysis/runs/tts_cache/<voice_id>/<line_id>.pcm` (git-ignored; reused when the text is unchanged, so a restart costs nothing) and attaches the audio to `say`. Any failure: logged, those lines go out as text. The client plays the audio and sends `said` when it ends.

**Client flags.** `--answers speech|typed` (default: speech when `sounddevice` and a microphone exist). The window shows "Listening..." with a live input level bar while it records, then "I heard: ..." with the transcript.

**Run.**
```
# terminal 1 (the key lives here only)
export ELEVENLABS_API_KEY=...          # and, for the line voice, export UW_TTS_VOICE_ID=...
python -m analysis.pc.server --person feodor --name Feodor --tts off           # or: --tts elevenlabs
# terminal 2
python -m analysis.pc.client --server ws://127.0.0.1:8765 --person feodor --answers speech
```
Offline demo with a fake ElevenLabs and a fake microphone: `python -m analysis.pc.demo_link --speech [--driver fake-claude]`.

**Costs.** Per ElevenLabs plan (speech-to-text per audio second, text-to-speech per character, once per line thanks to the cache). **Not measured here.**

**Status. No request to ElevenLabs was ever made in the build session; the first real run is the owner's.** Never run: the microphone (`mic.py` with `sounddevice`), line playback, ElevenLabs token minting, speech-to-text with the token in the query (that this authentication works on `/v1/speech-to-text` is taken from the API reference only) and text-to-speech, the websocket path with audio-sized messages (a 12 s line is about 0.5 MB of base64; lines longer than that go out as text). The voice-activity thresholds (`mic.py`) and the 0.5 s minimum width of her usual sentence duration are first guesses. The voice id default is an unverified placeholder.
