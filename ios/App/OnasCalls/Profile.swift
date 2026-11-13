import Foundation
import Observation
import UIKit

/// The person the family looks after. Edited on the profile screen, kept in UserDefaults; the photo is a file.
struct PersonProfile: Codable, Equatable {
    var name = "Fedya"
    /// How the family calls them: "Dad", "Mum", "Aunt". Shown before the last call time.
    var relation = "Dad"
    var pronoun: Pronoun = .he
    /// Free text for the family: medicines, usual routine, who has a key.
    var notes = ""

    /// Name to show when the field was cleared.
    var shownName: String {
        let trimmed = name.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? String(localized: "Your person") : trimmed
    }

    /// "Dad, last call 09:02", or "Last call 09:02" without a relation.
    func lastCall(_ time: String) -> String {
        let r = relation.trimmingCharacters(in: .whitespaces)
        return r.isEmpty ? String(localized: "Last call \(time)") : String(localized: "\(r), last call \(time)")
    }

    func youCalled(_ time: String) -> String {
        let r = relation.trimmingCharacters(in: .whitespaces)
        return r.isEmpty ? String(localized: "You called at \(time)") : String(localized: "\(r), you called at \(time)")
    }
}

/// He or she. Every line that names the person indirectly goes through here, so the profile can switch it.
enum Pronoun: String, Codable, CaseIterable, Identifiable {
    case he, she
    var id: String { rawValue }

    var label: String { self == .he ? String(localized: "He") : String(localized: "She") }
    var notUsualCaps: String { self == .he ? String(localized: "NOT HIS USUAL") : String(localized: "NOT HER USUAL") }
    var notUsual: String { self == .he ? String(localized: "Not his usual") : String(localized: "Not her usual") }
    var isFine: String { self == .he ? String(localized: "He's fine") : String(localized: "She's fine") }
    var unwellLine: String {
        self == .he ? String(localized: "If he seems unwell, call 112.") : String(localized: "If she seems unwell, call 112.")
    }
    var seemsDifferent: String {
        self == .he ? String(localized: "He seems different from his usual.") : String(localized: "She seems different from her usual.")
    }
    func seemedDifferent(_ name: String) -> String {
        self == .he ? String(localized: "\(name) seemed different from his usual during today's check-in.")
                    : String(localized: "\(name) seemed different from her usual during today's check-in.")
    }
    var tiredLine: String {
        self == .he ? String(localized: "He says he took his medicine and feels a bit tired.")
                    : String(localized: "She says she took her medicine and feels a bit tired.")
    }
    var saidFine: String { self == .he ? String(localized: "You said he was fine") : String(localized: "You said she was fine") }
}

/// One check-in call in the profile's history.
struct CallRecord: Codable, Identifiable, Equatable {
    enum Outcome: String, Codable {
        case asUsual, learning, notUsual, concern, incomplete, noAnswer

        init(kind: String) {
            switch kind {
            case "change_noticed": self = .notUsual
            case "concern_raised": self = .concern
            case "incomplete": self = .incomplete
            case "no_answer": self = .noAnswer
            case "learning": self = .learning
            default: self = .asUsual
            }
        }

        /// Amber in the list: the family was asked to call.
        var needsCall: Bool { self == .notUsual || self == .concern }
    }

    enum ClipKind: String, Codable {
        /// The staged scene's bundled clip.mp4.
        case staged
        /// Her camera frames from a real alert, stored on this phone.
        case frames
    }

    var id: String
    var date: Date
    var outcome: Outcome
    var text: String
    /// The family's own note on this call.
    var note = ""
    var clip: ClipKind?
    var clipPeriodMs: Int?
    var calledAt: Date?
    var markedFineAt: Date?
    /// From the check-in backend (true) or the staged scene / sample history (false).
    var live = false
}

/// The profile's call history. Newest first, kept as JSON in Application Support. Real alert clips are stored
/// next to it as JPEG frames, so they outlast the backend's 30 min.
@MainActor
@Observable
final class CallHistory {
    private(set) var records: [CallRecord] = []

    @ObservationIgnored private let fm = FileManager.default

    init() {
        if let data = try? Data(contentsOf: Self.file),
           let saved = try? JSONDecoder().decode([CallRecord].self, from: data) {
            records = saved
        } else {
            records = Self.sample()
            save()
        }
    }

    func record(_ id: String) -> CallRecord? { records.first { $0.id == id } }

    /// Adds a call or replaces one with the same id, keeping what the family added to it.
    func upsert(_ new: CallRecord) {
        if let i = records.firstIndex(where: { $0.id == new.id }) {
            var merged = new
            let old = records[i]
            merged.note = old.note
            merged.calledAt = old.calledAt ?? new.calledAt
            merged.markedFineAt = old.markedFineAt ?? new.markedFineAt
            if merged.clip == nil { merged.clip = old.clip; merged.clipPeriodMs = old.clipPeriodMs }
            merged.date = old.date
            records[i] = merged
        } else {
            records.append(new)
        }
        records.sort { $0.date > $1.date }
        save()
    }

    func update(_ id: String?, _ change: (inout CallRecord) -> Void) {
        guard let id, let i = records.firstIndex(where: { $0.id == id }) else { return }
        change(&records[i])
        save()
    }

    func delete(_ ids: [String]) {
        for id in ids {
            try? fm.removeItem(at: Self.clipDir(id))
            try? fm.removeItem(at: ClipExport.cachedURL(id))
        }
        records.removeAll { ids.contains($0.id) }
        save()
    }

    /// Demo panel: back to the sample week, all stored clips removed.
    func resetToSample() {
        delete(records.map(\.id))
        records = Self.sample()
        save()
    }

