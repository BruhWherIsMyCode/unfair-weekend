import CallKit
import Observation
import SwiftUI
import UIKit
import UserNotifications

/// The whole scene state. Local and hard-coded on purpose: this is a stage shell, not the product.
@MainActor
@Observable
final class DemoStore {
    enum Phase: String { case asUsual, alert, calling, declined }
    enum Screen { case home, alert }
    enum Overlay { case none, fakeFedyaCall, fake112 }

    // Scene
    private(set) var phase: Phase = .asUsual
    var screen: Screen = .home
    private(set) var overlay: Overlay = .none
    private(set) var alertTime = Date()
    private(set) var calledAt: Date?
    private(set) var declinedAt: Date?
    private(set) var armedFor: Date?

    // Demo panel settings
    var phoneNumber = "" { didSet { defaults.set(phoneNumber, forKey: Key.phone) } }
    var delaySeconds = 60 { didSet { defaults.set(delaySeconds, forKey: Key.delay) } }
    var fakeCallScreen = false { didSet { defaults.set(fakeCallScreen, forKey: Key.fakeCall) } }
    private(set) var notificationsAllowed: Bool?
    private(set) var timeSensitiveAllowed: Bool?

    static let delayPresets = [10, 30, 60, 120]
    static let fakeScreenSeconds: Double = 4

    @ObservationIgnored private let defaults = UserDefaults.standard
    @ObservationIgnored private let callMonitor = CallMonitor()
    @ObservationIgnored private var sawRealCall = false
    @ObservationIgnored private var fakeTimer: Task<Void, Never>?

    private enum Key {
        static let phone = "demo.phone", delay = "demo.delay", fakeCall = "demo.fakeCallScreen"
        static let phase = "scene.phase", alertTime = "scene.alertTime"
        static let calledAt = "scene.calledAt", declinedAt = "scene.declinedAt", armedFor = "scene.armedFor"
    }
    static let notificationId = "onas.fedya.alert"

    init() {
        phoneNumber = defaults.string(forKey: Key.phone) ?? ""
        delaySeconds = defaults.object(forKey: Key.delay) as? Int ?? 60
        fakeCallScreen = defaults.bool(forKey: Key.fakeCall)
        phase = Phase(rawValue: defaults.string(forKey: Key.phase) ?? "") ?? .asUsual
        alertTime = defaults.object(forKey: Key.alertTime) as? Date ?? Date()
        calledAt = defaults.object(forKey: Key.calledAt) as? Date
        declinedAt = defaults.object(forKey: Key.declinedAt) as? Date
        armedFor = defaults.object(forKey: Key.armedFor) as? Date
        // A real call cannot survive a relaunch; never come back stuck in "calling".
        if phase == .calling { phase = .alert }
        callMonitor.onChange = { [weak self] in self?.callsChanged() }
    }

    // MARK: Derived text

    var fedyaDigits: String {
        let trimmed = phoneNumber.trimmingCharacters(in: .whitespaces)
        let digits = trimmed.filter(\.isNumber)
        return trimmed.hasPrefix("+") ? "+" + digits : digits
    }

    /// Fedya's time in the "As usual" list: his last check-in.
    var usualTime: String { Clock.time(alertTime) }

    // MARK: Demo panel

    /// Schedules the notification and puts the app in the alert state.
    func arm() {
        resetScene()
        let fire = Date().addingTimeInterval(TimeInterval(delaySeconds))
        enterAlert(at: fire)
        armedFor = fire
        save()

        let content = UNMutableNotificationContent()
        content.title = String(localized: "Call Fedya now.")
        content.body = String(localized: "He seems different from his usual.")
        content.sound = UNNotificationSound(named: UNNotificationSoundName("alert.caf"))
        // Needs the Time Sensitive capability; without it iOS treats this as .active.
        content.interruptionLevel = .timeSensitive
        let trigger = UNTimeIntervalNotificationTrigger(timeInterval: TimeInterval(delaySeconds), repeats: false)
        let request = UNNotificationRequest(identifier: Self.notificationId, content: content, trigger: trigger)
        UNUserNotificationCenter.current().add(request)
    }

    /// Alert state now, no notification. For rehearsal.
    func showAlertNow() {
        resetScene()
        enterAlert(at: Date())
        save()
    }

    /// Back to the start: everyone as usual, nothing armed, notification cancelled.
    func reset() {
        resetScene()
        save()
    }

    private func resetScene() {
        let center = UNUserNotificationCenter.current()
        center.removePendingNotificationRequests(withIdentifiers: [Self.notificationId])
        center.removeDeliveredNotifications(withIdentifiers: [Self.notificationId])
        fakeTimer?.cancel()
        phase = .asUsual
        screen = .home
        overlay = .none
        calledAt = nil
        declinedAt = nil
        armedFor = nil
        sawRealCall = false
    }

