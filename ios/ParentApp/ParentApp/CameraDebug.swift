import Foundation
import Observation
import ParentCallKit
import UIKit

/// Camera debug: the backend runs Agent 3's real smile or arms module on this phone's frames, outside any call
/// (`/v1/debug/runs`, backend/checkin_backend/debug_probe.py). No assistant, nothing stored. This model drives the
/// task's own timeline, posts the frames and keeps what the analysis reported.
@MainActor
@Observable
final class CameraDebugModel {
    enum Task: String, CaseIterable, Identifiable {
        case smile, arms
        var id: String { rawValue }
        var title: String { self == .smile ? "Smile" : "Arms" }
    }

    enum Mirroring: String, CaseIterable, Identifiable {
        case server, on, off
        var id: String { rawValue }
        var title: String {
            switch self {
            case .server: return "Server setting"
            case .on: return "Mirrored"
            case .off: return "Not mirrored"
            }
        }
        var value: Bool? { self == .server ? nil : self == .on }
    }

    enum State: Equatable {
        case ready
        case starting
        case running(started: Date, durationMs: Int)
        case finishing
        case done
        case failed(String)
    }

    var task: Task = .arms
    var mirroring: Mirroring = .server
    private(set) var state: State = .ready
    private(set) var frame: DebugFrame?
    /// The JPEG the latest `frame` facts belong to: the analysis lags a few frames behind the upload.
    private(set) var frameImage: UIImage?
    @ObservationIgnored private var sent: [Int: Data] = [:]
    private(set) var observations: [JSONValue] = []
    private(set) var result: JSONValue?
    private(set) var error: String?
    private(set) var framesSent = 0
    private(set) var framesAnalysed = 0
    /// Arms: per phase, how many analysed frames had the hand on her left, her right, no hand, or no face.
    private(set) var tally: [String: [String: Int]] = [:]
    /// What the phone's camera delivered during this run (raw size, mirroring, angle, how frames were fixed).
    private(set) var cameraStats: SelfViewCamera.FrameStats?
    private(set) var cues: [(atMs: Int, lineId: String)] = []

    private let settings: AppSettings
    /// The on-screen preview.
    private let preview: SelfViewCamera
    /// Where the frames for the analysis come from (the same front camera on a phone).
    private let camera: CameraSource
    @ObservationIgnored private var loop: _Concurrency.Task<Void, Never>?

    init(settings: AppSettings, preview: SelfViewCamera, frames: CameraSource?) {
        self.settings = settings
        self.preview = preview
        self.camera = frames ?? preview
    }

    var cameraForPreview: SelfViewCamera { preview }

    var isRunning: Bool {
        switch state {
        case .starting, .running, .finishing: return true
        default: return false
        }
    }

    func appear() {
        preview.startPreview()
    }

    func disappear() {
        loop?.cancel()
        preview.stopPreview()
        _Concurrency.Task { [camera] in await camera.stop() }
    }

    func start() {
        guard !isRunning else { return }
        loop?.cancel()
        frame = nil
        frameImage = nil
        observations = []
        result = nil
        error = nil
        framesSent = 0
        framesAnalysed = 0
        sent = [:]
        state = .starting
        let task = task, mirrored = mirroring.value
        loop = _Concurrency.Task { [weak self] in
            await self?.run(task: task, mirrored: mirrored)
        }
    }

    /// What she should do at `elapsedMs`, from the task's cue lines (the same timeline as in a call).
    func instruction(atMs elapsed: Int) -> String {
        let passed = cues.filter { $0.atMs <= elapsed }.map(\.lineId)
        switch task {
        case .smile:
            return passed.contains("smile_now") ? "Smile wide!" : "Relax your face"
        case .arms:
            return passed.contains("swap_hands") ? "Swap: phone in LEFT hand, raise your RIGHT hand"
                                                 : "Phone in RIGHT hand, raise your LEFT hand"
        }
    }

    private func run(task: Task, mirrored: Bool?) async {
        guard let client = DebugClient(settings: settings) else {
            state = .failed("Set the backend address and demo key in Settings first.")
            return
        }
        do {
            let run = try await client.start(task: task.rawValue, mirrored: mirrored)
            cues = run.cues.map { ($0.atMs, $0.lineId) }
            try await camera.start()
            let started = Date()
            state = .running(started: started, durationMs: run.capture.durationMs)
            var sequence = 0
            var announced = Set<String>()
            while !_Concurrency.Task.isCancelled {
                let elapsed = Int(Date().timeIntervalSince(started) * 1000)
                if elapsed >= run.capture.durationMs { break }
                for cue in cues where cue.atMs <= elapsed && !announced.contains(cue.lineId) {
                    announced.insert(cue.lineId)
                    UINotificationFeedbackGenerator().notificationOccurred(.warning)
                }
                if let jpeg = try await camera.jpegFrame() {
                    let snapshot = try await client.sample(runId: run.runId, sequence: sequence, capturedMs: elapsed,
                                                           jpeg: jpeg, since: observations.count)
                    framesSent += 1
                    sequence += 1
                    sent[elapsed] = jpeg
                    if sent.count > 40, let oldest = sent.keys.min() {
                        sent[oldest] = nil
                    }
                    apply(snapshot)
                    cameraStats = preview.frameStats()
                }
                let next = started.addingTimeInterval(Double(sequence * run.capture.samplePeriodMs) / 1000)
                let wait = next.timeIntervalSinceNow
                if wait > 0 {
                    try await _Concurrency.Task.sleep(nanoseconds: UInt64(wait * 1_000_000_000))
                }
            }
            await camera.stop()
            state = .finishing
            let final = try await client.finish(runId: run.runId)
            observations = []
            apply(final)
            state = final.error == nil ? .done : .failed(final.error ?? "")
        } catch is CancellationError {
            await camera.stop()
        } catch {
            await camera.stop()
            state = .failed(DebugClient.describe(error))
        }
    }