    // MARK: Clips

    func saveClip(_ clip: AlertClip, for id: String) {
        guard record(id) != nil, record(id)?.clip != .frames else { return }
        let dir = Self.clipDir(id)
        try? fm.createDirectory(at: dir, withIntermediateDirectories: true)
        for (i, frame) in clip.frames.enumerated() {
            if let jpeg = frame.jpegData(compressionQuality: 0.85) {
                try? jpeg.write(to: dir.appendingPathComponent(String(format: "%04d.jpg", i)))
            }
        }
        update(id) {
            $0.clip = .frames
            $0.clipPeriodMs = clip.periodMs
        }
    }

    /// Her frames for a real alert, from this phone. Nil for a staged clip or when nothing was kept.
    nonisolated static func loadFrames(_ record: CallRecord) -> AlertClip? {
        guard record.clip == .frames else { return nil }
        let dir = clipDir(record.id)
        let names = ((try? FileManager.default.contentsOfDirectory(atPath: dir.path)) ?? []).filter { $0.hasSuffix(".jpg") }.sorted()
        let frames = names.compactMap { UIImage(contentsOfFile: dir.appendingPathComponent($0).path) }
        return frames.isEmpty ? nil : AlertClip(url: "local:\(record.id)", frames: frames, periodMs: record.clipPeriodMs ?? 200)
    }

    #if DEBUG
    /// UI tests only (`-uitest.framesClip`): a real-alert style call whose clip is drawn colour frames, no person.
    func addTestFramesClip() {
        let id = "uitest-frames"
        delete([id])
        let size = CGSize(width: 320, height: 240)
        let frames = (0..<20).map { i in
            UIGraphicsImageRenderer(size: size).image { ctx in
                UIColor(hue: CGFloat(i) / 20, saturation: 0.5, brightness: 0.9, alpha: 1).setFill()
                ctx.fill(CGRect(origin: .zero, size: size))
                ("\(i)" as NSString).draw(at: CGPoint(x: 140, y: 100),
                                         withAttributes: [.font: UIFont.boldSystemFont(ofSize: 40)])
            }
        }
        upsert(CallRecord(id: id, date: Date().addingTimeInterval(-3600), outcome: .notUsual,
                          text: "UI test call with a drawn clip.", live: true))
        saveClip(AlertClip(url: "uitest", frames: frames, periodMs: 200), for: id)
    }
    #endif

    // MARK: Storage

    nonisolated static var folder: URL {
        let base = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
        let dir = base.appendingPathComponent("OnasCalls", isDirectory: true)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        return dir
    }

    private static var file: URL { folder.appendingPathComponent("history.json") }
    nonisolated static func clipDir(_ id: String) -> URL { folder.appendingPathComponent("clips/\(id)", isDirectory: true) }

    private func save() {
        if let data = try? JSONEncoder().encode(records) {
            try? data.write(to: Self.file, options: .atomic)
        }
    }

    /// A calm week before the demo, so the profile does not open empty. No names in the text: the profile can
    /// be renamed later.
    private static func sample() -> [CallRecord] {
        let cal = Calendar.current
        let today = cal.startOfDay(for: Date())
        let plan: [(days: Int, hour: Int, minute: Int, outcome: CallRecord.Outcome, text: String)] = [
            (1, 9, 4, .asUsual, String(localized: "Check-in looked as usual. Answered every question, face, arms and speech as usual.")),
            (2, 9, 11, .asUsual, String(localized: "Check-in looked as usual. Had breakfast, took the morning medicine.")),
            (3, 9, 31, .noAnswer, String(localized: "Did not answer the check-in call. Answered the second try at 09:45.")),
            (4, 9, 2, .asUsual, String(localized: "Check-in looked as usual. Going to the market later.")),
            (5, 9, 7, .asUsual, String(localized: "Check-in looked as usual. Slept well.")),
        ]
        return plan.compactMap { p in
            guard let day = cal.date(byAdding: .day, value: -p.days, to: today),
                  let date = cal.date(bySettingHour: p.hour, minute: p.minute, second: 0, of: day) else { return nil }
            return CallRecord(id: "sample-\(p.days)", date: date, outcome: p.outcome, text: p.text)
        }
    }
}

/// The profile and its photo on disk.
enum ProfileStorage {
    private static let key = "profile.person"
    private static var photoURL: URL { CallHistory.folder.appendingPathComponent("photo.jpg") }

    static func load() -> PersonProfile {
        UserDefaults.standard.data(forKey: key).flatMap { try? JSONDecoder().decode(PersonProfile.self, from: $0) }
            ?? PersonProfile()
    }

    static func save(_ profile: PersonProfile) {
        UserDefaults.standard.set(try? JSONEncoder().encode(profile), forKey: key)
    }

    static func loadPhoto() -> UIImage? { UIImage(contentsOfFile: photoURL.path) }

    static func savePhoto(_ image: UIImage?) {
        guard let image else {
            try? FileManager.default.removeItem(at: photoURL)
            return
        }
        try? image.jpegData(compressionQuality: 0.85)?.write(to: photoURL, options: .atomic)
    }
}

extension UIImage {
    /// Scaled so the longer side is at most `side` points; for the profile photo.
    func fitted(to side: CGFloat) -> UIImage {
        let longest = max(size.width, size.height)
        guard longest > side else { return self }
        let scale = side / longest
        let target = CGSize(width: size.width * scale, height: size.height * scale)
        return UIGraphicsImageRenderer(size: target).image { _ in draw(in: CGRect(origin: .zero, size: target)) }
    }
}
