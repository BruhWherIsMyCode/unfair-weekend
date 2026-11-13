import Foundation

/// Everything the parent call screens (Claude Design "client app: check-in call flow") need, derived from the
/// backend's commands. Pure state: views animate it, this never touches UI. Contains no measurement or verdict.
public struct CallScreen: Equatable, Sendable {
    public enum Mode: Equatable, Sendable {
        case idle
        case ringing(callId: String, title: String)
        case inCall
        case done
        case ended(reason: String?)
    }

    /// The four progress dots: Questions, Smile, Arms, Sentence.
    public enum Stage: Int, CaseIterable, Sendable {
        case questions = 0
        case smile
        case arms
        case sentence
    }

    public enum DotState: Equatable, Sendable {
        case done
        case current
        case upcoming
    }

    /// Spoken subtitles. Word i starts fading in at `startedAt + i * durationMs / words.count`.
    /// Replace with TTS word timestamps when the backend sends them.
    public struct Subtitle: Equatable, Sendable {
        public let lineId: String
        public let text: String
        public let words: [String]
        public let startedAt: Date
        public let durationMs: Int

        public func wordStart(_ index: Int) -> Date {
            guard !words.isEmpty else { return startedAt }
            let step = Double(durationMs) / Double(words.count) / 1000
            return startedAt.addingTimeInterval(step * Double(index))
        }
    }

    public enum Timer: Equatable, Sendable {
        /// Instruction still playing: show the full ring, not counting.
        case waiting(totalSeconds: Int)
        /// Counting down to `endsAt`.
        case running(endsAt: Date, totalSeconds: Int)
        /// Ring turns green, number becomes a check mark.
        case finished
    }

    public enum Card: Equatable, Sendable {
        /// "Assistant" + subtitles (greeting, set-up, recovery, goodbye).
        case assistant(Subtitle)
        /// "Question n of 3" + subtitles; Listening indicator on the right while `listening`.
        case question(number: Int, total: Int, subtitle: Subtitle, listening: Bool)
        /// "Step n of 3", fixed title/subtitle text, timer ring on the right. `hint` is a neutral framing hint.
        case task(step: Int, total: Int, task: String, timer: Timer, hint: String?)
        /// "Step 3 of 3", the sentence in quotes, "Say it out loud", Listening on the right while capturing.
        case sentence(step: Int, total: Int, sentence: String, listening: Bool)
    }

    public var mode: Mode = .idle
    public var stage: Stage = .questions
    public var card: Card?
    /// True during smile/arms: the self-view grows to 200 x 266 and the card lifts 44 pt.
    public var selfViewLarge: Bool = false

    public init() {}

    public func dot(_ stage: Stage) -> DotState {
        if stage.rawValue < self.stage.rawValue {
            return .done
        }
        if stage == self.stage {
            return .current
        }
        return .upcoming
    }
}

/// Folds client-side events into a CallScreen. Deterministic and unit-tested.
public struct CallScreenReducer: Sendable {
    public private(set) var screen = CallScreen()
    private var lastSubtitle: CallScreen.Subtitle?
    private var task: String?

    static let questions = ["q_medicine": 1, "q_feeling": 2, "q_unusual": 3]
    static let taskSteps = ["smile": 1, "arms": 2, "sentence": 3]
    static let stages: [String: CallScreen.Stage] = ["smile": .smile, "arms": .arms, "sentence": .sentence]

    public init() {}

    public mutating func ringing(callId: String, title: String) {
        screen = CallScreen()
        screen.mode = .ringing(callId: callId, title: title)
        lastSubtitle = nil
        task = nil
    }

    public mutating func connected() {
        screen.mode = .inCall
    }

