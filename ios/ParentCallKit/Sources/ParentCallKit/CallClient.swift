import Foundation

/// The parent-side call engine. It owns no UI: it registers the phone, listens to the device event stream,
/// rings, executes the backend's commands with the injected devices and reports everything back.
///
/// The screen observes `onPhase`. Phases never contain measurements or a verdict.
public actor CallClient {
    public let api: CallAPI
    public let deviceId: String
    public let personId: String
    private let devices: ParentDevices
    private let onPhase: @Sendable (CallPhase) -> Void
    private let onEvent: @Sendable (CallEvent) -> Void

    private var connection: SSEConnection?
    private var listenTask: Task<Void, Never>?
    private var lastEventId: String?
    private var callId: String?
    private var seenCommands: Set<String> = []
    private var cancelled: Set<String> = []
    private var running: [String: Task<Void, Never>] = [:]
    private var resourceSequence: [Resource: Int] = [:]
    private var resourceState: [Resource: Bool] = [:]
    private var eventCounter = 0
    private var stopped = false

    private enum LiveState {
        case off
        case connecting(Date)
        case ready
    }

    private var liveState: LiveState = .off
    private var liveTask: Task<Void, Never>?
    private var liveGeneration = 0
    /// How long the first line waits for the live avatar before it plays as a clip instead.
    static let liveConnectWait: TimeInterval = 10

    public init(api: CallAPI, deviceId: String, personId: String, devices: ParentDevices,
                onPhase: @escaping @Sendable (CallPhase) -> Void,
                onEvent: @escaping @Sendable (CallEvent) -> Void = { _ in }) {
        self.api = api
        self.deviceId = deviceId
        self.personId = personId
        self.devices = devices
        self.onPhase = onPhase
        self.onEvent = onEvent
    }

    // MARK: lifecycle

    /// Registers the phone and keeps the event stream open, reconnecting after drops.
    public func start() async throws {
        try await api.register(deviceId: deviceId, personId: personId)
        stopped = false
        listenTask = Task { [weak self] () -> Void in
            guard let self else { return }
            await self.listenLoop()
        }
        onPhase(.idle)
    }

    public func stop() async {
        stopped = true
        listenTask?.cancel()
        connection?.cancel()
        await endLocalWork()
    }

    private func listenLoop() async {
        while !stopped && !Task.isCancelled {
            let connection = api.events(deviceId: deviceId, lastEventId: lastEventId)
            self.connection = connection
            do {
                for try await message in connection.messages {
                    if let id = message.id {
                        lastEventId = id
                    }
                    await handle(message)
                }
            } catch {
                if case APIError.http(status: 404, _) = error {
                    try? await api.register(deviceId: deviceId, personId: personId)
                }
            }
            if stopped || Task.isCancelled {
                return
            }
            try? await Task.sleep(nanoseconds: 1_000_000_000)
        }
    }

    // MARK: user actions

    /// She tapped the green button.
    public func answer() async throws {
        guard let callId else { return }
        onPhase(.connecting)
        _ = try await api.answer(callId: callId)
        startLiveAvatar(callId: callId)
        onEvent(.connected)
        await reportResource(.camera, available: resourceState[.camera] ?? true, commandId: nil, problem: nil)
        await reportResource(.microphone, available: resourceState[.microphone] ?? true, commandId: nil, problem: nil)
        try await post(["type": "client.ready", "command_id": nil, "error": nil])
    }

    public func decline() async throws {
        guard let callId else { return }
        try await api.decline(callId: callId)
        self.callId = nil
        onPhase(.idle)
    }

    /// The red button. Always available during a call.
    public func hangUp() async {
        guard callId != nil else { return }
        try? await post(["type": "hangup", "command_id": nil, "error": nil])
        await endLocalWork()
        callId = nil
        onPhase(.ended(reason: "hangup"))
        onEvent(.ended(reason: "hangup"))
    }

    /// The app noticed that the camera or microphone came or went (permission change, interruption, track end).
    public func resourceChanged(_ resource: Resource, available: Bool, problem: ResourceProblem? = nil) async {
        resourceState[resource] = available
        guard callId != nil else { return }
        await reportResource(resource, available: available, commandId: activeAttempt,
                             problem: available ? nil : (problem ?? .unknown))
    }

    // MARK: events from the backend

    private func handle(_ message: SSEMessage) async {
        let data = Data(message.data.utf8)
        switch message.event {
        case "call.incoming":
            guard let event = try? JSON.decoder.decode(IncomingCallEvent.self, from: data) else { return }
            if callId == event.callId {
                return
            }
            callId = event.callId
            seenCommands = []
            cancelled = []
            onPhase(.ringing(callId: event.callId, callerName: event.callerName))
            onEvent(.ringing(callId: event.callId, title: event.callerName))
        case "call.ended":
            guard let event = try? JSON.decoder.decode(CallEndedEvent.self, from: data) else { return }
            guard event.callId == callId else { return }
            await endLocalWork()
            callId = nil
            onPhase(.ended(reason: event.reason))
            onEvent(.ended(reason: event.reason))
        case "command.execute":
            guard let event = try? JSON.decoder.decode(CommandExecuteEvent.self, from: data) else { return }
            guard event.callId == callId else { return }
            let id = event.command.commandId
            if seenCommands.contains(id) {
                return
            }
            seenCommands.insert(id)
            let task = Task { [weak self] () -> Void in
                guard let self else { return }
                await self.execute(event.command)
            }
            running[id] = task
        case "command.cancel":
            guard let event = try? JSON.decoder.decode(CommandCancelEvent.self, from: data) else { return }
            guard event.callId == callId else { return }
            await cancel(commandId: event.commandId)
        case "task.progress":
            guard let event = try? JSON.decoder.decode(TaskProgressEvent.self, from: data) else { return }
            guard event.callId == callId else { return }
            onEvent(.hint(event.hint))
            if event.phase == "capturing" {
                onPhase(.task(name: currentTaskName ?? "", secondsLeft: secondsLeft(elapsedMs: event.elapsedMs),
                              hint: event.hint))
            }
        default:
            return
        }
    }

    private var currentTaskName: String?
    private var activeAttempt: String?
    private var currentTaskDurationMs: Int = 0

    private func secondsLeft(elapsedMs: Int) -> Int {
        max(0, (currentTaskDurationMs - elapsedMs + 999) / 1000)
    }

    private func cancel(commandId: String) async {
        cancelled.insert(commandId)
        if let task = running.removeValue(forKey: commandId) {
            task.cancel()
        }
        await devices.player.stop()
        await devices.clips?.stop()
        await devices.live?.stop()
        await devices.recorder.stop()
        await devices.camera.stop()
        await devices.microphone.stop()
        try? await post(["type": "command.cancelled", "command_id": commandId, "error": nil])
    }

    // MARK: commands

    private func execute(_ command: ClientCommand) async {
        let id = command.commandId
        defer { running[id] = nil }
        do {
            switch command {
            case .say(let say):
                try await performSay(say)
            case .capture(let capture):
                if capture.purpose.kind == "answer" {
                    try await performAnswer(capture)
                } else {
                    try await performTask(capture)
                }
            }
        } catch is CancellationError {
            return
        } catch {
            if cancelled.contains(id) || callId == nil {
                return
            }
            let code = failureCode(for: command, error: error)
            try? await post(["type": "command.failed", "command_id": id,
                             "error": ["code": code, "message": "The phone could not complete this step."]])
        }
    }

    private func failureCode(for command: ClientCommand, error: Error) -> String {
        if case .say = command {
            return "playback_failed"
        }
        return "capture_failed"
    }

    private func performSay(_ say: SayCommand) async throws {
        onPhase(.speaking(caption: say.text))
        if let live = await liveAvatar() {
            let audio = try await api.speech(path: say.audioPath)
            try Task.checkCancellation()
            onEvent(.speaking(lineId: say.lineId, text: say.text, durationMs: say.durationMs, at: Date()))
            do {
                try await live.speak(pcm16k: audio, durationMs: say.durationMs)
                try Task.checkCancellation()
                try await post(["type": "playback.completed", "command_id": say.commandId, "error": nil])
                return
            } catch {
                await live.stop()
                if error is CancellationError || Task.isCancelled {
                    throw CancellationError()
                }
                // The live session broke: this line and the rest of the call play as clips or voice.
                await dropLiveAvatar()
            }
        }
        if let videoPath = say.videoPath, let clips = devices.clips {
            if try await playClip(say, path: videoPath, with: clips) {
                try await post(["type": "playback.completed", "command_id": say.commandId, "error": nil])
                return
            }
        }
        let audio = try await api.speech(path: say.audioPath)
        try Task.checkCancellation()
        onEvent(.speaking(lineId: say.lineId, text: say.text, durationMs: say.durationMs, at: Date()))
        try await devices.player.play(pcm16k: audio, durationMs: say.durationMs)
        try Task.checkCancellation()
        try await post(["type": "playback.completed", "command_id": say.commandId, "error": nil])
    }

    // MARK: live avatar

    /// Asks the backend for a Simli session and connects in the background while the greeting is prepared.
    private func startLiveAvatar(callId: String) {
        guard let live = devices.live else { return }
        liveTask?.cancel()
        liveGeneration += 1
        let generation = liveGeneration
        liveState = .connecting(Date())
        liveTask = Task { [api, weak self] () -> Void in
            var connected = false
            do {
                let session = try await api.liveAvatar(callId: callId)
                try Task.checkCancellation()
                try await live.connect(session)
                connected = !Task.isCancelled
            } catch {
                connected = false
            }
            await self?.liveConnectFinished(connected, generation: generation)
        }
    }

    private func liveConnectFinished(_ connected: Bool, generation: Int) async {
        guard generation == liveGeneration else { return }
        liveTask = nil
        if connected {
            liveState = .ready
        } else {
            liveState = .off
            await devices.live?.disconnect()
        }
    }

    /// The live avatar if it is connected, waiting briefly while it is still connecting.
    private func liveAvatar() async -> LiveAvatarPlayer? {
        guard let live = devices.live else { return nil }
        while true {
            switch liveState {
            case .off:
                return nil
            case .ready:
                return live
            case .connecting(let since):
                if Date().timeIntervalSince(since) > Self.liveConnectWait {
                    // Still connecting: this line plays as a clip; a later line may still go live.
                    return nil
                }
                try? await Task.sleep(nanoseconds: 100_000_000)
                if Task.isCancelled {
                    return nil
                }
            }
        }
    }

    private func dropLiveAvatar() async {
        liveGeneration += 1
        liveTask?.cancel()
        liveTask = nil
        liveState = .off
        await devices.live?.disconnect()
    }

    /// Plays the line's prepared clip. True only when it played to its end. False when the clip could not be
    /// fetched or played: the clip is stopped first, so the PCM fallback never plays over it. Cancellation throws.
    private func playClip(_ say: SayCommand, path: String, with clips: AvatarClipPlayer) async throws -> Bool {
        do {
            let mp4 = try await api.video(path: path)
            try Task.checkCancellation()
            onEvent(.speaking(lineId: say.lineId, text: say.text, durationMs: say.durationMs, at: Date()))
            try await clips.play(mp4: mp4)
            try Task.checkCancellation()
            return true
        } catch {
            await clips.stop()
            if error is CancellationError || Task.isCancelled {
                throw CancellationError()
            }
            return false
        }
    }

    private func performAnswer(_ capture: CaptureCommand) async throws {
        onPhase(.listening(questionId: capture.purpose.questionId ?? ""))
        onEvent(.listening(questionId: capture.purpose.questionId ?? ""))
        let (audio, type) = try await devices.recorder.record(maxMs: capture.settings.durationMs,
                                                               stopOnSilenceMs: capture.settings.stopOnSilenceMs)
        try Task.checkCancellation()
        guard let callId else { return }
        try await api.uploadAnswer(callId: callId, commandId: capture.commandId, audio: audio, contentType: type)
    }

    private func performTask(_ capture: CaptureCommand) async throws {
        let attempt = capture.commandId
        let settings = capture.settings
        guard let format = settings.format, let period = settings.samplePeriodMs else {
            throw APIError.badResponse
        }
        currentTaskName = capture.purpose.task
        activeAttempt = attempt
        currentTaskDurationMs = settings.durationMs
        onPhase(.task(name: capture.purpose.task ?? "", secondsLeft: settings.durationMs / 1000, hint: nil))
        onEvent(.captureCommanded(task: capture.purpose.task ?? "", durationMs: settings.durationMs))
        let video = format == "jpeg"
        if video {
            try await devices.camera.start()
        } else {
            try await devices.microphone.start()
        }
        defer {
            if activeAttempt == attempt {
                activeAttempt = nil
            }
            Task { [devices] in
                await devices.camera.stop()
                await devices.microphone.stop()
            }
        }
        try await post(["type": "capture.started", "command_id": attempt, "error": nil])
        let started = Date()
        onEvent(.captureStarted(durationMs: settings.durationMs, at: started))
        var sequence = 0
        var lastSent = -1
        var lastMs = 0
        while true {
            try Task.checkCancellation()
            if cancelled.contains(attempt) || callId == nil {
                return
            }
            let elapsed = Int(Date().timeIntervalSince(started) * 1000)
            if elapsed >= settings.durationMs {
                break
            }
            let capturedMs = elapsed
            let payload: Data?
            if video {
                payload = try await devices.camera.jpegFrame()
            } else {
                payload = try await devices.microphone.pcmChunk(ms: period)
            }
            if let payload, let callId {
                do {
                    try await api.postSample(callId: callId, attemptId: attempt, sequence: sequence,
                                             capturedMs: min(capturedMs, 20000), format: format, data: payload)
                    lastSent = sequence
                    lastMs = min(capturedMs, 20000)
                    sequence += 1
                } catch APIError.http(status: 410, _) {
                    return
                } catch APIError.http(status: 503, _) {
                    continue
                }
            }
            if video {
                let next = started.addingTimeInterval(Double(sequence * period) / 1000)
                let wait = next.timeIntervalSinceNow
                if wait > 0 {
                    try await Task.sleep(nanoseconds: UInt64(wait * 1_000_000_000))
                }
            }
        }
        guard let callId else { return }
        onEvent(.captureFinished)
        try await api.finish(callId: callId, attemptId: attempt, lastSequence: lastSent, capturedUntilMs: lastMs)
        onPhase(.finishing)
    }

    // MARK: helpers

    private func nextEventId() -> String {
        eventCounter += 1
        return "\(deviceId)_e\(eventCounter)_\(Int(Date().timeIntervalSince1970 * 1000))"
    }

    private func post(_ event: [String: Any?]) async throws {
        guard let callId else { return }
        var body = event
        body["event_id"] = nextEventId()
        try await api.postEvent(callId: callId, body)
    }

    private func reportResource(_ resource: Resource, available: Bool, commandId: String?,
                                problem: ResourceProblem?) async {
        let sequence = (resourceSequence[resource] ?? -1) + 1
        resourceSequence[resource] = sequence
        try? await post([
            "type": "resource.changed",
            "command_id": commandId,
            "resource": resource.rawValue,
            "state": available ? "available" : "unavailable",
            "sequence": sequence,
            "reason": available ? nil : (problem ?? .unknown).rawValue,
        ])
    }

    private func endLocalWork() async {
        for (_, task) in running {
            task.cancel()
        }
        running = [:]
        await dropLiveAvatar()
        await devices.player.stop()
        await devices.clips?.stop()
        await devices.recorder.stop()
        await devices.camera.stop()
        await devices.microphone.stop()
    }
}
