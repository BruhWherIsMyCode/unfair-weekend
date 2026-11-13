# Binding the designed call screens to ParentCallKit

For the Claude Design "client app: check-in call flow" (440 × 956 pt, dark). The design's video uses fixed
timings; in the app every step moves only when `model.screen` changes. Rebuild the HTML export in SwiftUI.
Keep looks from the design, take behaviour from this file.

## One source of truth

```swift
@State var model = CallModel(api: CallAPI(baseURL: url, demoKey: key), personId: "p_demo",
                             devices: PhoneDevices.make())
// root view
.task { _ = await PhoneDevices.requestPermissions(); await model.start() }
```

| Design screen | Condition | Actions |
|---|---|---|
| 1 Incoming call | `model.screen.mode == .ringing(_, title)`, where title = "Daily check-in" | Accept → `model.answer()`, Decline → `model.decline()` |
| 2 Answer crossfade | `.ringing` → `.inCall` (0.6–0.7 s ease-in-out crossfade, no slide) | — |
| 3 In-call layout | `.inCall` | End (red 96 pt) → `model.hangUp()`, visible for the whole call |
| 9 Done | `.done`: show "Thank you" + check pop | after ~4 s, or on tap → `model.dismissDone()` |
| (not in design) | `.ended(reason)` with hangup/deadline/connection_lost | return to idle; show no result to her |
| (not in design) | `.idle` | waiting screen |

`model.phase` still exists, but it is the coarse version. Use `model.screen` for these views.

## Inside the call (`model.screen.card`)

| Card | Design step | Render |
|---|---|---|
| `.assistant(subtitle)` | 4 Avatar explains | label "Assistant", word-by-word subtitles |
| `.question(n, 3, subtitle, listening)` | 5 Question | "Question n of 3", subtitles; Listening indicator on the right only when `listening == true` |
| `.task(step, 3, "smile", timer, hint)` | 6 Smile | "Step 1 of 3", "Smile wide", "Look at the screen" |
| `.task(step, 3, "arms", timer, hint)` | 7 Arms | "Step 2 of 3", "Raise both arms", "Keep them up" |
| `.sentence(3, 3, sentence, listening)` | 8 Sentence | "Step 3 of 3", `"\(sentence)"` in quotes 30 pt, "Say it out loud"; Listening when `listening` |
| `nil` | right after answer | card not shown yet (entrance animation starts on first non-nil) |

Fixed task texts belong to the UI. The sentence text comes from the backend: the current script says
"Today I am sitting by the window.", not the design's "The sun is shining over Vilnius today."

- **Card crossfade**: key the card content on its identity (`subtitle.lineId`, the task name, the question
  number). A 0.5 s crossfade fires whenever that identity changes. The card never goes empty.
- **Progress dots**: `model.screen.dot(.questions | .smile | .arms | .sentence)` returns `.done` (green #7FD08F),
  `.current` (28 pt amber bar) or `.upcoming` (white at 30%).
- **Self-view size**: `model.screen.selfViewLarge` → 200 × 266 and the card lifts 44 pt; otherwise 150 × 216.
  Animate over 0.8 s. The self-view itself is a local `AVCaptureVideoPreviewLayer`; it does not come from the backend.
- **Subtitles**: lay out the whole `subtitle.text` up front (no reflow). Word `i` fades 0 → 1 over 0.7 s,
  starting at `subtitle.wordStart(i)`. Use `TimelineView(.animation)` and compute opacity from the current date.
  Timing is evenly spread over `durationMs` until the backend sends TTS word timestamps.
- **Timer ring** (`timer`):
  - `.waiting(total)`: the instruction is still playing, so show a full ring with `total`, not moving.
  - `.running(endsAt, total)`: count down from `ceil(endsAt - now)`. Progress = remaining / total, and the
    number goes 5 → 0 or 12 → 0. The design says 10 for arms; the backend window is 12 s.
  - `.finished`: the ring turns green and the number becomes a check mark.
- **Hint**: optional neutral line under the task text. `look_at_camera` → "Look at the screen",
  `show_both_arms` → "Show both arms", `check_camera` → "Check your camera".
- **Recovery** (camera lost mid-task): the backend speaks `camera_unavailable`. The card becomes `.assistant`
  with that line, the progress dot stays on the task, and a retry comes back as a new `.task`. Nothing special is needed.
- **Repeats**: `repeat_smile` / `repeat_arms` produce a fresh `.task(... .waiting)` for the same step. The
  timer restarts and the dot does not advance.

## Not covered by the design (decide)

- CallKit: the design asks for the system incoming-call screen. We render our own lookalike in-app.
  Real CallKit needs a VoIP push, which is out of scope for the demo; label it as staged.
- Avatar video is the full-screen background. Until Simli is wired into the parent app, use a looping
  local avatar clip or a still image, with the 1.08 → 1.0 settle animation.
- "Next call at 18:00" is static text in the UI.
- Wording: no numbers, results or health words anywhere on her screens.
