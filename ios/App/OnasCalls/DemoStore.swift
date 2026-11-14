import CallKit
import Observation
import SwiftUI
import UIKit
import UserNotifications

/// The whole scene state. With backend settings (demo panel) a real alert from the check-in call drives it:
/// `liveMessage` holds the backend's text and `clip` her frames. Without them it is the local stage shell.
@MainActor
@Observable
final class DemoStore {
    enum Phase: String { case asUsual, alert, calling, declined }
    enum Screen { case home, alert, profile }
    enum Overlay { case none, fakeFedyaCall, fake112 }

    // Scene
    private(set) var phase: Phase = .asUsual
    var screen: Screen = .home
    /// Where "back" on the alert screen goes: Family or the profile it was opened from.
    private(set) var alertBack: Screen = .home
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

    /// The backend's alert for the current scene; nil for a staged (demo panel) alert.
    private(set) var liveMessage: FamilyMessage?
    /// Her frames for `liveMessage`, once downloaded.
    private(set) var clip: AlertClip?
    let feed = FamilyFeed()
    /// The alert's earlier check-in next to today's.
    let comparison = Comparison()

    // Profile
    var profile = PersonProfile() { didSet { ProfileStorage.save(profile) } }
    /// A photo picked on the profile screen.
    private(set) var customPhoto: UIImage?
    var photo: UIImage? { customPhoto ?? LocalAssets.fedyaPhoto }
    let history = CallHistory()
    /// The history entry of the alert on screen (staged or real).
    private(set) var currentRecordId: String?

    @ObservationIgnored private let defaults = UserDefaults.standard
    @ObservationIgnored private let keepAlive = KeepAlive()
    @ObservationIgnored private var seenMessages: Set<String> = []
    @ObservationIgnored private let callMonitor = CallMonitor()
    @ObservationIgnored private var sawRealCall = false
    @ObservationIgnored private var fakeTimer: Task<Void, Never>?

    private enum Key {
        static let phone = "demo.phone", delay = "demo.delay", fakeCall = "demo.fakeCallScreen"
        static let phase = "scene.phase", alertTime = "scene.alertTime"
        static let calledAt = "scene.calledAt", declinedAt = "scene.declinedAt", armedFor = "scene.armedFor"
        static let liveMessage = "scene.liveMessage", seen = "scene.seenMessages"
        static let currentRecord = "scene.currentRecord"
    }
    static let notificationId = "onas.fedya.alert"

    init() {
        profile = ProfileStorage.load()
        customPhoto = ProfileStorage.loadPhoto()
        currentRecordId = defaults.string(forKey: Key.currentRecord)
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
        liveMessage = defaults.data(forKey: Key.liveMessage).flatMap { try? JSONDecoder().decode(FamilyMessage.self, from: $0) }
        seenMessages = Set(defaults.stringArray(forKey: Key.seen) ?? [])
        #if DEBUG
        let args = ProcessInfo.processInfo.arguments
        if args.contains("-uitest.resetProfile") {
            profile = PersonProfile()
            customPhoto = nil
            ProfileStorage.savePhoto(nil)
        }
        if args.contains("-uitest.framesClip") { history.addTestFramesClip() }
        #endif
        callMonitor.onChange = { [weak self] in self?.callsChanged() }
        feed.onMessage = { [weak self] message in self?.receive(message) }
        feed.onResend = { [weak self] message in self?.renotify(message) }
    }

    // MARK: Backend

    /// Launch: listen for the backend's messages and stay alive in the background to hear them.
    func connect() {
        feed.start()
        if feed.isConfigured {
            keepAlive.start()
        }
        loadClipIfNeeded()
        loadComparison()
    }

    /// A family message from the check-in call. An alert (a change from her usual, or a concern) rings with a
    /// notification and opens the alert state with the backend's text; a later update of the same message (the
    /// final summary) only refreshes it. "Could not finish" and "no answer" are a plain notification.
    func receive(_ message: FamilyMessage) {
        let isNew = !seenMessages.contains(message.messageId)
        if isNew {
            seenMessages.insert(message.messageId)
            defaults.set(Array(seenMessages.suffix(200)), forKey: Key.seen)
        }
        var record = CallRecord(id: message.messageId, date: ISODate.parse(message.createdAt) ?? Date(),
                                outcome: .init(kind: message.kind), text: message.text, live: true)
        record.callId = message.callId
        history.upsert(record)
        if message.isAlert {
            if isNew {
                resetScene()
                enterAlert(at: Date())
                currentRecordId = message.messageId
                notify(title: callNow + ".", body: message.text, loud: true)
            }
            if isNew || liveMessage?.messageId == message.messageId {
                liveMessage = message
                save()
                loadClipIfNeeded()
                if isNew || comparison.callId != message.callId || comparison.phase == .legacy
                    || comparison.phase == .failed {
                    loadComparison()
                }
            }
        } else if message.asksToCall, isNew {
            notify(title: String(localized: "Daily check-in"), body: message.text, loud: false)
        }
    }

    /// Debug (`resend-alert.sh`): the earlier message's notification again, nothing else. No new alert, no history
    /// row, the screen stays as it is.
    func renotify(_ message: FamilyMessage) {
        if message.isAlert {
            notify(title: callNow + ".", body: message.text, loud: true)
        } else if message.asksToCall {
            notify(title: String(localized: "Daily check-in"), body: message.text, loud: false)
        }
    }

