import SwiftUI
import UserNotifications

@main
struct OnasCallsApp: App {
    @UIApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate
    @Environment(\.scenePhase) private var scenePhase

    var body: some Scene {
        WindowGroup {
            RootView()
                .environment(appDelegate.store)
        }
        .onChange(of: scenePhase) { _, phase in
            // Keep the screen awake while the app is in front.
            UIApplication.shared.isIdleTimerDisabled = (phase == .active)
            if phase == .active { appDelegate.store.sceneBecameActive() }
        }
    }
}

final class AppDelegate: NSObject, UIApplicationDelegate, UNUserNotificationCenterDelegate {
    @MainActor let store = DemoStore()

    func application(_ application: UIApplication,
                     didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]? = nil) -> Bool {
        UNUserNotificationCenter.current().delegate = self
        MainActor.assumeIsolated {
            store.requestNotificationPermission()
            store.connect()
        }
        return true
    }

    /// Rehearsal with the app open: still show the banner and play the sound.
    func userNotificationCenter(_ center: UNUserNotificationCenter,
                                willPresent notification: UNNotification) async -> UNNotificationPresentationOptions {
        [.banner, .list, .sound]
    }

    /// Tap on the notification: open on Home in the alert state.
    func userNotificationCenter(_ center: UNUserNotificationCenter,
                                didReceive response: UNNotificationResponse) async {
        await MainActor.run { store.openedFromNotification() }
    }
}

struct RootView: View {
    @Environment(DemoStore.self) private var store

    var body: some View {
        ZStack {
            Palette.page.ignoresSafeArea()
            switch store.screen {
            case .home:
                HomeView()
                    .transition(.move(edge: .leading))
            case .alert:
                AlertView()
                    .transition(.move(edge: .trailing))
            }
            if store.overlay != .none {
                CallingView()
                    .transition(.opacity)
                    .zIndex(1)
            }
        }
        // Light status bar only on the dark calling screen. All app colours are explicit.
        .preferredColorScheme(store.overlay == .none ? .light : .dark)
        .animation(.easeInOut(duration: 0.3), value: store.screen)
        .animation(.easeInOut(duration: 0.25), value: store.overlay)
    }
}
