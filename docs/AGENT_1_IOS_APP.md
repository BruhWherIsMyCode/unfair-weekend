# Agent 1: the iOS app

You build what the judges and the older person see.

Read first, in this order: `PROJECT_CONTEXT.md` (§1, §2, §4 and the demo scene in §9), then `SHARED_CONTRACT.md`. Where they disagree, `SHARED_CONTRACT.md` wins. Nothing described here exists yet.

## Mission

A native iOS app with two roles on one build:

- **Parent:** the phone rings, she answers, the avatar talks her through the check-in and the three tasks, the call ends. After she answers she never has to touch the screen again.
- **Family:** one plain message arrives: "as usual", or an alert with a short clip and a button to call her.

Plus a demo overlay that puts the live numbers on screen. The organisers' guide says hard work the judges never see loses; the overlay is how they see ours.

## You own

- `ios/App/`: the Xcode project, all SwiftUI screens, design assets, strings.
- Camera and microphone capture, the self-preview, and recording a clip file for each task attempt.
- Ringtone, vibration, keeping the screen awake during a call.

## You do not own

- Measuring, baseline, decision, networking, the avatar renderer. They come from the Swift package `ios/CheckInCore/` (Agent 3). You render its state and feed it frames.
- Anything in `backend/` or `contract/`.
- The visual design. It is drawn by a teammate in Claude Design; you implement it.

## What you need from your human

1. **The Claude Design artifact** (link or export). Download it, read it, and implement it faithfully in SwiftUI: colours, type sizes, spacing, copy. If it has not arrived, build every screen as plain unstyled SwiftUI wired to `MockCheckInSession`, and restyle when it comes. Do not invent a visual identity in the meantime.
2. **A physical iPhone** and an Apple ID for signing. The iOS Simulator has no camera, so the real call can only be tried on a device. Use the Simulator with the mock for layout work.
3. A cheap phone stand for the demo phone (see "Decisions" below: the phone stands, portrait).

## Screens

The design should cover exactly these. Tell your human if one is missing from the artifact.

Parent role:

1. **Incoming call.** Full screen, caller name (set by her grandchild), one large answer button, ring and vibration. Shown when `phase` is `.ringing`.
2. **In call.** The avatar large (`AvatarView` from Core), her own picture small, and what the avatar is saying as large captions.
3. **Task.** What to do, shown as a picture or animation, and a countdown for the 10-second arm hold. A framing hint when `framing` is not `.ok`: "put the phone down and sit back so I can see your arms". [unverified] Rented real-time avatars are talking heads and probably cannot raise their arms, so the app has to show the arm task itself.
4. **Repeat.** Same task screen, second attempt. Neutral wording: "let's do that once more". Nothing that hints at a result.
5. **End.** A goodbye. Identical after every call. She never sees a verdict, a number or a colour that implies one.

Family role:

6. **Message.** One card per `Message`, newest first. The text comes from the backend. Display it exactly as given: do not rewrite it, do not add your own 112 line (the backend text already ends with it). Five kinds, all must be designed:
   - `as_usual`: the text, which carries her answers.
   - `change_noticed`: the text, the clip (plays inline), and one large button "Call {name}" (`tel:` link to her number from Setup).
   - `learning`: the text ("Still learning {name}'s usual, n of 5 calls"), with a small progress mark.
   - `incomplete`: the text and the same "Call {name}" button.
   - `no_answer`: the text and the same "Call {name}" button.

Both:

7. **Setup.** Choose role. Parent phone: her name, caller name. Family phone: her name and **her phone number** (stored on this phone only, used by the `tel:` button; it is not sent to the backend). Minimal.
8. **Demo overlay**, toggled by a hidden gesture. For each sign: the live value, her usual range, and a mark when it is outside. Attempt 1 and attempt 2. The count from `stats`, as "N false alarms in M normal sessions".
9. **Lab switch** inside setup: pick the person and label the session `normal` or `acted`. When the label is `acted`, show "ACTED" on screen for the whole call and burn it into the clip.

