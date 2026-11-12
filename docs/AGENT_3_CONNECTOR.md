# Agent 3: analysis and orchestrator

You build the core of the product: the measuring, her baseline, and the Claude orchestrator that runs the call and decides what happens next.

The file keeps its old name so existing links still work. It used to describe a "connector" (a Swift package on the phone). That plan is replaced by this one.

Read first, in this order: `PROJECT_CONTEXT.md` (§2, §4, §5, §7, §8), then `SHARED_CONTRACT.md` (start with its "Change, 12 Nov late evening" section). **Nothing described here exists yet. No code is written and no test has been run.**

Labels are the same as in the other files: **[proposal]** = accept, change or drop; **[unverified]** = from memory or reasoning, not checked. A third label is used here: **[decided]** = said by the team on 12 Nov late evening, as reported by Feodor. The other two teammates have not reviewed this text.

## 1. What changed, 12 Nov late evening (Feodor)

1. **[decided] The analysis runs on a server, not on the parent's phone.** The old brief put it in a Swift package (`CheckInCore`) using Apple's Vision framework. That is dropped for the measuring.
2. **[decided] Who does what.** The split is by person:
   - Agent 1's human: the iPhone apps.
   - Agent 2's human: the calling mechanism, the statuses and the backend that connects everything.
   - Agent 3's human (Feodor): the analysis and the orchestrator. This is the core.
3. **[decided, about 22:00, needs confirming] Our own apps on both sides**, one for the parent and one for the family, because it is easier to demo. This reverses the "parent uses WhatsApp" change at the top of `AGENT_1_IOS_APP.md`. A later file, `docs/tasks/agent1.md` (12 Nov, 23:15), describes the family app as a stage shell with everything mocked, and the parent's video call as "not our app, staged separately". Which app carries the parent's call, and so where the camera and microphone come from, must be confirmed before this part can be joined to anything. See §13, point 10.
4. **[decided] Claude is the orchestrator.** Measuring tools report their readings to Claude. Claude leads the conversation, drives the avatar, puts the readings together with what she says, and can send the alert during the call, before it ends.

Everything below that is not marked [decided] is a proposal written to make these four points buildable.

## 2. Mission

1. **Measuring tools** for the three signs (face, arms, speech), in Python on the server.
2. **Her baseline**: her usual range for each reading, from her own earlier calls.
3. **The orchestrator**: one Claude conversation per call, with a small set of tools.
4. **The floor and the log**: a fixed rule that Claude cannot switch off, and a record of every decision, so the false-alarm count is true.

## 3. You own

- `analysis/` [proposal for the folder name]: the measuring tools, the baseline store, the orchestrator, the floor rule, their tests.
- `contract/`, until the team gives it to someone else (see §13, point 7).

The table in `AGENTS.md` still lists the old folders for Agent 3 (`contract/`, `ios/CheckInCore/`). A human has to change that row before an agent may write to `analysis/`.

## You do not own

- `backend/` (Agent 2): the ring, the avatar and voice sessions, delivering the family message, deployment, all API keys for rented services.
- `ios/` (Agent 1): screens, camera and microphone capture.
- `ios/CheckInCore/` no longer has an owner. See §13, point 7.

## 4. The picture [proposal]

```
 Parent app (Agent 1)         Backend (Agent 2)              Analysis and orchestrator (Agent 3)
+----------------------+     +-------------------------+     +----------------------------------+
| camera, microphone   | --> | call, ring, statuses    | --> | Claude, one conversation per call |
| shows the task       |     | carries clips and audio |     |   tools: run a task, say a line,  |
| plays the avatar     | <-- | avatar and voice        | <-- |   read her history, send alert    |
+----------------------+     | family message delivery |     | measuring tools (MediaPipe)       |
                             +------------+------------+     | her baseline, floor rule, log     |
                                          |                  +----------------------------------+
                                          v
                               Family app (Agent 1)
```

- Claude is the head. The backend is its hands: it speaks a line through the avatar, shows a task on her phone, brings back her words and the task clip, and delivers the message.
- The measuring is plain code that Claude calls. Claude never looks at camera frames and never does arithmetic.
- [proposal] The analysis is a separate Python service that the backend talks to over HTTP on the same machine. The backend on branch `agent-2/backend` is Node; MediaPipe is used here from Python.

