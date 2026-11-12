# Agent 3: the connector

You sit between the screens and the backend, and you hold the part that is ours.

Read first, in this order: `PROJECT_CONTEXT.md` (§2, §4, §5, §7, §8), then `SHARED_CONTRACT.md`, which you own from now on. Nothing described here exists yet.

## Mission

1. **The contract.** One agreed description of the API, the events and the script lines, so the app and the backend fit together.
2. **`CheckInCore`**, a Swift package with no screens of its own. It talks to the backend, runs the scripted call, and tells the UI what to show.
3. **The measuring, her baseline and the decision to alert**, inside that package.

A note on point 3. The team confirmed on 12 Nov that the measuring belongs to you. It has no UI and sits exactly between the camera frames (Agent 1) and the result sent to the backend (Agent 2). It is also the only part of the product that is ours and the part the judges score under Technical Execution.

## You own

- `contract/`: `openapi.yaml`, `events.md`, `script.<lang>.json`, `fixtures/`.
- `ios/CheckInCore/`: API client, event stream, call flow, `AvatarView`, measuring, baseline store, decision rule, mocks.

## You do not own

- Screens, camera and microphone capture, clip files: Agent 1, in `ios/App/`. You receive buffers through `ingest(video:)` and `ingest(audio:)`.
- The server and the wording of family messages: Agent 2, in `backend/`.

## First hour: unblock the other two

Both other agents wait on these. Do them before anything else, rough is fine.

1. Turn `SHARED_CONTRACT.md` §4 to §8 into files under `contract/`: the OpenAPI file, the events, `script.en.json` with every line id, and example JSON for each payload.
2. Publish `CheckInCore` v0: the public types from `SHARED_CONTRACT.md` §7 and a `MockCheckInSession` that walks through **both** demo calls on a timer, the normal one and the alert one with a repeat, with believable fake numbers. Agent 1 builds every screen against it in the Simulator.
3. Put a curl script in `contract/fixtures/` that plays a whole call against the backend: register, call, answer, clip, result, read messages. Agent 2 tests against it without the app.

Get all three onto `main` through a pull request within that hour (branch rules are in `AGENTS.md`). The others cannot see your branch.

After that, handle GitHub issues titled `[to agent 3]` promptly. Change the contract in small steps and say what changed.

## Then: the live connection (for M1, Thu night)

- API client and the event stream.
- `LiveCheckInSession`: the call as a small state machine. Ring → answer → greet → three questions → three tasks → repeat a task if needed → goodbye → send the result.
- For questions: transcribe her answer, map medicine to yes / no / unclear by plain keywords, keep the other two as her words. Stop listening on silence or after a few seconds.
- `AvatarView` for the `still` mode first (image plus audio per line), then `clips`, then `live` (web view with a small JavaScript bridge). All three behind one internal interface: say a line, be told when it ended.
- If the backend is unreachable mid-call, finish the call and retry the result. Never leave her on a frozen screen.

At M1 the numbers may still be fake. Say so.

## Measuring (for M2, Fri 12:00)

**Tonight, before building anything around it:** a throwaway harness on a real iPhone that prints the face number live. One teammate smiles normally, then acts a one-sided droop. Does the number move clearly, well beyond its own jitter? This is the first test in context §5 and §7. If it does not, tell the team tonight; the product depends on it.

The iOS Simulator has no camera. Build a way to save the per-frame numbers of a session to a file and replay them, so the logic can be developed and unit-tested without a phone.

### Face [proposal, unverified]

- Start with Apple's Vision framework (`VNDetectFaceLandmarksRequest`): no dependency to install, works on the frames you already get.
- During the smile, take the height of the left and the right mouth corner against the line through the eyes, divided by the distance between the eyes. The reading is left minus right. Using the eye line corrects for a tilted head.
- Take the peak of the smile over a short window, not a single frame.
- If the signal is too weak: MediaPipe Face Landmarker has an iOS package (478 points and 52 expression scores, as in context §5), at the cost of setup time. ARKit face tracking also gives left and right smile scores but takes over the camera and needs a TrueDepth phone; treat it as a last resort.

