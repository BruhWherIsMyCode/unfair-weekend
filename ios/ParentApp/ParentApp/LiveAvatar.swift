import Foundation
import LiveKit
import Observation
import ParentCallKit
import os

/// What the call screen shows of the live avatar: its video track while connected.
@MainActor
@Observable
final class LiveAvatarFeed {
    var track: VideoTrack?
    /// Set by the video view once frames are on screen; until then the clip or placeholder stays visible.
    var rendering = false
}

/// Simli's live avatar, the native counterpart of simli-client 3.2.0's LiveKit transport:
///
/// 1. Open `ws_url?session_token=…` (the backend minted the token; the account key never reaches the phone).
/// 2. Simli answers with JSON holding `livekit_url` and `livekit_token`: join that room. Its video track is the
///    avatar; its audio track is the line's voice, already in sync with the lips.
/// 3. Each line: send its PCM16 as binary messages. Simli says `SPEAK` when the avatar starts and `SILENT` when it
///    is done. `SKIP` silences it, `DONE` ends the session.
///
/// UNTESTED against Simli from this machine (no key here). The protocol follows the simli-client source.
final class SimliLiveAvatar: NSObject, LiveAvatarPlayer, RoomDelegate, @unchecked Sendable {
    enum LiveError: Error {
        case badSession
        case timedOut
        case closed
        case neverSpoke
    }

    let feed: LiveAvatarFeed

    private let lock = NSLock()
    private var generation = 0
    private var socket: URLSessionWebSocketTask?
    private var room: Room?
    private var broken = false
    private var connectWaiter: CheckedContinuation<Void, Error>?
    private var line = 0
    private var lineWaiter: CheckedContinuation<Void, Error>?
    private var spoke = false
    private var lineStartedAt: TimeInterval = 0
    private var submittedBytes = 0
    private let log = Logger(subsystem: "HeartCall", category: "live-avatar")

    static let connectTimeout: TimeInterval = 15
    /// No `SPEAK` this long after the audio was sent: playback is uncertain and must not be replayed.
    static let speakTimeout: TimeInterval = 8
    /// Lets the last WebRTC audio drain before the microphone opens for her answer.
    static let drainNs: UInt64 = 300_000_000

    @MainActor
    override init() {
        feed = LiveAvatarFeed()
        super.init()
        // The app owns the audio session (play and record, voice chat, speaker): LiveKit must not switch it.
        AudioManager.shared.audioSession.isAutomaticConfigurationEnabled = false
    }

    // MARK: LiveAvatarPlayer

