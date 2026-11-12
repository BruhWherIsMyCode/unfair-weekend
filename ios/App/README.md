# Ona's calls: stage demo shell (iOS)

A shell for one scripted stage scene on the operator iPhone. Everything is local and hard-coded: no backend, no camera, no measuring, no networking. The task is `docs/tasks/agent1.md`, the design is `docs/design/export/`.

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
ios/App/LocalAssets/clip.mp4   # about 10 s
ios/App/LocalAssets/fedya.jpg
```

Without them the app shows the design's striped placeholders.

## Demo panel

Hold the "Family" title for 2 seconds. Fedya's number (the only number the app dials), alert delay, **Arm**, **Show alert now**, **Fake call screen**, **Reset**. Settings are kept between launches.

## Stage notes

- Ring/silent switch on ring, volume up, Focus off. The notification sound cannot play through silent mode.
- iOS asks "Call +370…?" before a `tel:` call from an app; the operator taps Call. [unverified on the stage phone]
- "Call 112" never dials. It shows a calling screen marked "DEMO – not dialled" for 4 s.
- No signal: turn on **Fake call screen** before arming.