## 5. How one call runs [proposal]

1. The backend rings her phone. She answers. The backend tells the orchestrator that a call started, and for whom.
2. Claude greets her. The backend speaks the line through the avatar and reports when it has finished.
3. She answers a question. Her words come back as text. Claude records the answer and picks the next line.
4. Claude starts a task, for example the smile. Her app shows it and records a few seconds. The clip reaches the analysis. The face tool measures it and returns the reading.
5. Reading inside her usual: Claude moves on.
6. Reading outside her usual: Claude asks her to do it once more, with neutral words.
7. Outside again: the alert goes to the family at once. The avatar carries on calmly to the goodbye and tells her nothing.
8. The call ends. All readings are stored. Her baseline is updated, unless the session was acted or ended with a confirmed change.

## 6. The tools Claude gets [proposal]

Keep the list short. Every tool is our own code.

| Tool | What it does | What it returns |
|---|---|---|
| `say(line_id)` | The avatar speaks one line of the script | Done, when the line has been spoken |
| `run_task(task)` | Shows the task (`smile`, `arms`, `sentence`), records it, measures it | The reading as facts, see below |
| `record_answer(question, value)` | Stores her answer to a check-in question | Done |
| `get_history(days)` | Her readings and answers from earlier calls | A short summary |
| `send_alert(sign, side)` | Sends the family message now | Delivered or not |
| `end_call()` | Says goodbye and closes the call | Done |

A measuring tool returns facts, not raw points. Made-up numbers:

```json
{
  "task": "smile",
  "attempt": 1,
  "quality": "usable",
  "reading": {
    "sign": "face",
    "value": -0.11,
    "usual_low": -0.02,
    "usual_high": 0.03,
    "outside": true,
    "side": "left",
    "outside_by": 0.09
  },
  "baseline": {"sessions": 12, "needed": 5, "ready": true}
}
```

- `side` is always **her** left and **her** right.
- `quality` is `usable` or `unusable` with a reason (face not visible, arms out of frame, too dark). An unusable capture is never read as "as usual". Claude asks her to fix the framing and tries again.
- When `baseline.ready` is false the status is `learning` and no measured alert is possible.

## 7. Rules Claude cannot override [proposal, not yet confirmed by Feodor]

Claude does the thinking. These rules sit under it in plain code.

1. **The floor.** If the same sign is outside her usual on both attempts in one call, the alert is sent. Code does this, whatever Claude concludes.
2. **More, never less.** Claude may raise a concern on more than the floor, for example two small changes together with her saying she feels dizzy. It may never cancel an alert the floor has raised.
3. **No verdict to her.** Claude never tells her a result, a number or a worry. The goodbye is the same after every call.
4. **No arithmetic.** Inside or outside, which side and by how much are computed by the tools.
5. **Everything is logged.** Every tool call, every reading and every decision Claude makes is stored with the session. The false-alarm count in context §7 must be taken on the whole system with Claude in it, because Claude can alert on more than the floor.
6. **The call works without Claude.** If Claude is slow or unreachable, the call continues on the fixed script and the floor still applies.
7. **Claim rules.** Any text Claude writes that a person can read is checked against the banned phrases in context §2 before it is shown or sent. Every alert ends with the 112 line.

Why a floor at all: the same numbers must always give the same result, or a count of false alarms over thirty normal sessions means nothing. The Kilo track brief also says it is not looking for "Generic AI chatbots with health branding" (context §3); a fixed, tested rule under Claude is our answer to that. Both points are reasoning, not measured.

## 8. Measuring [proposal, untested]

**No model is trained.** There are no patient recordings to train on (context §5). The tools find points on her face and body, and our own arithmetic turns them into one number per task.

**Tonight, before building anything around it:** a throwaway Python script on a laptop webcam that prints the face number live. One teammate smiles normally, then acts a one-sided droop. Does the number move clearly, well beyond its own jitter? If it does not, tell the team tonight; the product depends on it.

### Face

- Google MediaPipe Face Landmarker, from Python. It returns 478 face points and 52 expression scores for each frame.
- During the smile, take the height of the left and the right mouth corner against the line through the eyes, divided by the distance between the eyes. The reading is left minus right. Using the eye line corrects for a tilted head.
- Take the peak of the smile over a short window, not a single frame.
- [proposal] Keep the left and right smile scores as a second reading and see which separates normal from acted better.

