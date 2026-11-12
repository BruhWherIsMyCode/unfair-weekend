# Shared contract v0

Read by all three agents. Owned by Agent 3.

Written 12 Nov 2026, kickoff evening. **Nothing here is built or tested.** It exists so three agents can start in parallel without inventing three different APIs. Agent 3 moves it into the repo as `contract/` in the first hour and keeps it current. From then on the repo copy wins over this file.

Labels are the same as in `PROJECT_CONTEXT.md`: **[proposal]** = accept, change or drop; **[unverified]** = from memory or reasoning, not checked.

## Change, 12 Nov late evening (Feodor): overrides the sections below where they disagree

Reported by Feodor for the team. The other two teammates have not reviewed this text. Details and reasons are in `AGENT_3_CONNECTOR.md`, which was rewritten.

Decided:

- **The analysis runs on a server**, not on the parent's phone.
- **Agent 3 is now "analysis and orchestrator"**: the measuring, her baseline, and a Claude orchestrator that runs the call. Agent 2 builds the backend that connects the apps, the avatar and the orchestrator.
- **Claude is the orchestrator.** Measuring tools report readings to it. It leads the conversation, drives the avatar, and can send the alert during the call.
- **Our own apps on both sides**, parent and family (said about 22:00). `docs/tasks/agent1.md`, written later, has the family app as a mocked stage shell and the parent's call "not our app, staged separately". Which app carries the parent's call needs confirming.

What no longer holds below:

- §2, the diagram and rule 2. `CheckInCore` no longer measures, and "video frames never leave the phone" is not true any more: short task clips go to our server to be measured. [proposal] Under the plan in Agent 3's brief her voice also goes to ElevenLabs for speech-to-text, and her answers and readings go to Claude as text.
- §3. `ios/CheckInCore/` has no owner. Agent 3 owns `analysis/` [proposal for the name] and keeps `contract/` until the team says otherwise.
- §4. The API has no way to send a task clip for measuring (`POST /calls/{call_id}/clip` is the alert clip only), and no way to send an alert before the call ends (`POST /calls/{call_id}/result` comes at the end).
- §7. The Swift seam assumed the measuring was on the phone.
- §9, M2. "On a real iPhone" now means measured on the server from a real iPhone's clips.

Still holds: the `SessionResult` and `Message` shapes as a starting point, the events in §5, the avatar modes in §6, the script line ids in §8, the milestone times in §9, and every rule in §10.

Not agreed yet, and blocking code on both sides of the seam: `AGENT_3_CONNECTOR.md` §13. The first three are the seam between the backend and the orchestrator, the difference from Agent 2's plan on branch `agent-2/backend`, and how task clips reach the server.

## 1. What changed against PROJECT_CONTEXT.md

The team decided this after the context file was written. Where the two disagree, this file and the agent briefs win.

- The parent's side is a **native iOS app**, not a browser page. Context §4 ("one call screen in the browser", "out of scope: a native app") and §5 ("runs in the browser") no longer hold.
- There is a **backend**, and it may use Twilio.
- The three tracks proposed in context §11 are replaced by three agents: iOS app, backend, connector.
- Everything else in the context file still holds. The claim rules in §2 apply to every string any agent writes.

## 2. Architecture

```
 iPhone, Parent role                          Backend (Agent 2)
+-------------------------------+            +---------------------------+
| App UI (Agent 1)              |            | ring / no answer          |
|  screens, camera, mic, clips  |            | avatar and voice sessions |
|      | frames      ^ state    |   HTTPS    | message to the family     |
|      v             |          |  + events  | clips, session log, stats |
| CheckInCore (Agent 3)         | <--------> |                           |
|  call flow, measuring,        |            +-------------+-------------+
|  baseline, decision, API      |                          |
+-------------------------------+                          | event + SMS
                                                           v
 iPhone, Family role (same app)  <-------------------------+
```

Three rules of the seams:

1. The UI never talks to the backend. It talks to `CheckInCore`, and `CheckInCore` talks to the backend.
2. Video frames never leave the phone. What leaves: numbers, her answers, and one short clip when there is an alert.
3. API keys for rented services live only on the backend.

## 3. Repo layout and ownership

One git repo. Each agent works on its own branch and reaches `main` through pull requests; the branch rules are in `AGENTS.md` at the repo root.