    private func enterAlert(at time: Date) {
        alertTime = time
        phase = .alert
        screen = .home
    }

    // MARK: Scene actions

    func openAlert() { screen = .alert }
    func backToFamily() { screen = .home }

    /// The user tapped the notification: land on Home in its current state.
    func openedFromNotification() {
        guard overlay == .none else { return }
        screen = .home
    }

    func heIsFine() {
        fakeTimer?.cancel()
        phase = .asUsual
        screen = .home
        overlay = .none
        armedFor = nil
        save()
    }

    /// "Call Fedya" and "Call Fedya again". The only real call in the app.
    func callFedya() {
        calledAt = Date()
        declinedAt = nil
        let digits = fedyaDigits
        guard !fakeCallScreen, !digits.isEmpty, let url = URL(string: "tel:" + digits) else {
            startFakeFedyaCall()
            return
        }
        phase = .calling
        sawRealCall = false
        save()
        UIApplication.shared.open(url) { [weak self] ok in
            // Simulator or no telephony: fall back to the design's calling screen.
            if !ok { Task { @MainActor in self?.startFakeFedyaCall() } }
        }
    }

    /// "Call 112": never dials. Shows the calling screen marked as a demo, then back to the alert.
    func fakeCall112() {
        overlay = .fake112
        runFakeTimer { store in store.overlay = .none }
    }

    /// End button or tap on the fake Fedya calling screen.
    func endFakeCall() {
        switch overlay {
        case .fakeFedyaCall: finishCall()
        case .fake112: fakeTimer?.cancel(); overlay = .none
        case .none: break
        }
    }

    private func startFakeFedyaCall() {
        phase = .calling
        overlay = .fakeFedyaCall
        save()
        runFakeTimer { store in store.finishCall() }
    }

    private func runFakeTimer(_ done: @escaping @MainActor (DemoStore) -> Void) {
        fakeTimer?.cancel()
        fakeTimer = Task { [weak self] in
            try? await Task.sleep(for: .seconds(Self.fakeScreenSeconds))
            guard !Task.isCancelled, let self else { return }
            done(self)
        }
    }

    private func finishCall() {
        fakeTimer?.cancel()
        guard phase == .calling else { return }
        phase = .declined
        declinedAt = Date()
        overlay = .none
        screen = .home
        sawRealCall = false
        save()
    }

    // MARK: Real call end detection (CXCallObserver + scenePhase)

    private func callsChanged() {
        guard phase == .calling, overlay == .none else { return }
        if callMonitor.hasActiveCall { sawRealCall = true }
        else if sawRealCall { finishCall() }
    }

    func sceneBecameActive() {
        refreshNotificationSettings()
        guard phase == .calling, overlay == .none else { return }
        if callMonitor.hasActiveCall { sawRealCall = true; return }
        if sawRealCall { finishCall(); return }
        // Back without a call seen: either the call is about to start, or the
        // "Call ...?" prompt was cancelled. Look again shortly.
        Task { [weak self] in
            try? await Task.sleep(for: .seconds(2.5))
            guard let self, self.phase == .calling, self.overlay == .none else { return }
            if self.callMonitor.hasActiveCall { self.sawRealCall = true }
            else if self.sawRealCall { self.finishCall() }
            else if UIApplication.shared.applicationState == .active {
                self.phase = .alert
                self.save()
            }
        }
    }

    // MARK: Notifications

    func requestNotificationPermission() {
        UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound, .badge]) { [weak self] _, _ in
            Task { @MainActor in self?.refreshNotificationSettings() }
        }
    }

    func refreshNotificationSettings() {
        UNUserNotificationCenter.current().getNotificationSettings { [weak self] s in
            let allowed = s.authorizationStatus == .authorized
            let ts = s.timeSensitiveSetting == .enabled
            Task { @MainActor in
                self?.notificationsAllowed = allowed
                self?.timeSensitiveAllowed = ts
            }
        }
    }

    // MARK: Persistence

    private func save() {
        defaults.set(phase.rawValue, forKey: Key.phase)
        defaults.set(alertTime, forKey: Key.alertTime)
        defaults.set(calledAt, forKey: Key.calledAt)
        defaults.set(declinedAt, forKey: Key.declinedAt)
        defaults.set(armedFor, forKey: Key.armedFor)
    }
}

/// Observes system calls only. Needs no entitlement.
final class CallMonitor: NSObject, CXCallObserverDelegate {
    private let observer = CXCallObserver()
    var onChange: (@MainActor () -> Void)?

    override init() {
        super.init()
        observer.setDelegate(self, queue: .main)
    }

    var hasActiveCall: Bool { observer.calls.contains { !$0.hasEnded } }

    func callObserver(_ callObserver: CXCallObserver, callChanged call: CXCall) {
        MainActor.assumeIsolated { onChange?() }
    }
}