### Arms

- MediaPipe Pose Landmarker: 33 body points, each with a visibility score from 0 to 1.
- Over the 10-second hold, track each wrist's height against its own shoulder, scaled by shoulder width. The reading is how much more one side sank than the other.
- Check the framing first: both shoulders and both wrists visible. If not, return `unusable` and do not start the countdown. A phone in her hand cannot see her arms (context §5).

### Speech

- Claude cannot hear. Its models take text and images as input, not audio. So Claude cannot be handed the recording.
- ElevenLabs speech-to-text returns the words with a timestamp for each word. Readings: how long the set sentence took, the longest pause, and how far the words are from the expected sentence.
- Nothing in this plan hears a slur as a sound. [unverified] Speech-to-text may also repair unclear speech, so the words alone may show nothing. Test with an acted slur early.
- This is the weakest sign in the research. Ship face and arms first.

### Left and right

MediaPipe's documentation says its left and right expression scores "correspond to the subject's face, not the viewer", and that a mirrored image inverts them. Front cameras often mirror. Write a test, and check once by hand with a teammate raising a known arm. This bug would put the wrong side in the alert in front of the judges.

### Why Claude does not measure the frames

Anthropic's vision documentation says Claude's "coordinate and localization outputs are approximate", and that its outputs are not a substitute for professional medical advice. A point tracker gives the same number for the same frame every time. Claude's part is everything around the number.

## 9. How the video reaches the server [proposal, to agree with Agents 1 and 2]

| Option | How | Trade-off |
|---|---|---|
| **A. A clip per task** (build first) | The app records a few seconds for each task and uploads it | Simplest. The reading is back a few seconds after the task, in time for the repeat |
| B. Live frames | The app streams frames during the call | Live numbers on screen for the judges. More plumbing, and the call depends on the venue network |
| C. After the call | The whole call is analysed afterwards | Easiest, but loses the repeat in the same call, which is the false-alarm guard |

The measuring code works on frames either way, so B can be added after A works.

## 10. Baseline

- One small record per person on the server: for each reading, the values of her earlier sessions.
- Her usual range comes from those values. [proposal] Median plus or minus a multiple of the spread, with a minimum width so a very steady person does not trigger on noise. Simple enough to explain in one sentence on stage.
- Until there are enough sessions ([proposal] five), the status is `learning`.
- A session that ended in a confirmed change does not enter the baseline. Sessions labelled `acted` never do.
- This is the "persistent memory that visibly improves" the Kilo brief asks for. Expose the session count and the range so the demo can show it tightening.

## 11. Evidence support (context §7)

- Every session carries the label `live`, `normal` or `acted`, and the person. Store all readings and all of Claude's decisions.
- Record normal and acted sessions in the same place, light and clothes. Context §5 has the tale of the model that learned hospital beds.
- When a threshold or Claude's instructions change, earlier counts no longer describe the current system. Recount on sessions recorded after the change, or replay the saved readings through the new version. Tell your human which was done.

## 12. What leaves the phone now

The old rule "video frames never leave the phone" no longer holds. Say what is true, in the app, the README and the pitch:

- Short task clips go to our server to be measured. [proposal] They are deleted after measuring; only the alert clip is kept. Do not claim the deletion until it is built.
- [proposal, follows from §8] Her voice goes to ElevenLabs for speech-to-text.
- Her answers as text, and her readings, go to Anthropic's API for Claude.
- [proposal] Camera frames are not sent to Claude.

Context §5 still proposes the old privacy story ("video does not leave the device except for the alert clip"). Do not repeat it in the app, the video or the pitch.

## 13. Open decisions for the team

