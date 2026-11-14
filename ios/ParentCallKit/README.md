# ParentCallKit: the parent (бабушка) app's call engine

The phone that gets called. This package contains no UI. It registers the phone with the call service, listens
for the ring, and carries out the backend's commands. It plays each fixed line and reports when playback has
ended, records answers, and streams camera frames or microphone chunks during a task. It also reports when the
camera or microphone is lost. The design team builds the screens on top of `CallModel.phase`.

## Pieces

- `CallClient` (actor): the engine. `start()`, `answer()`, `decline()`, `hangUp()`, `resourceChanged(...)`.
- `CallModel` (`@Observable`, SwiftUI): wraps the client for views. Bind to `phase`:
  `idle`, `ringing`, `connecting`, `speaking(caption)`, `listening(questionId)`,
  `task(name, secondsLeft, hint)`, `finishing`, `ended(reason)`. Phases never carry a measurement or a verdict.
- `ParentDevices`: injected protocols for the speech player, answer recorder, camera and microphone.
  - `PhoneDevices.make()` (`AVDevices.swift`): AVFoundation implementations, including `AvatarClips`.
- Avatar clips: a `say` command may carry `video_path`, a prepared Simli MP4 with the line's own audio.
  `CallClient` downloads it with the demo key and plays it through `devices.clips` (AVPlayer; show
  `AvatarClips.player` full screen). `playback.completed` is sent only on `AVPlayerItemDidPlayToEndTime`. A failed
  download or playback stops the clip and falls back to the PCM `audio_path`, so both are never heard together.
  Cancel, hang-up and teardown stop the clip and cancel its download. Without a clip player, lines play as PCM.
- Live avatar: `devices.live` (`LiveAvatarPlayer`, implemented in the app as `SimliLiveAvatar` over LiveKit). After
  answering, `CallClient` asks `POST /v1/calls/{id}/avatar/live` for a Simli session and connects in the background.
  Each line streams its PCM to Simli, including Claude's newly generated remarks and clarification questions.
  Live mode remains the backend default (`AVATAR_MODE=live`); prepared clips are the fallback.
  A line can fall back immediately only when the live player reports `LivePlaybackFailure.notStarted`, before
  attempting to send audio. A partial or uncertain live failure ends that command with `playback_failed` and
  drops live for future commands; it never replays the current line as a clip or reports it completed.
  Playback acknowledgments are separate from playback: bounded acknowledgment retries send the same event
  without replaying audio. Native playback diagnostics use the `live-avatar` log category and omit tokens,
  provider URLs, text and media. The app must be rebuilt to use this policy.
- `BackgroundKeepAlive`: demo workaround, not for the App Store. A silent audio loop keeps the app running in the
  background so the event stream stays open; the app then rings through CallKit (no VoIP push).
  - The loop is restarted by a 5 s watchdog timer, by interruption-ended (even without "should resume"), route
    changes, media-services reset, the app going to the background or inactive, the end of every call, and CallKit
    deactivating the audio session. To see restarts, filter Console.app by category "keepalive".
  - Limit: if iOS ends the app (low memory, phone restart, force quit from the app switcher) it cannot ring until
    opened again. The full fix is a VoIP push (PushKit), which needs a paid Apple developer account (a free team
    has no push entitlement) and the backend.
  - Right after a CallKit call the system still holds the audio, so reactivation can fail ("insufficient
    priority"); the loop then retries twice a second under a background task until the session is back. Every app
    transition (`ensureRunning(force: true)`) stops and replays the loop, because after CallKit deactivated the
    session the player can report "playing" while silent, and iOS then suspends the app.
  - Tried on an iPhone (iOS 27, free team, 14 Nov): background for minutes, ring through CallKit, answer from the
    banner (the app comes to the front with the camera), a missed call in the background, then the next call
    rings again.
  - `FakeDevices.make()`: synthetic input for tests, previews and the simulator executable.
- `Ringer`: the ring the app plays itself on its own Accept/Decline screen while the app is in the foreground.
  `start()` loops a generated two-tone ring (about 2 s of ringing, then 2 s of silence) on the loudspeaker and
  vibrates once per ring; `stop()` stops both. It leaves the audio session and `BackgroundKeepAlive` alone. The app
  rings only while the screen is `.ringing`, the app is in front and CallKit is not ringing. If the app goes to the
  background while ringing, the ring is handed to CallKit. Written, not compiled, not tried on a device.
- With the phone unlocked and another app (or the home screen) in front, iOS shows a CallKit call as a small
  banner. For the full-screen Accept/Decline call screen, set Settings > Apps > Phone > Incoming Calls > Full Screen
  on the parent's phone (on the lock screen the call is always full screen).
- `parent-call-sim`: a command-line run of the real client against a backend, using fake devices.

## Minimal screen wiring

```swift
@State var model = CallModel(api: CallAPI(baseURL: URL(string: "http://<mac-ip>:8000")!, demoKey: "<DEMO_KEY>"),
                             personId: "p_demo", devices: PhoneDevices.make())

var body: some View {
    content.task {
        _ = await PhoneDevices.requestPermissions()
        await model.start()
    }
}
```

`model.answer()` / `model.decline()` on the ring screen. `model.hangUp()` stays visible for the whole call.
Report camera or microphone interruptions with `model.resourceChanged(.camera, available: false, problem: .trackEnded)`.
Info.plist needs `NSCameraUsageDescription` and `NSMicrophoneUsageDescription`. For the local backend it
also needs an ATS exception for plain HTTP.

## Protocol

This package follows `contract/mvp.openapi.yaml` (draft 0.2) and the routes in `backend/checkin_backend/app.py`:
`POST /v1/devices`, SSE `GET /v1/devices/{id}/events` (`call.incoming`, `command.execute`, `command.cancel`,
`task.progress`, `call.ended`), `POST /v1/calls/{id}/answer|decline|client-events`, `GET .../speech/{cmd}`,
`POST .../captures/{cmd}`, `POST .../attempts/{attempt}/samples|finish`.

## Check

```bash
cd ios/ParentCallKit
swift build && swift test          # Linux or macOS; AVDevices/CallModel are skipped without AVFoundation/SwiftUI
# with the backend running (backend/README.md):
DEMO_KEY=... .build/debug/parent-call-sim http://127.0.0.1:8000 p_demo 0.05
# then in another terminal: curl -XPOST -H "X-Demo-Key: $DEMO_KEY" -H 'Content-Type: application/json' \
#   -d '{"person_id":"p_demo"}' http://127.0.0.1:8000/v1/calls
```

## Status

- Built and run on Linux (Swift 6.1): the package build, 3 unit tests (SSE parsing, command decoding, fake
  media), and the simulator executable. In one simulator run it rang, answered and followed every
  say/listen/task command from the real backend + Agent 3's CallSession, through to `call.ended`.
- Written, not compiled: `AVDevices.swift` (real audio/camera/mic) and `CallModel.swift`. They need a Mac
  with Xcode. Check them on a real iPhone: playback-end timing, frame rotation and mirroring, PCM
  conversion, and the silence cut-off.
- Not done: the UI (design in progress), the Simli avatar video, background ring / CallKit.