| Folder | Owner | Contents |
|---|---|---|
| `contract/` | Agent 3 | `openapi.yaml`, `events.md`, `script.<lang>.json`, `fixtures/` |
| `ios/CheckInCore/` | Agent 3 | Swift package: API client, call flow, measuring, baseline, decision, `AvatarView` |
| `ios/App/` | Agent 1 | Xcode project, SwiftUI screens, camera and mic capture, clip recording, design assets |
| `backend/` | Agent 2 | Server, avatar page, message templates, trigger page |
| `docs/` | humans | `PROJECT_CONTEXT.md`, this file, the three briefs |
| repo root | humans | `AGENTS.md`, README |

Edit only your own folders. If you need a change in someone else's folder, open a GitHub issue titled `[to agent N] ...` (who asks, what, why) and tell your human. Do not make the edit yourself.

## 4. HTTP API v0 [proposal]

Base URL `https://<host>/v1`, JSON bodies. Every request carries the header `X-Demo-Key` (the repo will be public on Devpost).

| Method and path | Body → response | Purpose |
|---|---|---|
| `GET /health` | → `{ok: true}` | Is the backend up |
| `POST /devices` | `{device_id, role, person_id, family_id}` → 204 | Register a phone. `role` is `parent` or `family` |
| `GET /devices/{device_id}/events` | → event stream | Server-sent events, see §5 |
| `POST /calls` | `{person_id}` → `{call_id}` | Start a call now. Pressed by the demo operator. No scheduling |
| `POST /calls/{call_id}/answer` | → `{avatar, script_lang}` | She answered. `avatar` is described in §6 |
| `POST /calls/{call_id}/decline` | → 204 | She declined. No answer within `ring_timeout_s` counts the same |
| `POST /calls/{call_id}/clip` | multipart mp4, 10 s or less → `{clip_id}` | Upload the clip of the flagged task |
| `POST /calls/{call_id}/result` | `SessionResult` → `Message` | End of call. The backend writes the family message and sends it |
| `GET /families/{family_id}/messages` | → `[Message]` | Family screen, newest first |
| `GET /stats?person_id=` | → `Stats` | The false-alarm count shown on screen |
| `GET /clips/{clip_id}` | → mp4 | Unguessable id |

`SessionResult`:

```json
{
  "person_id": "p_demo",
  "label": "live",
  "duration_s": 96,
  "answers": {
    "medicine": "yes",
    "feeling": "I slept well",
    "unusual": "no"
  },
  "signs": [
    {
      "sign": "face",
      "side": "left",
      "attempts": [
        {"value": 0.071, "usual_low": -0.012, "usual_high": 0.019, "outside": true},
        {"value": 0.066, "usual_low": -0.012, "usual_high": 0.019, "outside": true}
      ],
      "confirmed": true
    },
    {"sign": "arm", "side": null, "attempts": [{"value": 0.01, "usual_low": -0.03, "usual_high": 0.03, "outside": false}], "confirmed": false},
    {"sign": "speech", "side": null, "attempts": [{"value": 0.0, "usual_low": 0.0, "usual_high": 0.1, "outside": false}], "confirmed": false}
  ],
  "status": "change_noticed",
  "baseline_sessions": 12,
  "baseline_needed": 5,
  "clip_id": "c_8f3k2n"
}
```

- `baseline_sessions` / `baseline_needed`: how many earlier sessions her baseline holds, and how many it needs before alerts are possible. The `learning` message uses them.

- `label`: `live`, or `normal` / `acted` for recorded evidence sessions (context §7).
- `answers.medicine`: `yes`, `no` or `unclear`. The other two are her words as transcribed.
- `sign`: `face`, `arm`, `speech`. `side`: `left`, `right` or `null`, always **her** left and right.
- `confirmed` is true only when the sign was outside her usual twice in this call.
- `status`: `as_usual`, `change_noticed`, `learning` (not enough earlier sessions for a baseline yet), `incomplete` (she answered but did not finish).

`Message`:

```json
{
  "message_id": "m_1",
  "call_id": "call_1",
  "kind": "change_noticed",
  "text": "The left corner of Ona's mouth is lower than usual. Call her now. If she seems unwell, call 112.",
  "clip_url": "https://<host>/v1/clips/c_8f3k2n",
  "created_at": "2026-11-13T09:02:11Z"
}
```

`kind`: `as_usual`, `change_noticed`, `learning`, `incomplete`, `no_answer`. The backend alone writes `text`, from templates, so the wording can be checked in one place.

`Stats`: `{normal_sessions, normal_flagged, acted_sessions, acted_flagged}`. `normal_flagged` is the false-alarm count.

## 5. Events

Sent on `GET /devices/{device_id}/events`.

