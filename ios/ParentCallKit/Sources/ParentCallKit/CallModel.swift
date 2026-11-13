#if canImport(SwiftUI)
import Foundation
import Observation

/// What a SwiftUI screen binds to. The design owns the views; this owns nothing visual.
///
///     @State var model = CallModel(api: CallAPI(baseURL: url, demoKey: key), personId: "p_demo",
///                                  devices: PhoneDevices.make())
///     .task { await model.start() }
///     switch model.phase { case .ringing: RingView(onAnswer: model.answer, onDecline: model.decline) ... }
@MainActor
@Observable
public final class CallModel {
    public private(set) var phase: CallPhase = .idle
    public private(set) var lastError: String?
    private var client: CallClient?
    private let api: CallAPI
    private let personId: String
    private let deviceId: String
    private let devices: ParentDevices

    public init(api: CallAPI, personId: String, devices: ParentDevices, deviceId: String? = nil) {
        self.api = api
        self.personId = personId
        self.devices = devices
        self.deviceId = deviceId ?? CallModel.storedDeviceId()
    }

    public func start() async {
        let client = CallClient(api: api, deviceId: deviceId, personId: personId, devices: devices) { [weak self] phase in
            Task { @MainActor in
                self?.phase = phase
            }
        }
        self.client = client
        do {
            try await client.start()
        } catch {
            lastError = "Could not reach the check-in service."
        }
    }

    public func answer() {
        Task {
            do {
                try await client?.answer()
            } catch {
                lastError = "Could not connect the call."
            }
        }
    }

    public func decline() {
        Task { try? await client?.decline() }
    }

    public func hangUp() {
        Task { await client?.hangUp() }
    }

    public func resourceChanged(_ resource: Resource, available: Bool, problem: ResourceProblem? = nil) {
        Task { await client?.resourceChanged(resource, available: available, problem: problem) }
    }

    private static func storedDeviceId() -> String {
        let key = "parent.device_id"
        if let id = UserDefaults.standard.string(forKey: key) {
            return id
        }
        let id = "dev_" + UUID().uuidString.replacingOccurrences(of: "-", with: "").prefix(16).lowercased()
        UserDefaults.standard.set(id, forKey: key)
        return id
    }
}
#endif
