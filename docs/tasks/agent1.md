# Agent 1 task: stage demo shell (v4, 12 Nov night)

Overrides `docs/AGENT_1_IOS_APP.md` and every earlier version of this file where they differ. Branch `agent-1/ios-app`, folder `ios/App/` only (see `AGENTS.md`). Your work reaches `main` only through a pull request.

## What we are building

**Not a product. A shell for one scripted stage scene**, on one iPhone (the "operator" phone), mirrored to a Mac and the big screen. Everything is local and hard-coded: no backend, no `CheckInCore`, no camera, no measuring, no networking, no accounts. It must look real and must not fail on stage.

## The design

`docs/design/export/` (PR #2, "Onas Calls Demo v5"). `onas-calls-demo-v5.html` is the real export: open it in a browser, switch to "Present" mode, step with the arrow keys. `unpacked/template.html` is the same markup as readable text. Rebuild its screens in SwiftUI faithfully: colours (take the hex values from the markup), type, spacing, copy. The storyboard frame, step numbers and "Made with Claude Design" are not part of the app.

Its six steps and what each one is in the app:

| Design step | In the app |
|---|---|
| 1 Lock screen | The real iOS lock screen. Not built by us |
| 2 Notification "Call Fedya now. He seems different from his usual." | A real local notification from our app, app name "Ona's calls" |
| 3 Family | **Home**: amber "NOT HIS USUAL / Call Fedya now" card with clip thumbnail on top, then "Everyone else" (Ona, Aldona: As usual) |
| 4 Alert | **Alert**: clip playing with its 0:00 / 0:10 progress, the report text, buttons "Call Fedya", "Call 112", "He's fine", and the 112 line |
| 5 Calling | The real system call screen after `tel:` (see below). Build the design's version only as the fallback |
| 6 Declined | **Home, after the call**: "Declined at 13:02 / Call Fedya again", Fedya's row says "you called at 13:02" |

## The scene

1. Before going on stage: operator opens the hidden demo panel, taps **Arm**, locks the phone.
2. After the armed delay (default 60 s, set in the panel) the app's local notification lands on the lock screen with sound (design step 2). Meanwhile Fedya, on his own phone, takes the check-in call and acts unwell.
3. Operator taps the notification → app opens on **Home** in the alert state (step 3) → taps the amber card → **Alert** (step 4), clip plays.
4. Operator taps **Call Fedya** → the app opens `tel:<Fedya's number>` → Fedya's phone really rings on stage → he declines.
5. When the call ends and the app is active again, it shows **Home, after the call** (step 6) with the real time.

Other buttons:
- **He's fine** → Fedya's card goes back to "As usual". End of scene.
- **Call 112** → **never dials.** Shows the design's calling screen with "112" and a small visible label "DEMO – not dialled", then returns. There must be no `tel:112` (or any emergency number) anywhere in the code.
- **Call Fedya again** → same as Call Fedya.

## Hidden demo panel

Opened with a 2-second long-press on the "Family" title. Saved in `UserDefaults`. Contains:
- Fedya's phone number (the only `tel:` target in the app).
- Alert delay in seconds (presets 10 / 30 / 60 / 120).
- **Arm**: schedules the local notification and puts the app in the alert state.
- **Show alert now**: alert state without a notification, for rehearsal.
- **Fake call screen** toggle: if on, "Call Fedya" shows the design's step-5 calling screen for 4 s, then "Declined", instead of dialling. Fallback for no signal on stage.
- **Reset**: back to the start (everyone As usual, nothing armed, notification cancelled). The scene must be repeatable many times in a row without reinstalling.

Nothing else in the app is hidden or dead. No other screens, no settings, no tab bar.

## Technical rules

- SwiftUI, iOS 17+, iPhone only, portrait only. No third-party packages.
- Generate the project with **XcodeGen** (`ios/App/project.yml` → `xcodegen generate`). Commit `project.yml`; the generated `.xcodeproj` may be committed too so a teammate without XcodeGen can open it. Signing team comes from an untracked `ios/App/Local.xcconfig` (`DEVELOPMENT_TEAM = ...`); commit `Local.xcconfig.example` instead.
- State: one small `@Observable` store, states `asUsual` → `alert` → `calling` → `declined`, plus `fake112`.
- Notification: `UNUserNotificationCenter`, ask permission on first launch, a custom sound (`.caf`, under 30 s, in the bundle, a loud ring). Try `interruptionLevel = .timeSensitive` to match the design's "TIME SENSITIVE" label; it needs the Time Sensitive Notifications capability. If the signing team cannot use it, fall back to `.active` and say so in the PR. [unverified whether a free Apple ID team allows it]
- Detecting the end of the real call: `CXCallObserver` (CallKit, observing only, no entitlement needed) plus `scenePhase` returning to `.active`. Use the real time for "Declined at".
- Clip: `AVPlayer` with a bundled mp4, looping, muted by default, with a mute toggle. Show the design's dark placeholder if the file is missing.
- Keep the screen awake while the app is in the foreground (`isIdleTimerDisabled`).
- All strings in one `Localizable.xcstrings`.

## Private assets: never commit

- `ios/App/LocalAssets/clip.mp4` (Fedya acting, about 10 s) and `fedya.jpg`. Add `LocalAssets/` to `ios/App/.gitignore`. The app must build and run without them (placeholders). Reason: `AGENTS.md`, no recordings of people in a repo that goes public.

## Wording rules (`docs/PROJECT_CONTEXT.md` §2)

- Never in any string: "stroke", "detect", "diagnose", "predict", "suspect", "medical", "safe", "risk", "100%".
- Use the design copy as written. The alert ends with "If he seems unwell, call 112."

## Verify before opening the PR

1. `xcodebuild` builds for the Simulator with no errors (find a simulator with `xcrun simctl list devices available`).
2. The scene runs in the Simulator with "Show alert now" and the fake call screen.
3. `grep -rniE "stroke|detect|diagnos|predict|suspect|medical|\\bsafe\\b|100%" ios/App --include=*.swift --include=*.xcstrings --include=*.plist` finds nothing user-visible.
4. `grep -rn "tel:" ios/App` shows only the number from the panel.
5. `git status` shows no mp4, jpg, `.xcconfig` with a team id, or keys.

In the PR say what ran in the Simulator, what ran on the real iPhone (notification on the lock screen, the real `tel:` call, the Declined state) and what did not run yet.
