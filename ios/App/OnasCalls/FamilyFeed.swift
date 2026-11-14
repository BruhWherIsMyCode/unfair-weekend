import Foundation
import Observation
import UIKit

/// One family message from the backend (`message.new`, backend/checkin_backend/delivery.py). A mid-call change and
/// the final summary share `messageId`: upsert by it.
struct FamilyMessage: Codable, Equatable {
    let messageId: String
    let callId: String
    let personId: String
    let kind: String
    let text: String
    let phase: String
    let clipUrl: String?
    let createdAt: String
    let updatedAt: String

    /// The kinds that put the app in the alert state (ring, amber card, report and clip).
    var isAlert: Bool { kind == "change_noticed" || kind == "concern_raised" }
    /// Asks the family to call, but no change was seen: a plain notification.
    var asksToCall: Bool { kind == "incomplete" || kind == "no_answer" }
}

/// The alert clip: the camera frames of her tries at the task that showed the change, played as a flipbook.
struct AlertClip: Equatable {
    let url: String
    let frames: [UIImage]
    let periodMs: Int

    var seconds: Int { max(1, Int((Double(frames.count * periodMs) / 1000).rounded())) }
}

/// The caretaker phone's link to the check-in backend: registers as a family device, keeps the event stream open
/// (reconnecting after drops) and hands every `message.new` to `onMessage`. Settings live in the demo panel.
@MainActor
@Observable
final class FamilyFeed {
    enum Status: Equatable { case off, connecting, listening, failed(String) }

    var baseURL: String { didSet { defaults.set(baseURL, forKey: Key.baseURL) } }
    var demoKey: String { didSet { defaults.set(demoKey, forKey: Key.demoKey) } }
    var familyId: String { didSet { defaults.set(familyId, forKey: Key.familyId) } }
    private(set) var status: Status = .off

    @ObservationIgnored var onMessage: (FamilyMessage) -> Void = { _ in }
    /// Debug (`resend-alert.sh`): an earlier message whose notification should show again.
    @ObservationIgnored var onResend: (FamilyMessage) -> Void = { _ in }
    @ObservationIgnored private let defaults = UserDefaults.standard
    @ObservationIgnored private var loop: Task<Void, Never>?
    @ObservationIgnored private var lastEventId: String?

    private enum Key {
        static let baseURL = "family.baseURL", demoKey = "family.demoKey", familyId = "family.familyId"
        static let deviceId = "family.deviceId"
    }

    init() {
        // Launch arguments (`-family.baseURL https://…`) are kept as if typed in the demo panel.
        let passed = defaults.volatileDomain(forName: UserDefaults.argumentDomain)
        for key in [Key.baseURL, Key.demoKey, Key.familyId] {
            if let value = passed[key] as? String {
                defaults.set(value, forKey: key)
            }
        }
        baseURL = defaults.string(forKey: Key.baseURL) ?? ""
        demoKey = defaults.string(forKey: Key.demoKey) ?? ""
        familyId = defaults.string(forKey: Key.familyId) ?? "f_demo"
    }

    var deviceId: String {
        if let id = defaults.string(forKey: Key.deviceId) { return id }
        let id = "dev_family_" + UUID().uuidString.replacingOccurrences(of: "-", with: "").prefix(12).lowercased()
        defaults.set(id, forKey: Key.deviceId)
        return id
    }

    var url: URL? {
        let trimmed = baseURL.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let url = URL(string: trimmed), url.scheme == "http" || url.scheme == "https", url.host != nil else {
            return nil
        }
        return url
    }

    var isConfigured: Bool { url != nil && !demoKey.isEmpty && !familyId.isEmpty }

    /// (Re)starts listening with the current settings. Without settings the app stays a local stage shell.
    func start() {
        loop?.cancel()
        lastEventId = nil
        guard isConfigured, let base = url else {
            status = .off
            return
        }
        status = .connecting
        let key = demoKey, family = familyId, device = deviceId
        loop = Task { [weak self] in
            while !Task.isCancelled {
                do {
                    try await Self.register(base: base, key: key, device: device, family: family)
                    self?.status = .listening
                    try await self?.listen(base: base, key: key, device: device)
                } catch is CancellationError {
                    return
                } catch {
                    self?.status = .failed(Self.describe(error))
                }
                try? await Task.sleep(for: .seconds(3))
            }
        }
    }

    // MARK: HTTP

    private static func request(_ base: URL, _ path: String, key: String) -> URLRequest {
        var request = URLRequest(url: URL(string: path, relativeTo: base)!.absoluteURL)
        request.setValue(key, forHTTPHeaderField: "X-Demo-Key")
        request.timeoutInterval = 20
        return request
    }

    private static func register(base: URL, key: String, device: String, family: String) async throws {
        var request = request(base, "/v1/devices", key: key)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONSerialization.data(withJSONObject: [
            "device_id": device, "role": "family", "family_id": family,
        ])
        let (_, response) = try await URLSession.shared.data(for: request)
        try check(response)
    }