    func connect(_ session: LiveAvatarSession) async throws {
        await disconnect()
        guard var components = URLComponents(string: session.wsUrl), components.scheme == "wss" else {
            throw LiveError.badSession
        }
        components.queryItems = [URLQueryItem(name: "session_token", value: session.sessionToken)]
        guard let url = components.url else {
            throw LiveError.badSession
        }
        try PhoneDevices.activateAudio()
        let task = URLSession.shared.webSocketTask(with: url)
        let room = Room(delegate: self, connectOptions: ConnectOptions(autoSubscribe: true))
        let ticket = lock.withLock { () -> Int in
            generation += 1
            broken = false
            socket = task
            self.room = room
            return generation
        }
        trace("connect", ticket: ticket)
        try await withTaskCancellationHandler {
            try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
                lock.withLock { connectWaiter = continuation }
                receive(task, ticket: ticket)
                task.resume()
                Task { [weak self] in
                    try? await Task.sleep(nanoseconds: UInt64(Self.connectTimeout * 1_000_000_000))
                    guard let self, self.lock.withLock({ self.connectWaiter != nil }) else { return }
                    self.fail(LiveError.timedOut, ticket: ticket, reason: "connect_timeout")
                }
            }
        } onCancel: {
            self.fail(CancellationError(), ticket: ticket, reason: "connect_cancelled")
        }
    }

    func speak(pcm16k: Data, durationMs: Int) async throws {
        try Task.checkCancellation()
        let (task, current) = lock.withLock { () -> (URLSessionWebSocketTask?, Int) in
            line += 1
            spoke = false
            lineStartedAt = ProcessInfo.processInfo.systemUptime
            submittedBytes = 0
            return (broken ? nil : socket, line)
        }
        guard let task else {
            trace("not_started", line: current)
            throw LivePlaybackFailure.notStarted
        }
        trace("line_begin", line: current)
        do {
            try await withTaskCancellationHandler {
                try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
                    // A disconnect between preflight and registration must resolve this line too.
                    let registered = lock.withLock { () -> Bool in
                        guard !broken else { return false }
                        lineWaiter = continuation
                        return true
                    }
                    guard registered else {
                        continuation.resume(throwing: LivePlaybackFailure.notStarted)
                        return
                    }
                    if Task.isCancelled {
                        finishLine(current, error: CancellationError())
                        return
                    }
                    Task { [weak self] in
                        do {
                            // Like simli-client: the whole line at once, in 6000-byte binary messages.
                            var offset = 0
                            while offset < pcm16k.count {
                                guard let self,
                                      self.lock.withLock({ self.line == current && self.lineWaiter != nil && !self.broken })
                                else { return }
                                let end = min(offset + 6000, pcm16k.count)
                                try await task.send(.data(pcm16k.subdata(in: offset..<end)))
                                self.lock.withLock {
                                    if self.line == current {
                                        self.submittedBytes += end - offset
                                    }
                                }
                                offset = end
                            }
                            self?.trace("audio_submitted", line: current)
                        } catch {
                            self?.trace("audio_send_failed", line: current)
                            self?.finishLine(current, error: LiveError.closed)
                            return
                        }
                        try? await Task.sleep(nanoseconds: UInt64(Self.speakTimeout * 1_000_000_000))
                        guard let self,
                              self.lock.withLock({ self.line == current && self.lineWaiter != nil }) else { return }
                        if !self.lock.withLock({ self.spoke }) {
                            self.trace("speak_timeout", line: current)
                            self.finishLine(current, error: LiveError.neverSpoke)
                            return
                        }
                        // Preserve the existing duration allowance when no SILENT arrives.
                        let rest = max(0, Double(durationMs) / 1000 + 6 - Self.speakTimeout)
                        try? await Task.sleep(nanoseconds: UInt64(rest * 1_000_000_000))
                        self.finishLine(current, error: nil)
                    }
                }
            } onCancel: {
                self.finishLine(current, error: CancellationError())
            }
            try await Task.sleep(nanoseconds: Self.drainNs)
        } catch {
            if error is CancellationError || Task.isCancelled {
                trace("line_cancelled", line: current)
                throw CancellationError()
            }
            if let failure = error as? LivePlaybackFailure, failure == .notStarted {
                trace("not_started", line: current)
                throw failure
            }
            // A failed send or missing signal cannot prove that the person heard nothing.
            trace("line_interrupted", line: current)
            throw LivePlaybackFailure.interrupted
        }
    }

    func stop() async {
        let (task, current) = lock.withLock { (broken ? nil : socket, line) }
        trace("stop", line: current)
        try? await task?.send(.string("SKIP"))
        finishLine(current, error: CancellationError())
    }

    func disconnect() async {
        let (task, room, connecting, speaking) = lock.withLock {
            () -> (URLSessionWebSocketTask?, Room?, CheckedContinuation<Void, Error>?, CheckedContinuation<Void, Error>?) in
            generation += 1
            broken = true
            defer {
                socket = nil
                self.room = nil
                connectWaiter = nil
                lineWaiter = nil
            }
            return (socket, self.room, connectWaiter, lineWaiter)
        }
        connecting?.resume(throwing: CancellationError())
        speaking?.resume(throwing: CancellationError())
        await clearFeed()
        if let task {
            try? await task.send(.string("DONE"))
            task.cancel(with: .goingAway, reason: nil)
        }
        await room?.disconnect()
    }

    // MARK: Simli signalling

    private func receive(_ task: URLSessionWebSocketTask, ticket: Int) {
        task.receive { [weak self] result in
            guard let self, self.isCurrent(ticket) else { return }
            switch result {
            case .failure:
                self.fail(LiveError.closed, ticket: ticket, reason: "socket_receive_failed")
            case .success(let message):
                if case .string(let text) = message {
                    self.handle(text, ticket: ticket)
                }
                self.receive(task, ticket: ticket)
            }
        }
    }

    private func handle(_ text: String, ticket: Int) {
        let first = text.uppercased().split(separator: " ").first.map(String.init) ?? ""
        switch first {
        case "START", "ACK":
            return
        case "SPEAK":
            lock.withLock { spoke = true }
            trace("speak", ticket: ticket)
        case "SILENT":
            trace("silent", ticket: ticket)
            let (spoken, current) = lock.withLock { (spoke, line) }
            if spoken {
                finishLine(current, error: nil)
            }
        case "STOP", "CLOSING", "RATE", "ERROR", "ERROR:":
            let category = first == "ERROR:" ? "error" : first.lowercased()
            fail(LiveError.closed, ticket: ticket, reason: "provider_\(category)")
        default:
            if first.contains("LIVEKIT") || first.contains("SDP") {
                join(text, ticket: ticket)
            } else if first.contains("ENDFRAME") {
                fail(LiveError.closed, ticket: ticket, reason: "provider_end_frame")
            }
            // DESTINATION, VIDEO_METADATA, warnings: nothing to do.
        }
    }

    private func join(_ text: String, ticket: Int) {
        guard let info = try? JSONSerialization.jsonObject(with: Data(text.utf8)) as? [String: Any],
              let url = info["livekit_url"] as? String, let token = info["livekit_token"] as? String,
              let room = lock.withLock({ isCurrentLocked(ticket) ? self.room : nil }) else {
            fail(LiveError.badSession, ticket: ticket, reason: "invalid_room_data")
            return
        }
        Task { [weak self] in
            do {
                try await room.connect(url: url, token: token)
            } catch {
                self?.fail(LiveError.closed, ticket: ticket, reason: "room_join_failed")
            }
        }
    }

    // MARK: RoomDelegate

    func room(_ room: Room, participant: RemoteParticipant, didSubscribeTrack publication: RemoteTrackPublication) {
        guard let track = publication.track as? VideoTrack,
              let ticket = lock.withLock({ self.room === room ? generation : nil }) else { return }
        Task { @MainActor [feed] in
            feed.rendering = false
            feed.track = track
        }
        let waiter = lock.withLock { () -> CheckedContinuation<Void, Error>? in
            guard isCurrentLocked(ticket) else { return nil }
            defer { connectWaiter = nil }
            return connectWaiter
        }
        waiter?.resume()
    }

    func room(_ room: Room, didDisconnectWithError error: LiveKitError?) {
        guard let ticket = lock.withLock({ self.room === room ? generation : nil }) else { return }
        fail(LiveError.closed, ticket: ticket, reason: "room_disconnected")
    }

    func room(_ room: Room, didFailToConnectWithError error: LiveKitError?) {
        guard let ticket = lock.withLock({ self.room === room ? generation : nil }) else { return }
        fail(LiveError.closed, ticket: ticket, reason: "room_connect_failed")
    }

    // MARK: helpers

    private func isCurrent(_ ticket: Int) -> Bool {
        lock.withLock { isCurrentLocked(ticket) }
    }

    private func isCurrentLocked(_ ticket: Int) -> Bool {
        ticket == generation && !broken
    }

    /// The session is unusable. The current line's playback is uncertain; future lines may use the fallback.
    private func fail(_ error: Error, ticket: Int, reason: String) {
        trace(reason, ticket: ticket)
        let (connecting, speaking) = lock.withLock {
            () -> (CheckedContinuation<Void, Error>?, CheckedContinuation<Void, Error>?) in
            guard isCurrentLocked(ticket) else { return (nil, nil) }
            broken = true
            defer {
                connectWaiter = nil
                lineWaiter = nil
            }
            return (connectWaiter, lineWaiter)
        }
        connecting?.resume(throwing: error)
        speaking?.resume(throwing: error)
        if lock.withLock({ ticket == generation }) {
            Task { await clearFeed() }
        }
    }

    private func finishLine(_ current: Int, error: Error?) {
        let waiter = lock.withLock { () -> CheckedContinuation<Void, Error>? in
            guard current == line else { return nil }
            defer { lineWaiter = nil }
            return lineWaiter
        }
        guard let waiter else { return }
        trace(error == nil ? "line_completed" : "line_failed", line: current)
        if let error {
            waiter.resume(throwing: error)
        } else {
            waiter.resume()
        }
    }

    /// Diagnostics contain only local counters and fixed categories, never credentials, URLs or media.
    private func trace(_ event: String, line current: Int? = nil, ticket: Int? = nil) {
        let state = lock.withLock {
            (generation, line, spoke, submittedBytes,
             lineStartedAt == 0 ? 0 : Int(max(0, ProcessInfo.processInfo.systemUptime - lineStartedAt) * 1000))
        }
        log.info("event=\(event, privacy: .public) session=\(ticket ?? state.0) line=\(current ?? state.1) spoke=\(state.2) submitted_bytes=\(state.3) elapsed_ms=\(state.4)")
    }

    @MainActor
    private func clearFeed() {
        feed.track = nil
        feed.rendering = false
    }
}