1. **The seam between backend and orchestrator.** Who calls whom, and whether it is one process or two.
2. **Agent 2's plan on branch `agent-2/backend`** (`backend/ARCHITECTURE_PLAN.md`, not on `main`). It puts the Claude loop inside the backend and keeps video analysis on the phone. That differs from points 1 and 2 of §1. Much of it can be reused as it stands: observations that carry quality and "unknown", commands that are acknowledged, one message per call, what to do when Claude times out.
3. **Clip transport**: A, B or C in §9, and the new endpoint it needs.
4. **How free the conversation is.** Context §4 lists free conversation as out of scope, and Agent 2's brief says "we do not want free chat". [proposal] Keep the avatar's lines as a fixed script and let Claude choose which line comes next. It keeps the delay short and the wording checked.
5. **Who writes the family message.** Agent 2's brief says the backend alone writes it, from templates with tests. [proposal] Keep the templates for the alert sentence and the 112 line; Claude may add one line that quotes her own words.
6. **A second kind of message.** Agent 2's rule accepts an alert only when a sign is `confirmed`. A concern Claude raises on more than the floor (§7, rule 2) needs its own message kind and its own wording, and a rule for what happens while her baseline is still `learning`.
7. **Owner of `contract/` and of what `CheckInCore` was**: the call flow inside the app, and the mock session Agent 1 builds screens against.
8. **Sending the alert during the call.** The contract sends the result only when the call ends.
9. **Which Claude model.** [unverified] The delay of one Claude turn inside a spoken call has not been measured. Anthropic's model page describes its Haiku tier as the one for latency-sensitive work.
10. **How the judges see this part.** `docs/tasks/agent1.md` has the family app fire its alert from a hidden tap, with a clip bundled in the app, and no backend. As written, the alert on the big screen is not produced by the analysis. Decide where the real measuring is shown: a real alert sent to the family app, live numbers on a second screen during the parent's call, or the recorded evidence in the video. Whatever is staged must be said to be staged (context §2).

## 14. Order of work [proposal]

1. **Tonight:** the droop test (§8). Then the three measuring functions running on saved clips of teammates, kept out of the repo (`AGENTS.md`: no recordings of people in the repo; saved numbers are fine).
2. **Tonight:** the floor rule as a pure function with tests: all normal; one blip then normal (no alert); twice outside (alert); not enough baseline (`learning`); she hangs up mid-task (`incomplete`).
3. **Fri morning, for M2:** the orchestrator with its tools, first against made-up tool results, then against the real measuring. Baseline store.
4. **Fri midday:** joined to the backend: one real call, end to end.
5. **Fri evening, for M3:** normal and acted sessions recorded and counted. Speech readings if face and arms are solid.
6. **Sat morning:** fixes only. Freeze at 12:00.

## 15. Done means

- A normal session of a teammate with a baseline ends `as_usual`. An acted droop held on both attempts ends `change_noticed` with the correct side, and the family message arrives before the call ends.
- The floor rule has unit tests for the five cases above.
- With Claude switched off, the call still completes on the fixed script.
- You have told your human, in numbers, how far apart normal and acted readings are for each sign, and which signs are not reliable yet.
- The false-alarm count was taken on the whole system, with Claude in it.

## 16. Not tested yet

- Whether an acted droop moves the face number beyond its jitter.
- How fast MediaPipe runs on the machine that will host it.
- Whether speech-to-text hides an acted slur.
- How long Claude takes per turn, and how that feels in a spoken call.
- Upload speed for task clips on the venue network and on a phone hotspot.

## 17. Risks to raise early

- The droop does not show in the numbers. Raise it tonight.
- Arms out of frame in real use. It needs the design and the script to solve it, not only code.
- Pauses while Claude thinks. Mitigation: fixed lines, a fast model, and the script fallback.
- Two plans for where the Claude loop lives (§13, point 2). Settle it before either side writes code against it.
- The core is built but nobody sees it, because the stage scene runs on mocks (§13, point 10). The organisers' guide says hard work the judges never see loses.
- If you fall behind, cut speech, then Claude's "more than the floor" concerns. Never cut the floor rule or its tests.

## Sources read on 12 Nov

- MediaPipe Face Landmarker: https://developers.google.com/edge/mediapipe/solutions/vision/face_landmarker
- MediaPipe Pose Landmarker: https://developers.google.com/edge/mediapipe/solutions/vision/pose_landmarker
- Claude vision: https://platform.claude.com/docs/en/build-with-claude/vision
- Claude models: https://platform.claude.com/docs/en/models/overview
- ElevenLabs speech-to-text: https://elevenlabs.io/docs/capabilities/speech-to-text