| Event | Data | Goes to |
|---|---|---|
| `call.incoming` | `{call_id, caller_name, ring_timeout_s}` | parent phone |
| `call.cancelled` | `{call_id}` | parent phone |
| `message.new` | `Message` | family phone |

The ring is our own in-app screen, driven by `call.incoming`, with the app open. That is staged and we say so (context §4). A real ring on a locked phone is a stretch goal, see Agent 2's brief.

## 6. Avatar modes

`POST /calls/{call_id}/answer` returns one of three shapes. All three are driven the same way: the app asks for a line by its id and is told when the line has been spoken.

| `mode` | Payload | Meaning |
|---|---|---|
| `still` | `{image_url, lines: {line_id: audio_url}}` | A photo and a voice. **Built first. Always kept working as the fallback** |
| `clips` | `{lines: {line_id: mp4_url}}` | Pre-rendered avatar video per line. No delay, no live network risk |
| `live` | `{page_url}` | A web page with the rented real-time avatar. Loaded in a web view. The page exposes `window.avatar.say(lineId)` and posts `{type: "spoken", lineId}` back when done |

## 7. Core and UI seam [proposal, a sketch]

Agent 3 publishes the real types, with a mock, in the first hour. Until then Agent 1 codes against this sketch.

```swift
@MainActor
public protocol CheckInSessioning: ObservableObject {
    var phase: Phase { get }            // what the screen shows now
    var caption: String { get }         // the line the avatar is saying
    var framing: Framing { get }        // .ok, .faceNotVisible, .armsNotVisible
    var readings: [SignReading] { get } // live numbers against her usual, for the demo overlay
    var stats: Stats? { get }           // the false-alarm count

    func answer()
    func decline()
    func hangUp()

    func ingest(video: CMSampleBuffer)  // every camera frame while in a call
    func ingest(audio: CMSampleBuffer)  // microphone
    var clipProvider: ((TaskKind, Int) async -> URL?)? { get set } // app returns the clip file for a task attempt
}

public enum Phase {
    case idle
    case ringing(callerName: String)
    case connecting
    case speaking(lineID: String)
    case listening(question: String)
    case task(kind: TaskKind, attempt: Int, secondsLeft: Int?)
    case finished          // identical whatever the outcome: she is never shown a verdict
    case failed(reason: String)
}

public enum TaskKind { case smile, arms, sentence }
```

Also exported by `CheckInCore`:

- `AvatarView(session:)`, a SwiftUI view that renders whichever avatar mode the backend returned. Agent 1 places and frames it.
- `FamilyFeed`, an observable list of `Message` for the Family role.
- `MockCheckInSession`, which walks through a normal call and an alert call on a timer with fake numbers. It needs no camera and no backend.

Split of capture: the **app** owns the camera, the microphone and the clip files. **Core** owns everything computed from them.

## 8. Script lines

`contract/script.<lang>.json` maps line ids to text. `{name}` is her name.

Ids: `greet`, `q_medicine`, `q_feeling`, `q_unusual`, `setup_phone`, `task_smile`, `task_arms`, `task_sentence`, `repeat_smile`, `repeat_arms`, `repeat_sentence`, `bye`. The sentence she repeats is under the key `sentence`.

No line tells her a health verdict. `bye` is the same after every call.

## 9. Milestones

| When | What works |
|---|---|
| **M1, Thu night** | Walking skeleton. Operator presses call → phone rings → she answers → still face speaks the lines → fake numbers → result → message appears on the Family phone. Mocks are fine |
| **M2, Fri 12:00** | Real measuring of the three signs against a stored baseline on a real iPhone. Real alert with a clip. Good enough for the Friday afternoon test with older people |
| **M3, Fri evening** | Avatar decision made and in (live, clips or still). SMS to the family phone. Lab labels and the false-alarm count on screen |
| **M4, Sat 12:00** | Feature freeze. Fixes only. The video is recorded Saturday afternoon. Devpost deadline is 20:00 |

## 10. Rules for every agent

- Follow context §2. Never write "detects a stroke", "diagnoses", "predicts", "medical device", "100%" or "safe" in the UI, code comments shown to users, README, logs shown on screen or sample data.
- Every `change_noticed` message ends with "If she seems unwell, call 112."
- Sessions with an acted droop or slur are labelled `acted` in the data and visibly on screen and on the clip.
- Only our own or consented recordings. No real patient data, no faces from the internet.
- Report status honestly to your human: built, built but untested, mocked, not started. A mock that looks real is the fastest way to lose the demo.
- One golden path, end to end, before anything else. No screen, endpoint or button that the golden path does not use.
- No secrets in the repo.