    private func apply(_ snapshot: DebugSnapshot) {
        framesAnalysed = snapshot.framesAnalysed
        if let newTally = snapshot.tally {
            tally = newTally
        }
        if let newFrame = snapshot.frame, newFrame != frame {
            frame = newFrame
            if let jpeg = sent[newFrame.ms] {
                frameImage = UIImage(data: jpeg)
            }
        }
        observations.append(contentsOf: snapshot.observations)
        result = snapshot.result
        error = snapshot.error
    }
}

// MARK: wire format

struct DebugRunStarted: Decodable {
    struct Capture: Decodable {
        let durationMs: Int
        let samplePeriodMs: Int
    }

    struct Cue: Decodable {
        let atMs: Int
        let lineId: String
    }

    let runId: String
    let capture: Capture
    let cues: [Cue]
}

struct DebugSnapshot: Decodable {
    let framesAnalysed: Int
    let tally: [String: [String: Int]]?
    let frame: DebugFrame?
    let observations: [JSONValue]
    let result: JSONValue?
    let error: String?
    let done: Bool
}

/// What the module saw in its latest frame, in that frame's pixels (after flipping, when mirrored).
struct DebugFrame: Decodable, Equatable {
    struct Hand: Decodable, Equatable {
        let side: String
        let eyeWidths: Double?
        let ifMirroredFlipped: String?
        let centre: [Double]?
        let box: [Double]?
    }

    let task: String
    let ms: Int
    let expected: String?
    let mirrored: Bool
    let width: Double?
    let height: Double?
    let face: Bool
    let faceSmall: Bool?
    let midlineX: Double?
    let eyeDistancePx: Double?
    let rollDeg: Double?
    let eyePoints: [[Double]]?
    let mouthPoints: [[Double]]?
    let hands: [Hand]?
    let reading: [String: JSONValue]?
}

/// Any JSON, for the analysis's observations and result, which this screen shows rather than interprets.
enum JSONValue: Decodable, Equatable {
    case null
    case bool(Bool)
    case number(Double)
    case string(String)
    case array([JSONValue])
    case object([String: JSONValue])

    init(from decoder: Decoder) throws {
        let c = try decoder.singleValueContainer()
        if c.decodeNil() { self = .null }
        else if let b = try? c.decode(Bool.self) { self = .bool(b) }
        else if let n = try? c.decode(Double.self) { self = .number(n) }
        else if let s = try? c.decode(String.self) { self = .string(s) }
        else if let a = try? c.decode([JSONValue].self) { self = .array(a) }
        else { self = .object(try c.decode([String: JSONValue].self)) }
    }

    subscript(key: String) -> JSONValue? {
        if case .object(let o) = self { return o[key] }
        return nil
    }

    var string: String? {
        if case .string(let s) = self { return s }
        return nil
    }

    var number: Double? {
        if case .number(let n) = self { return n }
        return nil
    }

    var bool: Bool? {
        if case .bool(let b) = self { return b }
        return nil
    }

    var strings: [String] {
        if case .array(let a) = self { return a.compactMap(\.string) }
        return []
    }
}

// MARK: HTTP

struct DebugClient {
    let base: URL
    let key: String

    init?(settings: AppSettings) {
        guard let url = settings.url, !settings.demoKey.isEmpty else { return nil }
        base = url
        key = settings.demoKey
    }

    enum Failure: Error { case http(Int, String?) }

    private static let decoder: JSONDecoder = {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return decoder
    }()

    private func post<T: Decodable>(_ path: String, _ body: [String: Any]) async throws -> T {
        var request = URLRequest(url: URL(string: path, relativeTo: base)!.absoluteURL)
        request.httpMethod = "POST"
        request.setValue(key, forHTTPHeaderField: "X-Demo-Key")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.timeoutInterval = 40
        request.httpBody = try JSONSerialization.data(withJSONObject: body)
        let (data, response) = try await URLSession.shared.data(for: request)
        let status = (response as? HTTPURLResponse)?.statusCode ?? 0
        guard (200..<300).contains(status) else {
            let message = (try? JSONSerialization.jsonObject(with: data) as? [String: Any])?["message"] as? String
            throw Failure.http(status, message)
        }
        return try Self.decoder.decode(T.self, from: data)
    }

    func start(task: String, mirrored: Bool?) async throws -> DebugRunStarted {
        var body: [String: Any] = ["task": task]
        if let mirrored { body["mirrored"] = mirrored }
        return try await post("/v1/debug/runs", body)
    }

    func sample(runId: String, sequence: Int, capturedMs: Int, jpeg: Data, since: Int) async throws -> DebugSnapshot {
        try await post("/v1/debug/runs/\(runId)/samples", [
            "sequence": sequence, "captured_ms": capturedMs, "data_base64": jpeg.base64EncodedString(), "since": since,
        ])
    }

    func finish(runId: String) async throws -> DebugSnapshot {
        try await post("/v1/debug/runs/\(runId)/finish", [:])
    }

    static func describe(_ error: Error) -> String {
        switch error {
        case Failure.http(401, _): return "Wrong demo key."
        case Failure.http(let status, let message): return message ?? "Backend answered \(status)."
        default: return "Cannot reach the backend: \(error.localizedDescription)"
        }
    }
}
