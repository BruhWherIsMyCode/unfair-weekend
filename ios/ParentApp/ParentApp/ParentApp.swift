import ParentCallKit
import SwiftUI

@main
struct ParentApp: App {
    var body: some Scene {
        WindowGroup {
            RootView()
        }
    }
}

/// Picks the screen from `model.screen.mode` and nothing else. Screens crossfade; nothing slides.
struct RootView: View {
    @State private var app = AppState()
    @State private var showSettings = false
    @State private var showCameraDebug = false
    @State private var cameraDebugAfterSettings = false
    /// The ring title ("Daily check-in"), kept for the pill during the call.
    @State private var title = "Daily check-in"
    @Environment(\.scenePhase) private var scenePhase

    private enum Step: Equatable {
        case idle, ringing, call, done
    }

    var body: some View {
        let screen = app.model?.screen ?? CallScreen()
        ZStack {
            Palette.phone.ignoresSafeArea()

            switch step(screen.mode) {
            case .idle:
                IdleView(connection: app.connection) { showSettings = true }
                    .transition(.opacity.animation(Motion.glide(0.6)))
            case .ringing:
                IncomingView(title: title,
                             onAccept: {
                                 app.ringer.stop()
                                 app.model?.answer()
                             },
                             onDecline: {
                                 app.ringer.stop()
                                 app.model?.decline()
                                 // The engine does not move the screen on decline: go back to waiting here.
                                 app.model?.dismissDone()
                             })
                    .transition(.opacity.animation(Motion.glide(0.6)))
            case .call:
                InCallView(screen: screen, title: title, camera: app.camera, clips: app.clips?.player,
                           live: app.live?.feed) {
                    app.model?.hangUp()
                }
                    .transition(.asymmetric(insertion: .opacity.animation(Motion.glide(0.7)),
                                            removal: .opacity.animation(Motion.glide(0.6))))
            case .done:
                DoneView { leaveAfterCall() }
                    .transition(.asymmetric(insertion: .opacity.animation(Motion.glide(0.6).delay(0.2)),
                                            removal: .opacity.animation(Motion.glide(0.6))))
            }
        }
        .animation(Motion.glide(0.6), value: step(screen.mode))
        .preferredColorScheme(.dark)
        .sheet(isPresented: $showSettings, onDismiss: {
            if cameraDebugAfterSettings {
                cameraDebugAfterSettings = false
                showCameraDebug = true
            }
        }) {
            SettingsView(initial: app.settings, onSave: { app.update($0) },
                         onCameraDebug: { cameraDebugAfterSettings = true })
        }
        .fullScreenCover(isPresented: $showCameraDebug) {
            CameraDebugView(model: CameraDebugModel(settings: app.settings, preview: app.camera,
                                                    frames: app.debugFrames)) {
                showCameraDebug = false
            }
        }
        .onChange(of: screen.mode, initial: true) { _, mode in
            follow(mode)
        }
        .onChange(of: scenePhase) { _, phase in
            followScene(phase)
        }
        .task {
            // A phone that waits for calls on a stand must not lock.
            UIApplication.shared.isIdleTimerDisabled = true
            app.systemCalls.onAnswer = { app.model?.answer() }
            app.systemCalls.onDecline = {
                app.model?.decline()
                app.model?.dismissDone()
            }
            app.systemCalls.onHangUp = { app.model?.hangUp() }
            app.systemCalls.onAudioDeactivated = { app.keepAwake(reason: "callkit-deactivated") }
            await app.launch()
        }
    }

    private func step(_ mode: CallScreen.Mode) -> Step {
        switch mode {
        case .idle, .ended: return .idle
        case .ringing: return .ringing
        case .inCall: return .call
        case .done: return .done
        }
    }

    /// The app's own ring plays only while the ring screen is up, the app is in front and CallKit is not ringing.
    private func syncRing(_ mode: CallScreen.Mode) {
        if case .ringing = mode, scenePhase == .active, !app.systemCalls.isRinging {
            app.ringer.start()
        } else {
            app.ringer.stop()
        }
    }

    /// Leaving the foreground while ringing: stop the app ring and let CallKit keep ringing.
    /// Coming back to the front: ring on the app's own screen again, unless CallKit still rings.
    private func followScene(_ phase: ScenePhase) {
        let mode = (app.model?.screen ?? CallScreen()).mode
        Diag.log("scene \(phase) connection=\(app.connection)")
        if phase == .background || phase == .inactive {
            app.keepAwake(reason: "scene-\(phase == .background ? "background" : "inactive")")
        }
        if phase == .background, case .ringing = mode {
            app.ringer.stop()
            app.systemCalls.reportIncoming(title: title)
        }
        syncRing(mode)
    }

    private func follow(_ mode: CallScreen.Mode) {
        defer { syncRing(mode) }
        Diag.log("call screen \(mode) (app \(Diag.appStateNow))")
        switch mode {
        case .ringing(_, let ringTitle):
            title = ringTitle
            // Clips hold their last frame between lines. A new call must not show the previous call's avatar
            // while its own live stream or first prepared clip is connecting.
            app.clips?.player.pause()
            app.clips?.player.replaceCurrentItem(with: nil)
            // A real call wins over the debug screen.
            showCameraDebug = false
            app.camera.stopPreview()
            // In the background nobody sees the app's ring screen: ring through the system call screen.
            if UIApplication.shared.applicationState != .active {
                app.systemCalls.reportIncoming(title: ringTitle)
            }
        case .inCall:
            app.camera.startPreview()
            // Answered on the app's own ring screen while the system one was still ringing.
            if app.systemCalls.isRinging {
                app.systemCalls.reportEnded(unanswered: false)
            }
        case .ended:
            // Hang-up, deadline or lost connection: no result for her, straight back to waiting.
            app.camera.stopPreview()
            app.systemCalls.reportEnded(unanswered: app.systemCalls.isRinging)
            app.model?.dismissDone()
            keepAwakeAfterCall(reason: "call-ended")
        case .idle, .done:
            app.camera.stopPreview()
            app.systemCalls.reportEnded(unanswered: app.systemCalls.isRinging)
            keepAwakeAfterCall(reason: "call-idle-or-done")
        }
    }

    /// End of every call (answered, declined or missed): restart the silent loop now, and once more after the
    /// live avatar has had time to disconnect (that disconnect can deactivate the audio session).
    private func keepAwakeAfterCall(reason: String) {
        app.keepAwake(reason: reason)
        Task {
            try? await Task.sleep(nanoseconds: 2_000_000_000)
            app.keepAwake(reason: "\(reason)-after-disconnect")
        }
    }

    /// After "Thank you": back to the iPhone home screen, like the end of a real call. The app keeps running in
    /// the background (BackgroundKeepAlive) and rings through CallKit next time. Demo only: `suspend` is not
    /// public API.
    private func leaveAfterCall() {
        app.keepAwake(reason: "leave-after-call")
        Diag.log("leaving to the home screen (suspend)")
        UIApplication.shared.perform(#selector(NSXPCConnection.suspend))
        Task {
            // Reset behind the home-screen animation, so reopening shows the waiting screen.
            try? await Task.sleep(nanoseconds: 600_000_000)
            app.model?.dismissDone()
        }
    }
}
