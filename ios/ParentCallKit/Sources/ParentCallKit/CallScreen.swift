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

    /// The ring shows only while she has to hold something, and counts the time left of that part.
    public enum Timer: Equatable, Sendable {
        /// No ring: the instruction is playing, or she is still relaxing before "Now smile!".
        case hidden
        /// Counting down to `endsAt`; the ring starts full at `total` seconds.
        case running(endsAt: Date, total: TimeInterval)
        /// Ring turns green, number becomes a check mark.
        case finished
    }

    /// What she is doing inside a task, following the backend's cue lines (`smile_now`, `swap_hands`).
    public enum TaskPart: String, Equatable, Sendable {
        /// Smile, before "Now smile!": face relaxed, no timer.
        case relax
        /// Smile, after "Now smile!": counts down to the end of the capture.
        case smile
        /// Arms, first half: counts down to the swap.
        case leftHand
        /// Arms, after "Now swap": counts down to the end of the capture.
        case rightHand
        /// A task this app does not know: one countdown for the whole capture.
        case other
    }

    public enum Card: Equatable, Sendable {
        /// "Assistant" + subtitles (greeting, set-up, recovery, goodbye).
        case assistant(Subtitle)
        /// "Question n of 3" + subtitles; Listening indicator on the right while `listening`.
        case question(number: Int, total: Int, subtitle: Subtitle, listening: Bool)
        /// "Step n of 3", title/subtitle for `part`, timer ring on the right. `hint` is a neutral framing hint.
        case task(step: Int, total: Int, task: String, part: TaskPart, timer: Timer, hint: String?)
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
    /// When the current capture ends, from `captureStarted`; the smile and right-hand countdowns end here.
    private var captureEndsAt: Date?

    static let questions = ["q_medicine": 1, "q_feeling": 2, "q_unusual": 3]
    static let taskSteps = ["smile": 1, "arms": 2, "sentence": 3]
    static let stages: [String: CallScreen.Stage] = ["smile": .smile, "arms": .arms, "sentence": .sentence]
    /// Cue lines the backend plays during a capture: they move the task on and keep its card.
    static let cues: [String: (task: String, part: CallScreen.TaskPart)] = [
        "smile_now": ("smile", .smile),
        "swap_hands": ("arms", .rightHand),
    ]
    /// When the arms swap cue plays, after the capture starts. The phone is not told, so this mirrors
    /// analysis/session/limits.py ARMS_PHASES ("swap" starts at 6500 ms). The swap cue itself always takes over.
    static let armsSwapMs = 6500
    /// Smile time left if the cue arrives before the capture's start is known (6000 ms window, cue at 1500 ms).
    static let smileFallbackMs = 4500

    public init() {}

    public mutating func ringing(callId: String, title: String) {
        screen = CallScreen()
        screen.mode = .ringing(callId: callId, title: title)
        lastSubtitle = nil
        task = nil
        captureEndsAt = nil
    }

    public mutating func connected() {
        screen.mode = .inCall
    }

    public mutating func say(lineId: String, text: String, durationMs: Int, at now: Date) {
        screen.mode = .inCall
        let words = text.split(whereSeparator: { $0 == " " }).map(String.init)
        let subtitle = CallScreen.Subtitle(lineId: lineId, text: text, words: words, startedAt: now, durationMs: durationMs)
        lastSubtitle = subtitle
        if let cue = Self.cues[lineId], cue.task == task, case .task = screen.card {
            enterPart(cue.part, at: now)
            return
        }
        if let number = Self.questions[lineId] {
            screen.stage = .questions
            screen.selfViewLarge = false
            screen.card = .question(number: number, total: 3, subtitle: subtitle, listening: false)
            return
        }
        if let taskName = Self.taskName(of: lineId) {
            enterTask(taskName, instruction: text)
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

    /// The capture is on its way: still no ring until it has really started.
    public mutating func captureCommanded(task taskName: String, durationMs: Int) {
        if task != taskName {
            enterTask(taskName, instruction: nil)
        }
        captureEndsAt = nil
        updateTask { part, timer, _ in
            part = Self.firstPart(of: taskName)
            timer = .hidden
        }
    }

    public mutating func captureStarted(durationMs: Int, at now: Date) {
        let ends = now.addingTimeInterval(Double(durationMs) / 1000)
        captureEndsAt = ends
        switch screen.card {
        case .task(_, _, let name, _, _, _):
            switch Self.firstPart(of: name) {
            case .relax:
                updateTask { part, timer, _ in
                    part = .relax
                    timer = .hidden
                }
            case .leftHand:
                let swap = min(ends, now.addingTimeInterval(Double(Self.armsSwapMs) / 1000))
                updateTask { part, timer, _ in
                    part = .leftHand
                    timer = .running(endsAt: swap, total: swap.timeIntervalSince(now))
                }
            default:
                updateTask { _, timer, _ in timer = .running(endsAt: ends, total: ends.timeIntervalSince(now)) }
            }
        case .sentence(let step, let total, let sentence, _):
            screen.card = .sentence(step: step, total: total, sentence: sentence, listening: true)
        default:
            break
        }
    }

    public mutating func hint(_ newHint: String?) {
        updateTask { _, _, hint in hint = newHint }
    }

    public mutating func captureFinished() {
        captureEndsAt = nil
        switch screen.card {
        case .task:
            updateTask { _, timer, hint in
                timer = .finished
                hint = nil
            }
        case .sentence(let step, let total, let sentence, _):
            screen.card = .sentence(step: step, total: total, sentence: sentence, listening: false)
        default:
            break
        }
    }

    public mutating func ended(reason: String?) {
        task = nil
        captureEndsAt = nil
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

    /// A cue line: the next part starts now and counts down to the end of the capture.
    private mutating func enterPart(_ next: CallScreen.TaskPart, at now: Date) {
        let fallback = now.addingTimeInterval(Double(Self.smileFallbackMs) / 1000)
        let ends = max(now, captureEndsAt ?? fallback)
        updateTask { part, timer, hint in
            part = next
            timer = .running(endsAt: ends, total: ends.timeIntervalSince(now))
            hint = nil
        }
    }

    private mutating func updateTask(_ change: (inout CallScreen.TaskPart, inout CallScreen.Timer, inout String?) -> Void) {
        guard case .task(let step, let total, let name, var part, var timer, var hint) = screen.card else { return }
        change(&part, &timer, &hint)
        screen.card = .task(step: step, total: total, task: name, part: part, timer: timer, hint: hint)
    }

    /// The instruction line (or a capture for a new task): the task card, no ring yet.
    private mutating func enterTask(_ taskName: String, instruction: String?) {
        let changed = task != taskName
        task = taskName
        captureEndsAt = nil
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
        if changed || instruction != nil {
            screen.card = .task(step: step, total: 3, task: taskName, part: Self.firstPart(of: taskName),
                                timer: .hidden, hint: nil)
        }
    }

    static func firstPart(of task: String) -> CallScreen.TaskPart {
        switch task {
        case "smile": return .relax
        case "arms": return .leftHand
        default: return .other
        }
    }

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