    public mutating func say(lineId: String, text: String, durationMs: Int, at now: Date) {
        screen.mode = .inCall
        let words = text.split(whereSeparator: { $0 == " " }).map(String.init)
        let subtitle = CallScreen.Subtitle(lineId: lineId, text: text, words: words, startedAt: now, durationMs: durationMs)
        lastSubtitle = subtitle
        if let number = Self.questions[lineId] {
            screen.stage = .questions
            screen.selfViewLarge = false
            screen.card = .question(number: number, total: 3, subtitle: subtitle, listening: false)
            return
        }
        if let taskName = Self.taskName(of: lineId) {
            enterTask(taskName, totalMs: nil, instruction: text)
            return
        }
        if lineId == "bye" {
            screen.stage = .sentence
        }
        screen.selfViewLarge = false
        screen.card = .assistant(subtitle)
    }

    public mutating func listening(questionId: String) {
        guard case .question(let number, let total, let subtitle, _) = screen.card else { return }
        screen.card = .question(number: number, total: total, subtitle: subtitle, listening: true)
    }

    public mutating func captureCommanded(task taskName: String, durationMs: Int) {
        if task != taskName {
            enterTask(taskName, totalMs: durationMs, instruction: nil)
        }
        if case .task(let step, let total, let name, _, let hint) = screen.card {
            screen.card = .task(step: step, total: total, task: name, timer: .waiting(totalSeconds: durationMs / 1000), hint: hint)
        }
    }

    public mutating func captureStarted(durationMs: Int, at now: Date) {
        switch screen.card {
        case .task(let step, let total, let name, _, let hint):
            screen.card = .task(step: step, total: total, task: name,
                                timer: .running(endsAt: now.addingTimeInterval(Double(durationMs) / 1000),
                                                totalSeconds: durationMs / 1000), hint: hint)
        case .sentence(let step, let total, let sentence, _):
            screen.card = .sentence(step: step, total: total, sentence: sentence, listening: true)
        default:
            break
        }
    }

    public mutating func hint(_ hint: String?) {
        if case .task(let step, let total, let name, let timer, _) = screen.card {
            screen.card = .task(step: step, total: total, task: name, timer: timer, hint: hint)
        }
    }

    public mutating func captureFinished() {
        switch screen.card {
        case .task(let step, let total, let name, _, _):
            screen.card = .task(step: step, total: total, task: name, timer: .finished, hint: nil)
        case .sentence(let step, let total, let sentence, _):
            screen.card = .sentence(step: step, total: total, sentence: sentence, listening: false)
        default:
            break
        }
    }

    public mutating func ended(reason: String?) {
        task = nil
        screen.selfViewLarge = false
        if reason == "completed" {
            screen.mode = .done
            return
        }
        screen.mode = .ended(reason: reason)
    }

    public mutating func reset() {
        self = CallScreenReducer()
    }

    private mutating func enterTask(_ taskName: String, totalMs: Int?, instruction: String?) {
        let changed = task != taskName
        task = taskName
        screen.stage = Self.stages[taskName] ?? screen.stage
        let step = Self.taskSteps[taskName] ?? 1
        if taskName == "sentence" {
            screen.selfViewLarge = false
            var sentence = ""
            if case .sentence(_, _, let previous, _) = screen.card {
                sentence = previous
            }
            if let instruction, let extracted = Self.sentence(from: instruction) {
                sentence = extracted
            }
            screen.card = .sentence(step: step, total: 3, sentence: sentence, listening: false)
            return
        }
        screen.selfViewLarge = true
        let seconds = (totalMs ?? Self.defaultMs[taskName] ?? 5000) / 1000
        if changed || instruction != nil {
            screen.card = .task(step: step, total: 3, task: taskName, timer: .waiting(totalSeconds: seconds), hint: nil)
        }
    }

    static let defaultMs = ["smile": 5000, "arms": 12000, "sentence": 12000]

    static func taskName(of lineId: String) -> String? {
        for prefix in ["task_", "repeat_"] where lineId.hasPrefix(prefix) {
            return String(lineId.dropFirst(prefix.count))
        }
        return nil
    }

    /// "Please repeat this sentence after me: Today I am sitting by the window." -> the part after the colon.
    static func sentence(from instruction: String) -> String? {
        guard let colon = instruction.lastIndex(of: ":") else { return nil }
        let tail = instruction[instruction.index(after: colon)...].trimmingCharacters(in: .whitespaces)
        return tail.isEmpty ? nil : tail
    }
}
