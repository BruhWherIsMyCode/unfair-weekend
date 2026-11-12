# Agent 1 task: demo shell app (v3, 12 Nov night)

Overrides `docs/AGENT_1_IOS_APP.md` where they differ. Work on branch `agent-1/ios-app`, folder `ios/App/` only (AGENTS.md).

## What we are building

**Not a product. A stage shell for one scripted scene.** Everything is local, hard-coded and mocked. No backend, no `CheckInCore`, no camera, no measuring, no networking. It must look real on a big screen and never fail.

The design is the Claude Design export in `docs/design/export/` (file `Onas Calls Demo v5`). Rebuild it in SwiftUI faithfully: colours, type, spacing, copy. If something below conflicts with the design, the design wins on looks, this file wins on behaviour and wording rules.

## The scene

1. Fedya is on stage, talking about the product. His phone (iPhone 17 Pro Max) gets a video call (not our app, staged separately). He acts unwell. The call ends.
2. **Our app, on the operator phone (iPhone 18 Pro Max), mirrored to the Mac and the big screen,** rings loudly with an incoming alert.
3. The operator opens the app. Home screen: Fedya's profile with an amber banner above it ("Call Fedya now").
4. Opens the alert: what changed from his usual, the clip, and three choices:
   - **He's fine** (closes the alert, status back to "as usual").
   - **I'll call him myself** (starts a real phone call to Fedya's number).
   - **Call 112** (demo: fake, see Safety).
5. The operator picks "I'll call him myself". The phone really calls Fedya. Fedya declines on stage and carries on.

## Screens (match the design export)

1. **Home.** Persons list or Fedya's profile as in the design, with the amber alert banner when an alert is active; calm "as usual" state otherwise.
2. **Incoming alert.** Full-screen, loud, call-like screen with ringtone and vibration until the operator taps it. Then goes to the alert detail.
3. **Alert detail.** Text of what changed, the clip (plays inline, from a bundled mp4), the three choices.
4. **Calling.** After "I'll call him myself": open `tel:` to Fedya's number (system call screen takes over, that is fine).
5. **112 (fake).** A "Calling 112…" screen in our own UI with a visible small "DEMO – not dialled" label. Then back to home.

No other screens, no dead buttons. Anything in the design that this scene does not use: render it static or leave it out.

## Hidden demo controls

- **Trigger:** triple-tap on the home screen title fires the incoming alert after a delay chosen in the demo panel (0 / 5 / 10 s). Nothing visible on screen.
- **Demo panel:** long-press (2 s) on the title. Contains: Fedya's phone number, delay, "Reset to as usual", which clip to use. Saved in `UserDefaults`.
- **Reset** returns everything to the starting state, so the scene can be rehearsed many times in a row.

## Loud on stage

- Play the ring with `AVAudioPlayer` on an `AVAudioSession` category `.playback`, so it sounds even with the silent switch on. Volume is the phone's; tell your human to set it to max.
- The app stays in the foreground during the demo (it is mirrored anyway). Also schedule a local notification with sound as a backup in case the app is backgrounded. No push, no CallKit, no entitlements.
- Keep the screen awake (`isIdleTimerDisabled = true`).

## Safety (must have, reviewed)

- **The app must never dial 112 or any emergency number.** "Call 112" only shows the fake screen. There is no `tel:112` anywhere in the code. A real call to an emergency line from the stage is a real emergency-services call.
- The only `tel:` target is the number typed into the demo panel.

## Wording (claim rules, `docs/PROJECT_CONTEXT.md` §2)

- Never: "stroke", "detect", "diagnose", "predict", "suspect", "medical", "safe", "risk", "100%". Say "different from his usual", "noticed a change".
- The alert text ends with "If he seems unwell, call 112." (the person in the demo is Fedya, so "he").
- Example alert text: "The left corner of Fedya's mouth is lower than usual. It looked the same when he tried again. Call Fedya now. If he seems unwell, call 112."
- Button labels: "He's fine", "I'll call him myself", "Call 112". All strings in one `Localizable.xcstrings`.

## Assets

- The clip: a short mp4 of Fedya acting the droop, recorded by the team, added to the app bundle locally. **Do not commit it** (no recordings of people in the repo, AGENTS.md). Put it in `ios/App/LocalAssets/` and add that folder to `ios/App/.gitignore`. Show a placeholder frame if the file is missing.
- Fedya's photo: same rule, local only, placeholder if missing.

## Tech

- SwiftUI, iOS 17+, iPhone, portrait only. One target, no third-party packages.
- The state is one small `@Observable` store: `asUsual` → `ringing` → `alertOpen` → `calling` / `fake112` → back.

## Done means

- From a cold start: triple-tap → ring after the delay → open → clip plays → "I'll call him myself" opens the call to the number in the panel → returning to the app shows home; Reset restores the start.
- Run the scene 5 times in a row on the real iPhone without restarting the app.
- A search of the strings finds none of the banned words, and the code contains no `112` inside a `tel:` URL.
- PR into `main` saying what was tested on the device and what was not.