    private func loadClipIfNeeded() {
        guard let path = liveMessage?.clipUrl, clip?.url != path else { return }
        Task { [weak self] in
            guard let self else { return }
            var loaded = await self.feed.fetchClip(path)
            // The backend forgets the frames after 30 min or a restart: use the copy kept on this phone.
            if loaded == nil, let id = self.liveMessage?.messageId, let record = self.history.record(id),
               let kept = await Task.detached(operation: { CallHistory.loadFrames(record) }).value {
                loaded = AlertClip(url: path, frames: kept.frames, periodMs: kept.periodMs)
            }
            guard let loaded, let message = self.liveMessage, message.clipUrl == path else { return }
            self.clip = loaded
            // Kept on this phone: the backend forgets it after 30 min.
            self.history.saveClip(loaded, for: message.messageId)
        }
    }

    private func notify(title: String, body: String, loud: Bool) {
        let content = UNMutableNotificationContent()
        content.title = title
        content.body = body
        content.sound = loud ? UNNotificationSound(named: UNNotificationSoundName("alert.caf")) : .default
        content.interruptionLevel = loud ? .timeSensitive : .active
        let request = UNNotificationRequest(identifier: loud ? Self.notificationId : UUID().uuidString,
                                            content: content, trigger: nil)
        UNUserNotificationCenter.current().add(request)
    }

    /// The alert's clip area: the live call's attempt videos with an earlier check-in, or the staged clips.
    func loadComparison() {
        if let message = liveMessage {
            comparison.loadLive(callId: message.callId, feed: feed, history: history,
                                before: ISODate.parse(message.createdAt) ?? alertTime)
        } else if phase != .asUsual {
            comparison.loadStaged(at: alertTime)
        } else {
            comparison.clear()
        }
    }

    /// Opening the alert: try again when the clips failed or the backend had none yet (still encoding).
    func refreshComparisonIfStale() {
        if comparison.phase == .failed || comparison.phase == .legacy || comparison.phase == .idle {
            loadComparison()
        }
    }

    /// The profile: the backend's current state of every recent call's attempt videos (ready, expired…).
    func refreshAttempts(_ records: [CallRecord]) async {
        guard feed.isConfigured else { return }
        for record in records.prefix(15) {
            guard record.live, let id = record.callId, let clips = try? await feed.fetchCallClips(id) else { continue }
            history.setAttempts(clips, callId: id)
        }
    }

    // MARK: Derived text

    var name: String { profile.shownName }
    var pronoun: Pronoun { profile.pronoun }
    var callNow: String { String(localized: "Call \(name) now") }

    var fedyaDigits: String {
        let trimmed = phoneNumber.trimmingCharacters(in: .whitespaces)
        let digits = trimmed.filter(\.isNumber)
        return trimmed.hasPrefix("+") ? "+" + digits : digits
    }

    /// Fedya's time in the "As usual" row: the last check-in in the history.
    var usualTime: String { Clock.time(history.records.first?.date ?? alertTime) }

    // MARK: Demo panel

    /// Schedules the notification and puts the app in the alert state.
    func arm() {
        resetScene()
        let fire = Date().addingTimeInterval(TimeInterval(delaySeconds))
        enterAlert(at: fire)
        armedFor = fire
        recordStaged(at: fire)
        save()
        loadComparison()

        let content = UNMutableNotificationContent()
        content.title = callNow + "."
        content.body = pronoun.seemsDifferent
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
        recordStaged(at: Date())
        save()
        loadComparison()
    }

    /// The staged alert goes into the history too, with the staged report and clip.
    private func recordStaged(at date: Date) {
        let id = "staged-" + UUID().uuidString
        let text = [pronoun.seemedDifferent(name), pronoun.tiredLine, pronoun.unwellLine].joined(separator: " ")
        history.upsert(CallRecord(id: id, date: date, outcome: .notUsual, text: text, clip: .staged))
        currentRecordId = id
    }

    /// Demo panel: back to the sample week of calls.
    func resetHistory() {
        history.resetToSample()
        currentRecordId = nil
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
        // A rehearsal leaves no trace: the staged alert being reset leaves the history.
        if let id = currentRecordId, let record = history.record(id), !record.live {
            history.delete([id])
        }
        currentRecordId = nil
        phase = .asUsual
        screen = .home
        overlay = .none
        liveMessage = nil
        clip = nil
        comparison.clear()
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

    func openAlert() {
        alertBack = screen == .profile ? .profile : .home
        screen = .alert
    }
    func backFromAlert() { screen = alertBack }
    func backToFamily() { screen = .home }
    func openProfile() { screen = .profile }

    // MARK: Profile

    func saveProfile(_ new: PersonProfile, phone: String, photo: UIImage?, photoChanged: Bool) {
        profile = new
        phoneNumber = phone
        if photoChanged {
            let fitted = photo?.fitted(to: 800)
            ProfileStorage.savePhoto(fitted)
            customPhoto = fitted
        }
    }

    /// The user tapped the notification: land on Home in its current state.
    func openedFromNotification() {
        guard overlay == .none else { return }
        screen = .home
    }

    func heIsFine() {
        history.update(currentRecordId) { $0.markedFineAt = Date() }
        currentRecordId = nil
        fakeTimer?.cancel()
        liveMessage = nil
        clip = nil
        comparison.clear()
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
        history.update(currentRecordId) { $0.calledAt = Date() }
        let digits = fedyaDigits
        guard !fakeCallScreen, Self.isDialable(digits), let url = URL(string: "tel:" + digits) else {
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

    /// A personal number, never a short or emergency code (112, 911, 999…): at least 6 digits.
    static func isDialable(_ digits: String) -> Bool { digits.filter(\.isNumber).count >= 6 }

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

    // MARK: Real call end (CXCallObserver + scenePhase)

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
        defaults.set(liveMessage.flatMap { try? JSONEncoder().encode($0) }, forKey: Key.liveMessage)
        defaults.set(currentRecordId, forKey: Key.currentRecord)
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