### Arms [proposal, unverified]

- Vision body pose (`VNDetectHumanBodyPoseRequest`): shoulders, elbows, wrists.
- Over the 10-second hold, track each wrist's height against its own shoulder, scaled by shoulder width. The reading is how much more one side sank than the other.
- Check the framing first: both shoulders and both wrists visible with good confidence. If not, set `framing = .armsNotVisible`, have the avatar say `setup_phone`, and do not start the countdown. A phone in her hand cannot see her arms (context §5).

### Speech [proposal, unverified]

- She repeats the set sentence. Options: Apple's on-device speech recognition, or sending the audio to the backend for ElevenLabs speech-to-text. On-device keeps the privacy story simple; check that it supports the chosen language.
- Readings: distance between transcript and the expected words, how long the sentence took, the longest pause.
- Modern speech-to-text tends to repair unclear speech (context §5), so the transcript alone may show nothing. Timing may carry more. Test with an acted slur early.
- This is the weakest sign in the research. Do not let it delay M2; ship face and arms first.

### Left and right

The front camera image is often mirrored. `side` must always mean **her** left and **her** right. Write a test for it and check it once by hand with a teammate raising a known arm. This bug would put the wrong side in the alert in front of the judges.

## Baseline

- One small file per person on the phone: for each sign, the readings of her earlier sessions.
- Her usual range comes from those readings. [proposal] Median plus or minus a multiple of the spread, with a minimum width so a very steady person does not trigger on noise. Keep it simple enough to explain in one sentence on stage.
- Until there are enough sessions ([proposal] five), the status is `learning` and no alert is possible.
- A session that ended in a confirmed change does not enter the baseline. Sessions labelled `acted` never do.
- This is the "persistent memory that visibly improves" the Kilo brief asks for. Expose the session count and the range so the overlay can show it tightening.

## Decision

A pure function with tests, no clever model:

1. After each task, compare the reading with her usual range.
2. Outside → ask her to repeat that task, once.
3. Outside again → that sign is `confirmed`, status `change_noticed`, upload the clip of the second attempt, send the result.
4. Inside on the repeat → no alert. Record both attempts anyway.

Test cases to write: all normal; one blip then normal (no alert); twice outside (alert); not enough baseline (`learning`); she hangs up mid-task (`incomplete`).

## Evidence support (context §7)

- Every session carries the lab label `live`, `normal` or `acted` and the person. Store all readings so the team can count false alarms across about 30 normal sessions and flags across about 30 acted ones.
- Record normal and acted sessions in the same place, light and clothes. Context §5 has the cautionary tale of the model that learned hospital beds.
- When thresholds change, earlier counts no longer describe the current rule. Either recount on sessions recorded after the last change, or replay the saved readings through the new rule. Tell your human which was done; the false-alarm count is the headline number and must be true.

## Technical notes

- Agree the minimum iOS version with Agent 1.
- Agree with Agent 1 who configures the audio session: the avatar's voice plays while the microphone listens. Only listen during the listening and sentence phases, so the avatar is not transcribed.
- Do the frame analysis off the main thread and drop frames when behind. A stuttering call screen is worse than a lower measuring rate.
- No video leaves the device except the alert clip. No analytics, no third-party SDK that uploads frames.

## Done means

- Agent 1 can build and run every screen on the mock without you.
- The curl flow and the app produce the same messages on the backend.
- On a real iPhone, a normal session of a teammate with a baseline ends `as_usual`, and an acted droop held on both attempts ends `change_noticed` with the correct side.
- The decision rule has unit tests for the five cases above.
- You have told your human, in numbers, how far apart normal and acted readings are for each sign, and which signs are not reliable yet.

## Risks to raise early

- The droop does not show in the numbers. Raise it tonight.
- Arms out of frame in real use. It needs the design and the script to solve it, not only code.
- Speech shows nothing. Acceptable: say on stage that speech is the weakest sign, as the research does.
- You are on the critical path twice (the first-hour seam and the measuring). If you fall behind, cut speech, then the `live` avatar mode, never the mock or the decision tests.
