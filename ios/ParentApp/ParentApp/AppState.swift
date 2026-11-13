import AVFoundation
import Foundation
import Observation
import ParentCallKit

/// Backend address and demo key, set on the hidden settings screen. Stored in UserDefaults on the
/// phone only, never in the repo. Launch arguments such as `-parent.baseURL http://10.0.0.5:8000`
/// set them, and are kept as if typed on the settings screen (so a developer can set up a phone from a Mac).
struct AppSettings: Equatable {
    var baseURL: String
    var demoKey: String
    var personId: String

    private static let baseURLKey = "parent.baseURL"
    private static let demoKeyKey = "parent.demoKey"
    private static let personIdKey = "parent.personId"

    static func load() -> AppSettings {
        let defaults = UserDefaults.standard
        let passed = defaults.volatileDomain(forName: UserDefaults.argumentDomain)
        for key in [baseURLKey, demoKeyKey, personIdKey] {
            if let value = passed[key] as? String {
                defaults.set(value, forKey: key)  // keep it after this run
            }
        }
        return AppSettings(baseURL: defaults.string(forKey: baseURLKey) ?? "",
                           demoKey: defaults.string(forKey: demoKeyKey) ?? "",
                           personId: defaults.string(forKey: personIdKey) ?? "p_demo")
    }

    func save() {
        let defaults = UserDefaults.standard
        defaults.set(baseURL, forKey: Self.baseURLKey)
        defaults.set(demoKey, forKey: Self.demoKeyKey)
        defaults.set(personId, forKey: Self.personIdKey)
    }

    var url: URL? {
        let trimmed = baseURL.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let url = URL(string: trimmed), url.scheme == "http" || url.scheme == "https", url.host != nil else {
            return nil
        }
        return url
    }

    var isComplete: Bool {
        url != nil && !demoKey.isEmpty && !personId.isEmpty
    }
}

/// Owns the CallModel and the phone's devices. The screens read `model.screen` and nothing else.
@MainActor
@Observable
final class AppState {
    enum Connection: Equatable {
        case notSetUp
        case connecting
        case waiting
    }

    private(set) var settings = AppSettings.load()
    private(set) var model: CallModel?
    private(set) var connection: Connection = .notSetUp
    let camera = SelfViewCamera()
    /// Plays the backend's prepared avatar clips; the call screen shows its video as the avatar.
    private(set) var clips: AvatarClips?
    /// Simli's live avatar, tried first for every line; nil in the Simulator unless `-parent.liveAvatar YES`.
    private(set) var live: SimliLiveAvatar?
    /// Frames for the camera debug screen: the front camera on a phone, the fake camera in the Simulator.
    private(set) var debugFrames: CameraSource?
    /// Rings through the system call screen while the app is in the background.
    let systemCalls = SystemCalls()
    @ObservationIgnored private let keepAlive = BackgroundKeepAlive()

    @ObservationIgnored private var devices: ParentDevices?
    @ObservationIgnored private var connectTask: Task<Void, Never>?
    @ObservationIgnored private var permissions: (camera: Bool, microphone: Bool) = (true, true)

    /// First launch: permissions, then connect if settings exist.
    func launch() async {
        permissions = await PhoneDevices.requestPermissions()
        if permissions.camera {
            camera.prepare()
        }
        #if targetEnvironment(simulator)
        // The Simulator has no front camera, and its voice-chat audio unit aborts on start
        // (AURemoteIO RPC timeout). Fake devices there, except the clip player (AVPlayer works there),
        // so the screens and the avatar clips can still be walked through.
        // No keep-alive here either: it needs the same voice-chat audio session.
        let fake = FakeDevices.make()
        let clips = PhoneDevices.makeClips()
        let live = UserDefaults.standard.bool(forKey: "parent.liveAvatar") ? SimliLiveAvatar() : nil
        devices = ParentDevices(player: fake.player, recorder: fake.recorder, camera: fake.camera,
                                microphone: fake.microphone, clips: clips, live: live)
        debugFrames = fake.camera
        #else
        let live = SimliLiveAvatar()
        let phone = PhoneDevices.make(live: live)
        let clips = phone.clips as? AvatarClips
        devices = ParentDevices(player: phone.player, recorder: phone.recorder, camera: camera,
                                microphone: phone.microphone, clips: clips, live: live)
        debugFrames = camera
        // Keeps the app (and its event stream) alive in the background, so a call can ring there.
        keepAlive.start()
        #endif
        self.clips = clips
        self.live = live
        camera.setAvailabilityHandler { [weak self] available, problem in
            Task { @MainActor in
                self?.model?.resourceChanged(.camera, available: available, problem: problem)
            }
        }
        observeAudioInterruptions()
        connect()
    }

    func update(_ newSettings: AppSettings) {
        newSettings.save()
        settings = newSettings
        connect()
    }

    /// Registers with the backend, retrying every few seconds until it answers.
    private func connect() {
        connectTask?.cancel()
        let old = model
        model = nil
        guard let devices, settings.isComplete, let url = settings.url else {
            connection = .notSetUp
            Task { await old?.stop() }
            return
        }
        connection = .connecting
        let settings = settings
        let permissions = permissions
        connectTask = Task { [weak self] in
            await old?.stop()
            while !Task.isCancelled {
                let candidate = CallModel(api: CallAPI(baseURL: url, demoKey: settings.demoKey),
                                          personId: settings.personId, devices: devices)
                await candidate.start()
                if Task.isCancelled {
                    await candidate.stop()
                    return
                }
                if candidate.lastError == nil {
                    // Remembered by the client and reported when she answers.
                    if !permissions.camera {
                        candidate.resourceChanged(.camera, available: false, problem: .permissionDenied)
                    }
                    if !permissions.microphone {
                        candidate.resourceChanged(.microphone, available: false, problem: .permissionDenied)
                    }
                    self?.model = candidate
                    self?.connection = .waiting
                    return
                }
                try? await Task.sleep(nanoseconds: 3_000_000_000)
            }
        }
    }

    private func observeAudioInterruptions() {
        NotificationCenter.default.addObserver(forName: AVAudioSession.interruptionNotification, object: nil,
                                               queue: .main) { [weak self] note in
            guard let raw = note.userInfo?[AVAudioSessionInterruptionTypeKey] as? UInt,
                  let type = AVAudioSession.InterruptionType(rawValue: raw) else { return }
            let available = type == .ended
            MainActor.assumeIsolated {
                self?.model?.resourceChanged(.microphone, available: available,
                                             problem: available ? nil : .trackEnded)
            }
        }
    }
}