No dashboard, no history charts, no settings beyond these. A button that does nothing is worse than no button.

## Decisions (12 Nov, Alex and Valdo)

These close the open points in this brief. Where they differ from the text above or from the context file, they win.

- **How the phone stands.** During the call the phone stands upright in portrait on the table, on a stand or leaning on something, about an arm and a half away; she sits back. She answers holding it, then the avatar says `setup_phone` and the app shows a picture of exactly that before the first task. No landscape.
- **Minimum iOS 17, portrait only, iPhone only.**
- **Audio session:** owned by Core, see Technical notes.
- **Family message:** the app shows the backend text verbatim. The 112 line and the pronoun in it are the backend's job. The button says "Call {name}" with her name, not "Call her", so it is right for any person, including a male teammate in the demo.
- **Her phone number** is entered on the Family phone in Setup and stays on that phone. Without it the call button is hidden, never dead.
- **"ACTED" on the clip:** after the attempt is recorded, export a copy with the word drawn on top (`AVMutableVideoComposition` + `AVVideoCompositionCoreAnimationTool`) and hand that copy to Core. Part of M3. If it is not working by the Saturday 12:00 freeze, drop it and say in the video that acted clips are not marked; the on-screen "ACTED" during the call stays.

## Design constraints for an older user

- Large type and large touch targets. One action per screen.
- High contrast. Everything the avatar says is also on screen as text.
- The only tap required from her is "answer".
- All strings in one localisable file. The call language is still undecided (context §11, point 3).

## Technical notes [proposal]

- SwiftUI, **minimum iOS 17**, iPhone only, **portrait only**. Agent 3's package uses the same minimum.
- One `AVCaptureSession` with the front camera and the microphone, video and audio data outputs. Pass every buffer to Core with `ingest(video:)` and `ingest(audio:)`. Do no image analysis yourself.
- `ClipRecorder`: write each task attempt to a short mp4 (10 s or less, modest resolution). Hand the file URL to Core through `clipProvider`. Delete clips that were not used.
- Front camera preview is mirrored. That is fine for display. Do not flip or relabel anything that goes to Core.
- **`AVAudioSession` is configured by Core (Agent 3), not by the app.** Core knows when the avatar speaks and when to listen. The app never calls `AVAudioSession` itself and sets `captureSession.automaticallyConfiguresApplicationAudioSession = false`, so the capture session does not overwrite Core's setup.
- Permission prompts (camera, microphone, speech recognition) need plain usage strings that follow the claim rules.
- The ring works only while the app is open. That is accepted for the demo and we say so.

## Order of work

1. **Now:** create the Xcode project. Add the local package `ios/CheckInCore/` once Agent 3's first pull request is on `main` and you have merged `main` into your branch; until then code against the sketch in `SHARED_CONTRACT.md` §7.
2. **Tonight, for M1:** all Parent screens and the Family message screen running on `MockCheckInSession` in the Simulator. Role switch. Ring.
3. **Fri morning, for M2:** camera and microphone capture feeding Core on a real phone. Clip recording. Framing hint. Demo overlay with real numbers.
4. **Fri midday:** a build on the phone that a stranger aged 60+ can use without help. Watch one teammate who has not seen it try it first, in silence.
5. **Fri evening, for M3:** apply the design fully. Lab switch and "ACTED" mark. Avatar view framed properly in all three modes.
6. **Sat morning:** fix what Friday broke. Freeze at 12:00.

## Done means

- From a cold start, on a real iPhone, the whole Parent call runs with one tap.
- The Family phone shows the message within a few seconds of the call ending, and the clip plays.
- Every screen in the list exists and none has a dead control.
- A search of your strings finds none of the banned phrases in context §2.
- You have told your human plainly which parts run on the mock and which on real data.

## Risks to raise early

- The design artifact arrives late. Mitigation: unstyled screens first.
- No device or signing trouble. Raise it tonight, not Friday.
- The arm task with the phone in her hand. This is a design problem, not a code problem; flag it to the teammate drawing the design.
