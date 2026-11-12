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

## Tap map (from the design's click handlers, `unpacked/template.html`)

Every tappable element in the design and where it leads. Build exactly these, nothing else is tappable.

| Screen | Element | Leads to |
|---|---|---|
| Lock screen (iOS) | the "Ona's calls" notification ("Call Fedya now. He seems different from his usual.") | app opens on **Home (alert)** |
| Home (alert) | amber card "NOT HIS USUAL / Call Fedya now / Same on both tries / 10 second clip" | **Alert** |
| Home (alert) | white row under it: Fedya, "Dad, last call 13:01" | **Alert** |
| Home | Ona and Aldona rows ("As usual") | not tappable |
| Alert | "‹ Family" back button, top left | **Home (alert)** |
| Alert | clip area | play / pause (the design auto-plays, 0:00 → 0:10, loops) |
| Alert | green "Call Fedya" | real call: `tel:<number from the panel>` (design step 5 is the system call screen) |
| Alert | red "Call 112" | **fake** 112 calling screen with "DEMO – not dialled", 4 s, back to Alert. Never dials. Not wired in the design; this is our addition |
| Alert | grey "He's fine" | **Home (as usual)**: Fedya's card gone, Fedya listed "As usual". Not wired in the design; this is our addition |
| Calling (fallback only) | whole screen / red end button | **Home (declined)** |
| Home (declined) | green "Call Fedya" under "Declined at 13:02 / Call Fedya again" | same as Alert → Call Fedya |
| Home (declined) | Fedya row "Dad, you called at 13:02" | **Alert** |

Texts to copy exactly (status bar times in the design are 12:58 / 13:01 / 13:02 / 13:03; in the app use the real clock):
- Home alert card: "NOT HIS USUAL" · time · "Call Fedya now" · "Same on both tries" · "10 second clip".
- Alert: "Fedya", "Check-in at 13:01, 1 min 40 s", clip label "clip, 2nd try", "NOT HIS USUAL", "Call Fedya now", section "Report": "Fedya seemed different from his usual during today's check-in." / "He says he took his medicine and feels a bit tired." Buttons "Call Fedya", "Call 112", "He's fine", footer "If he seems unwell, call 112."
- Home declined: "Declined at HH:MM", "Call Fedya again", row "Dad, you called at HH:MM".
- "Everyone else": Ona — Mum. "Going to the market." — As usual 09:02; Aldona — Aunt. "Watching the news." — As usual 09:15.
- Colours from the markup: amber card `#FCE8C4`, amber text `#8A5406` / `#5A3804`, amber outline `#E7B866`, green button `#237A3A`, red button `#C4362F`, grey button `#EAE3D9`, ink `#1F1B16`, secondary `#6B6359`, back link `#8A5A33`.

## Stage runbook (what the operator does, second by second)

1. **Backstage, 5 min before:** open the app → long-press "Family" 2 s → demo panel → check Fedya's number → **Reset** → delay 60 s → **Arm** → lock the phone. Volume max, Focus off.
2. **Fedya on stage** talks; his own iPhone 17 gets the check-in video call (not our app); he acts unwell; the call ends.
3. **iPhone 18 lock screen:** notification with loud sound. Start mirroring to the Mac now if not already on.
4. Tap the notification → Face ID → **Home (alert)**, amber card above Fedya.
5. Tap the amber card → **Alert**, clip plays. Pause 3–4 s so the room reads the report.
6. Tap **Call Fedya** → system call screen → Fedya's phone rings on stage → Fedya declines.
7. The app comes back on **Home (declined)**. Fedya continues the talk.
8. Rehearsal again: long-press "Family" → **Reset** → **Arm**.

If there is no signal: in the panel turn on **Fake call screen** before step 1; step 6 then shows our calling screen for 4 s and goes to Declined.

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
