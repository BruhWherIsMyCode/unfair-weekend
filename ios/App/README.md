# Ona's calls: stage demo shell (iOS)

A shell for one scripted stage scene on the operator iPhone, which can also receive real alerts from the check-in backend. The task is `docs/tasks/agent1.md`, the design is `docs/design/export/`.

## Real alerts from the check-in call

In the demo panel, under **Check-in backend**, set the backend address (`http://<mac-ip>:8000`), the demo key and the family (`f_demo`), then **Connect**. The app registers as a family device and listens for `message.new`:

- **Change from her usual, or a concern:** a time-sensitive notification with the alert sound, the amber card, and the alert screen with the backend's own text and the clip. The clip area shows **an earlier check-in next to today's**: same task, one play button and scrubber for both, **Try 1 / Try 2** when today's task was tried twice. Today's clips are the call's attempt videos (`GET /v1/calls/{call_id}/clips`, backend `CLIP_STORAGE_DIR`), at the task with the most tries. The earlier clip is chosen on this phone: the newest earlier backend call in the history that was as usual or still learning, with a finished, not acted video of the same task. Nothing marks it as part of the usual yet; that selection belongs to Agent 3. No earlier call, no video for today, a failed fetch (Retry) and an acted call each have their own panel. A backend that records no videos keeps the old clip: her camera frames from both tries (`GET /v1/clips/{id}`, kept in the backend's memory for 30 min). A real alert never shows the staged `clip.mp4`.
- **Could not finish, or no answer:** a plain notification with the backend's text.
- **As usual, or still learning:** nothing.

Demo workaround, not for the App Store: after connecting, a silent audio loop (the `audio` background mode) keeps the app running in the background, so alerts still arrive while the phone is locked. There is no push. If iOS ends the app, open it again. The names on screen are the profile's (Fedya, he by default). The report text uses the backend's person, so for the stage set `DEMO_PERSON_NAME=Fedya` and `DEMO_PERSON_PRONOUN=he` in `backend/.env`.

The staged scene (Arm, Show alert now, Reset) still works with or without a backend.

## Build

Built and tested with Xcode 27.2. Runs on iOS 17+. Needs [XcodeGen](https://github.com/yonaskolb/XcodeGen) if you change `project.yml`.

```bash
cd ios/App
xcodegen generate          # only after editing project.yml; the generated project is committed
open OnasCalls.xcodeproj
```

Simulator build and scene tests from the command line:

```bash
xcodebuild -project OnasCalls.xcodeproj -scheme OnasCalls \
  -destination 'platform=iOS Simulator,name=iPhone 18 Pro' build
xcodebuild -project OnasCalls.xcodeproj -scheme OnasCalls \
  -destination 'platform=iOS Simulator,name=iPhone 18 Pro' test
```

## Signing (real iPhone)

```bash
cp Local.xcconfig.example Local.xcconfig   # untracked, never commit it
```

Set `DEVELOPMENT_TEAM`. A free Personal Team cannot use the Time Sensitive Notifications capability, so with one, switch `ONAS_ENTITLEMENTS` to the basic file as the example shows. The notification then arrives as a normal one: no "TIME SENSITIVE" label, and a Focus mode can hold it back. A paid team keeps the label. You may also need your own `ONAS_BUNDLE_ID` if someone else has registered this one. On the phone, trust the developer once: Settings > General > VPN & Device Management.

## Private assets

Put Fedya's clip and photo here. They are git-ignored and copied into the app at build time:

```
ios/App/LocalAssets/clip.mp4     # about 10 s, today's clip in the staged alert
ios/App/LocalAssets/earlier.mp4  # optional, the staged alert's earlier check-in
ios/App/LocalAssets/fedya.jpg
```

Without them the app shows the design's striped placeholders.

## Profile

Tap the person's row on Family. The profile shows their name, relation and number, the alert waiting (if any), the family's notes and every check-in call, newest first.

- **Edit** changes the name, relation ("Dad"), he/she, phone number, photo (picked from Photos) and notes. Nothing changes until **Save**. The whole app uses the new name and pronoun; the backend's report text keeps its own.
- **Calls:** every staged alert and every backend message (as usual and still learning too) goes into the history. A call opens its report, what the family did ("You called at", "You said he was fine"), a note to add, and Delete. **Edit history** or a swipe deletes calls.
- **Clip:** a call with a clip shows it with **Save to Photos** and **Share** (Files, AirDrop, Messages). A real alert's frames are stored on the phone as soon as they arrive, so the clip stays after the backend forgets it (30 min), and are encoded to an .mp4 when the call is opened. The staged scene's call uses `clip.mp4` if it is present.
- **Attempt videos:** with a recording backend, each call shows a thumbnail and its clip state: kept until (7 days), preparing, couldn't be saved, or expired. The call opens a **Clips** list with one row per attempt ("Smile · try 1"); a tap downloads it once and plays it, so it stays on the phone after the backend deletes it.
- First launch fills the history with a sample week of calm calls. **Reset** in the demo panel removes the staged alert it reset from the history; **Reset call history** goes back to the sample week.

Stored in the app's Application Support folder (history, notes, clip frames, photo) and UserDefaults (profile). Nothing leaves the phone except what the family shares.

## Demo panel

Hold the "Family" title for 2 seconds. Fedya's number (the only number the app dials; the same field as on the profile, and never a number shorter than 6 digits, so never 112), alert delay, **Arm**, **Show alert now**, **Fake call screen**, **Reset**. Settings are kept between launches.

## Stage notes

- Ring/silent switch on ring, volume up, Focus off. The notification sound cannot play through silent mode.
- iOS asks "Call +370…?" before a `tel:` call from an app; the operator taps Call. [unverified on the stage phone]
- "Call 112" never dials. It shows a calling screen marked "DEMO – not dialled" for 4 s.
- No signal: turn on **Fake call screen** before arming.
