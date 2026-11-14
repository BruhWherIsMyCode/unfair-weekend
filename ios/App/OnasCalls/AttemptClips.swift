import AVFoundation
import Foundation
import UIKit

/// The two camera checks of a call. The backend's task ids are `smile` and `arms`.
enum CheckTask: String, Codable {
    case smile, arms

    init?(wire: String) {
        if wire.hasPrefix("smile") { self = .smile }
        else if wire.hasPrefix("arm") { self = .arms }
        else { return nil }
    }

    /// "Smile check": the chip on the alert.
    var title: String { self == .smile ? String(localized: "Smile check") : String(localized: "Arms check") }
    /// "Smile": a clip row in the call detail ("Smile · try 1").
    var short: String { self == .smile ? String(localized: "Smile") : String(localized: "Arms") }
    var spoken: String { self == .smile ? String(localized: "smile check") : String(localized: "arms check") }
}

/// One task attempt's silent video on the backend (`GET /v1/calls/{call_id}/clips`, backend/CLIP_STORAGE.md).
/// Kept on the call's history record as the last state the backend reported.
struct AttemptClip: Codable, Equatable, Identifiable {
    let clipId: String
    let callId: String
    let attemptId: String
    let task: String
    /// `live`, `normal` or `acted`.
    let sessionLabel: String
    /// `finished` or `interrupted`: whether the phone sent the whole attempt. Says nothing about the measurement.
    let captureStatus: String
    /// `queued`, `ready`, `failed` or `expired`.
    let state: String
    let recordedAt: String
    let expiresAt: String
    let durationMs: Int?

    var id: String { clipId }

    enum Status { case ready, preparing, failed, expired }

    var status: Status {
        switch state {
        case "ready": .ready
        case "queued": .preparing
        case "expired": .expired
        default: .failed
        }
    }

    var checkTask: CheckTask? { CheckTask(wire: task) }
    var isActed: Bool { sessionLabel == "acted" }
    var recorded: Date? { ISODate.parse(recordedAt) }
    var expires: Date? { ISODate.parse(expiresAt) }
    var videoPath: String { "/v1/clips/\(clipId)/video" }
    /// The video was downloaded to this phone, so it plays after the backend has deleted it.
    var isOnPhone: Bool { AttemptVideos.cached(clipId) != nil }
    var isPlayable: Bool { status == .ready || isOnPhone }

    /// Recording order, then try numbers per task: "Smile · try 1", "Smile · try 2", "Arms · try 1".
    static func numbered(_ clips: [AttemptClip]) -> [(clip: AttemptClip, task: CheckTask, attempt: Int)] {
        var counts: [CheckTask: Int] = [:]
        return clips.sorted { $0.recordedAt < $1.recordedAt }.compactMap { clip in
            guard let task = clip.checkTask else { return nil }
            counts[task, default: 0] += 1
            return (clip, task, counts[task]!)
        }
    }
}

/// The attempt videos downloaded to this phone, next to the call history.
enum AttemptVideos {
    nonisolated static func file(_ clipId: String) -> URL {
        let safe = clipId.filter { $0.isLetter || $0.isNumber || $0 == "_" || $0 == "-" }
        return CallHistory.folder.appendingPathComponent("videos/\(safe).mp4")
    }

    nonisolated static func cached(_ clipId: String) -> URL? {
        let url = file(clipId)
        return FileManager.default.fileExists(atPath: url.path) ? url : nil
    }

    nonisolated static func remove(_ clips: [AttemptClip]) {
        for clip in clips { try? FileManager.default.removeItem(at: file(clip.clipId)) }
    }

    /// Seconds, read from the file itself.
    static func duration(_ url: URL) async -> Double {
        guard let d = try? await AVURLAsset(url: url).load(.duration), d.seconds.isFinite else { return 0 }
        return max(0, d.seconds)
    }

    /// The first frame, for the history and call detail thumbnails.
    static func poster(_ url: URL) async -> UIImage? {
        let generator = AVAssetImageGenerator(asset: AVURLAsset(url: url))
        generator.appliesPreferredTrackTransform = true
        generator.maximumSize = CGSize(width: 200, height: 200)
        guard let (image, _) = try? await generator.image(at: .zero) else { return nil }
        return UIImage(cgImage: image)
    }
}

enum ISODate {
    static func parse(_ iso: String) -> Date? {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let d = f.date(from: iso) { return d }
        f.formatOptions = [.withInternetDateTime]
        return f.date(from: iso)
    }
}