    /// Server-sent events. Each frame is `id:`, `event:` and a one-line JSON `data:`, so an event is complete at
    /// its `data:` line.
    private func listen(base: URL, key: String, device: String) async throws {
        var request = Self.request(base, "/v1/devices/\(device)/events", key: key)
        request.setValue("text/event-stream", forHTTPHeaderField: "Accept")
        request.timeoutInterval = 3600
        if let lastEventId {
            request.setValue(lastEventId, forHTTPHeaderField: "Last-Event-ID")
        }
        let (bytes, response) = try await URLSession.shared.bytes(for: request)
        try Self.check(response)
        var event = "message"
        for try await line in bytes.lines {
            if line.hasPrefix("id:") {
                lastEventId = line.dropFirst(3).trimmingCharacters(in: .whitespaces)
            } else if line.hasPrefix("event:") {
                event = line.dropFirst(6).trimmingCharacters(in: .whitespaces)
            } else if line.hasPrefix("data:") {
                let data = Data(line.dropFirst(5).trimmingCharacters(in: .whitespaces).utf8)
                if event == "message.new", let message = try? Self.decoder.decode(FamilyMessage.self, from: data) {
                    onMessage(message)
                } else if event == "message.resend",
                          let message = try? Self.decoder.decode(FamilyMessage.self, from: data) {
                    onResend(message)
                }
                event = "message"
            }
        }
        throw URLError(.networkConnectionLost)
    }

    /// Downloads the alert clip (`GET /v1/clips/{id}`). Nil when it is gone (clips expire after 30 min) or broken.
    func fetchClip(_ path: String) async -> AlertClip? {
        guard let base = url else { return nil }
        var request = Self.request(base, path, key: demoKey)
        request.timeoutInterval = 30
        struct Body: Decodable {
            let periodMs: Int
            let frames: [String]
        }
        guard let (data, response) = try? await URLSession.shared.data(for: request),
              (try? Self.check(response)) != nil,
              let body = try? Self.decoder.decode(Body.self, from: data) else { return nil }
        let frames = body.frames.compactMap { Data(base64Encoded: $0).flatMap(UIImage.init(data:)) }
        return frames.isEmpty ? nil : AlertClip(url: path, frames: frames, periodMs: body.periodMs)
    }

    /// Every task attempt's video of a call (`GET /v1/calls/{call_id}/clips`). Empty when the backend does not
    /// record videos (no `CLIP_STORAGE_DIR`).
    func fetchCallClips(_ callId: String) async throws -> [AttemptClip] {
        guard let base = url else { throw URLError(.badURL) }
        struct Body: Decodable { let clips: [AttemptClip] }
        let (data, response) = try await URLSession.shared.data(for: Self.request(base, "/v1/calls/\(callId)/clips", key: demoKey))
        try Self.check(response)
        return try Self.decoder.decode(Body.self, from: data).clips
    }

    /// The backend's choice for the alert (`GET /v1/calls/{call_id}/comparison`): the task, today's attempts at it
    /// and one earlier video from her learned usual or the last calm call.
    struct ComparisonChoice: Decodable {
        let task: String?
        let today: [AttemptClip]
        let earlier: AttemptClip?
        /// `baseline` or `recent_as_usual`.
        let earlierSource: String?
        /// `expired`, `not_retained` or `no_matching_source` when `earlier` is null.
        let earlierMissing: String?
    }

    /// Nil when the backend predates the route (404), so the app chooses on its own.
    func fetchComparison(_ callId: String) async throws -> ComparisonChoice? {
        guard let base = url else { throw URLError(.badURL) }
        let (data, response) = try await URLSession.shared.data(for: Self.request(base, "/v1/calls/\(callId)/comparison", key: demoKey))
        if (response as? HTTPURLResponse)?.statusCode == 404 { return nil }
        try Self.check(response)
        return try Self.decoder.decode(ComparisonChoice.self, from: data)
    }

    enum VideoError: Error { case preparing, failed, expired, missing }

    /// Downloads an attempt video to this phone once (`GET /v1/clips/{clip_id}/video`) and returns the file.
    /// Waits while the backend is still encoding it (202), up to about 15 s.
    func downloadVideo(_ clip: AttemptClip) async throws -> URL {
        if let cached = AttemptVideos.cached(clip.clipId) { return cached }
        guard let base = url else { throw URLError(.badURL) }
        for _ in 0..<15 {
            var request = Self.request(base, clip.videoPath, key: demoKey)
            request.timeoutInterval = 30
            let (temp, response) = try await URLSession.shared.download(for: request)
            guard let http = response as? HTTPURLResponse else { throw URLError(.badServerResponse) }
            switch http.statusCode {
            case 200:
                let dest = AttemptVideos.file(clip.clipId)
                try? FileManager.default.createDirectory(at: dest.deletingLastPathComponent(), withIntermediateDirectories: true)
                try? FileManager.default.removeItem(at: dest)
                try FileManager.default.moveItem(at: temp, to: dest)
                return dest
            case 202: try await Task.sleep(for: .seconds(1))
            case 409: throw VideoError.failed
            case 410: throw VideoError.expired
            case 404: throw VideoError.missing
            default: try Self.check(response); throw URLError(.badServerResponse)
            }
        }
        throw VideoError.preparing
    }

    private static let decoder: JSONDecoder = {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return decoder
    }()

    private static func check(_ response: URLResponse) throws {
        guard let http = response as? HTTPURLResponse else { throw URLError(.badServerResponse) }
        guard (200..<300).contains(http.statusCode) else {
            throw http.statusCode == 401 ? FeedError.wrongKey : FeedError.http(http.statusCode)
        }
    }

    enum FeedError: Error { case wrongKey, http(Int) }

    private static func describe(_ error: Error) -> String {
        switch error {
        case FeedError.wrongKey: return String(localized: "Wrong demo key")
        case FeedError.http(let code): return String(localized: "Backend answered \(code)")
        default: return String(localized: "Cannot reach the backend")
        }
    }
}
