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
                InCallView(screen: screen, title: title, camera: app.camera) { app.model?.hangUp() }
                    .transition(.asymmetric(insertion: .opacity.animation(Motion.glide(0.7)),
                                            removal: .opacity.animation(Motion.glide(0.6))))
            case .done:
                DoneView { app.model?.dismissDone() }
                    .transition(.asymmetric(insertion: .opacity.animation(Motion.glide(0.6).delay(0.2)),
                                            removal: .opacity.animation(Motion.glide(0.6))))
            }
        }
        .animation(Motion.glide(0.6), value: step(screen.mode))
        .preferredColorScheme(.dark)
        .sheet(isPresented: $showSettings) {
            SettingsView(initial: app.settings) { app.update($0) }
        }
        .onChange(of: screen.mode, initial: true) { _, mode in
            follow(mode)
        }
        .task {
            // A phone that waits for calls on a stand must not lock.
            UIApplication.shared.isIdleTimerDisabled = true
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
            app.camera.stopPreview()
        case .inCall:
            app.camera.startPreview()
        case .ended:
            // Hang-up, deadline or lost connection: no result for her, straight back to waiting.
            app.camera.stopPreview()
            app.model?.dismissDone()
        case .idle, .done:
            app.camera.stopPreview()
        }
    }
}
