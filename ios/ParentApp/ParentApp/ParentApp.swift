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
                             onAccept: { app.model?.answer() },
                             onDecline: {
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
        .task {
            // A phone that waits for calls on a stand must not lock.
            UIApplication.shared.isIdleTimerDisabled = true
            app.systemCalls.onAnswer = { app.model?.answer() }
            app.systemCalls.onDecline = {
                app.model?.decline()
                app.model?.dismissDone()
            }
            app.systemCalls.onHangUp = { app.model?.hangUp() }
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

    private func follow(_ mode: CallScreen.Mode) {
        switch mode {
        case .ringing(_, let ringTitle):
            title = ringTitle
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
        case .idle, .done:
            app.camera.stopPreview()
            app.systemCalls.reportEnded(unanswered: app.systemCalls.isRinging)
        }
    }

    /// After "Thank you": back to the iPhone home screen, like the end of a real call. The app keeps running in
    /// the background (BackgroundKeepAlive) and rings through CallKit next time. Demo only: `suspend` is not
    /// public API.
    private func leaveAfterCall() {
        UIApplication.shared.perform(#selector(NSXPCConnection.suspend))
        Task {
            // Reset behind the home-screen animation, so reopening shows the waiting screen.
            try? await Task.sleep(nanoseconds: 600_000_000)
            app.model?.dismissDone()
        }
    }
}
