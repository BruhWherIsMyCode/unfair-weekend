import Foundation
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif
import XCTest
@testable import ParentCallKit

final class PlaybackFallbackTests: XCTestCase {
    func testPartialLiveFailureDoesNotReplayOrReportCompletion() async throws {
        try await checkNoReplay(live: .failed)
    }

    func testExplicitInterruptedLiveFailureDoesNotReplayOrReportCompletion() async throws {
        try await checkNoReplay(live: .interrupted)
    }

    private func checkNoReplay(live: LivePlaybackSpy.Outcome) async throws {
        let call = PlaybackHarness(live: live)
        try await call.start()
        call.server.say("instruction", line: "task_arms")
        try await waitFor { !call.server.terminalEvents.isEmpty }
        XCTAssertEqual(call.server.terminalEvents, ["command.failed"])
        let liveCalls = await call.live.spoken
        let clipCalls = await call.clips.count
        let voiceCalls = await call.voice.count
        XCTAssertEqual(liveCalls, 1)
        XCTAssertEqual(clipCalls, 0)
        XCTAssertEqual(voiceCalls, 0)
        XCTAssertEqual(call.server.videoRequests, 0)
        await call.stop()
    }

    func testOnlyDefinitelyNotStartedFallsBackToTheClip() async throws {
        let call = PlaybackHarness(live: .notStarted)
        try await call.start()
        call.server.say("instruction", line: "task_arms")
        try await waitFor { !call.server.terminalEvents.isEmpty }
        XCTAssertEqual(call.server.terminalEvents, ["playback.completed"])
        let clipCalls = await call.clips.count
        let voiceCalls = await call.voice.count
        XCTAssertEqual(clipCalls, 1)
        XCTAssertEqual(voiceCalls, 0)
        XCTAssertEqual(call.server.videoRequests, 1)
        await call.stop()
    }

    func testLiveCancellationStopsWithoutFallbackOrCompletion() async throws {
        let call = PlaybackHarness(live: .cancelled)
        try await call.start()
        call.server.say("instruction", line: "task_arms")
        try await waitFor { await call.live.stops == 1 }
        XCTAssertEqual(call.server.terminalEvents, [])
        let clipCalls = await call.clips.count
        let voiceCalls = await call.voice.count
        XCTAssertEqual(clipCalls, 0)
        XCTAssertEqual(voiceCalls, 0)
        await call.stop()
    }

    func testLostCompletionAcknowledgmentRetriesOnlyTheSameEvent() async throws {
        let call = PlaybackHarness(lostAcks: 1)
        try await call.start()
        call.server.say("instruction", line: "task_arms")
        try await waitFor { call.server.ackBodies.count >= 2 }
        let bodies = call.server.ackBodies
        XCTAssertEqual(bodies.count, 2)
        XCTAssertEqual(bodies[0], bodies[1])
        let liveCalls = await call.live.spoken
        let clipCalls = await call.clips.count
        let voiceCalls = await call.voice.count
        XCTAssertEqual(liveCalls, 1)
        XCTAssertEqual(clipCalls, 0)
        XCTAssertEqual(voiceCalls, 0)
        XCTAssertEqual(call.server.videoRequests, 0)
        await call.stop()
    }

    func testExhaustedAcknowledgmentsDoNotReplayOrReportPlaybackFailure() async throws {
        let call = PlaybackHarness(lostAcks: 5)
        try await call.start()
        call.server.say("instruction", line: "task_arms")
        try await waitFor { call.server.ackBodies.count == 3 }
        for _ in 0..<10 { await Task.yield() }
        XCTAssertEqual(Set(call.server.ackBodies).count, 1)
        XCTAssertEqual(call.server.terminalEvents, [])
        let liveCalls = await call.live.spoken
        let clipCalls = await call.clips.count
        let voiceCalls = await call.voice.count
        XCTAssertEqual(liveCalls, 1)
        XCTAssertEqual(clipCalls, 0)
        XCTAssertEqual(voiceCalls, 0)
        await call.stop()
    }

    func testCancellingDuringAcknowledgmentBackoffStopsRetries() async throws {
        let call = PlaybackHarness(lostAcks: 5)
        try await call.start()
        call.server.say("instruction", line: "task_arms")
        try await waitFor { call.server.ackBodies.count == 1 }
        call.server.cancel("instruction")
        try await waitFor { call.server.cancelAcknowledgments == 1 }
        // Longer than the first backoff: a cancelled command must not send its pending receipt later.
        try await Task.sleep(nanoseconds: 300_000_000)
        XCTAssertEqual(call.server.ackBodies.count, 1)
        XCTAssertEqual(call.server.terminalEvents, [])
        let liveCalls = await call.live.spoken
        let clipCalls = await call.clips.count
        XCTAssertEqual(liveCalls, 1)
        XCTAssertEqual(clipCalls, 0)
        await call.stop()
    }

    func testHealthyFixedAndGeneratedQuestionBothStayLive() async throws {
        let call = PlaybackHarness()
        try await call.start()
        call.server.say("fixed", line: "q_feeling")
        try await waitFor { call.server.terminalEvents.count == 1 }
        call.server.say("generated", line: "agent_remark", text: "What is bothering you most today?")
        try await waitFor { call.server.terminalEvents.count == 2 }
        XCTAssertEqual(call.server.terminalEvents, ["playback.completed", "playback.completed"])
        let liveCalls = await call.live.spoken
        let clipCalls = await call.clips.count
        let voiceCalls = await call.voice.count
        XCTAssertEqual(liveCalls, 2)
        XCTAssertEqual(clipCalls, 0)
        XCTAssertEqual(voiceCalls, 0)
        XCTAssertEqual(call.server.videoRequests, 0)
        await call.stop()
    }

    private func waitFor(_ condition: () async -> Bool) async throws {
        for _ in 0..<500 {
            if await condition() { return }
            try await Task.sleep(nanoseconds: 10_000_000)
        }
        XCTFail("The synthetic call did not reach the expected event")
        throw PlaybackTestError.timeout
    }
}

private enum PlaybackTestError: Error {
    case partialPlayback, timeout
}

private actor LivePlaybackSpy: LiveAvatarPlayer {
    enum Outcome { case completed, failed, interrupted, notStarted, cancelled }
    let outcome: Outcome
    private(set) var spoken = 0
    private(set) var stops = 0

    init(_ outcome: Outcome) { self.outcome = outcome }
    func connect(_ session: LiveAvatarSession) async throws {}
    func speak(pcm16k: Data, durationMs: Int) async throws {
        spoken += 1
        if case .failed = outcome { throw PlaybackTestError.partialPlayback }
        if case .interrupted = outcome { throw LivePlaybackFailure.interrupted }
        if case .notStarted = outcome { throw LivePlaybackFailure.notStarted }
        if case .cancelled = outcome { throw CancellationError() }
    }
    func stop() async { stops += 1 }
    func disconnect() async {}
}

private actor ClipPlaybackSpy: AvatarClipPlayer {
    private(set) var count = 0
    func play(mp4: Data) async throws { count += 1 }
    func stop() async {}
}

private actor VoicePlaybackSpy: SpeechPlayer {
    private(set) var count = 0
    func play(pcm16k: Data, durationMs: Int) async throws { count += 1 }
    func stop() async {}
}

private final class PlaybackHarness {
    let server: PlaybackServer
    let client: CallClient
    let live: LivePlaybackSpy
    let clips = ClipPlaybackSpy()
    let voice = VoicePlaybackSpy()
    private let phases = PlaybackPhases()
    private let session: URLSession

    init(live outcome: LivePlaybackSpy.Outcome = .completed, lostAcks: Int = 0) {
        server = PlaybackServer(lostAcks: lostAcks)
        live = LivePlaybackSpy(outcome)
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [PlaybackHTTP.self]
        session = URLSession(configuration: configuration)
        let api = CallAPI(baseURL: URL(string: "https://\(server.host)")!, demoKey: "synthetic", session: session)
        let devices = ParentDevices(player: voice, recorder: FakeRecorder(), camera: FakeCamera(),
                                    microphone: FakeMicrophone(timeScale: 0), clips: clips, live: live)
        let phases = phases
        client = CallClient(api: api, deviceId: "test-device", personId: "test-person", devices: devices,
                            onPhase: { phases.receive($0) })
    }

    func start() async throws {
        try await client.start()
        for _ in 0..<500 {
            if phases.ringing { try await client.answer(); return }
            try await Task.sleep(nanoseconds: 10_000_000)
        }
        XCTFail("The synthetic incoming call did not arrive")
        throw PlaybackTestError.timeout
    }

    func stop() async {
        await client.stop()
        session.invalidateAndCancel()
        PlaybackHTTP.remove(server.host)
    }
}

private final class PlaybackPhases: @unchecked Sendable {
    private let lock = NSLock()
    private var receivedRinging = false
    var ringing: Bool { lock.withLock { receivedRinging } }
    func receive(_ phase: CallPhase) {
        if case .ringing = phase { lock.withLock { receivedRinging = true } }
    }
}

/// Synthetic HTTP and SSE only: the real CallClient handles incoming commands and posts their outcomes.
private final class PlaybackServer: @unchecked Sendable {
    let host = "playback-\(UUID().uuidString.lowercased()).invalid"
    private let lock = NSLock()
    private var stream: PlaybackHTTP?
    private var acknowledgments: [Data] = []
    private var terminals: [String] = []
    private var cancellations = 0
    private var videos = 0
    private var lostAcks: Int
    var ackBodies: [Data] { lock.withLock { acknowledgments } }
    var terminalEvents: [String] { lock.withLock { terminals } }
    var videoRequests: Int { lock.withLock { videos } }
    var cancelAcknowledgments: Int { lock.withLock { cancellations } }

    init(lostAcks: Int) {
        self.lostAcks = lostAcks
        PlaybackHTTP.add(self)
    }

    func receive(_ request: PlaybackHTTP) {
        let path = request.request.url!.path
        if path.hasSuffix("/events") {
            lock.withLock { stream = request }
            request.openStream()
            emit("call.incoming", ["call_id": "test-call", "caller_name": "Test", "ring_timeout_s": 30])
        } else if path.hasSuffix("/answer") {
            request.respond(["call_id": "test-call", "person_name": "Test", "voiced": true])
        } else if path.hasSuffix("/avatar/live") {
            request.respond(["session_token": "synthetic", "ws_url": "wss://invalid.example"])
        } else if path.contains("/speech/") {
            request.respond(Data(repeating: 0, count: 32))
        } else if path.contains("/video/") {
            lock.withLock { videos += 1 }
            request.respond(Data("synthetic clip".utf8))
        } else if path.hasSuffix("/client-events") {
            let body = request.request.httpBody ?? Data()
            let json = (try? JSONSerialization.jsonObject(with: body)) as? [String: Any]
            let kind = json?["type"] as? String ?? ""
            let lose = lock.withLock { () -> Bool in
                if kind == "command.cancelled" { cancellations += 1 }
                if kind == "playback.completed" {
                    acknowledgments.append(body)
                    if lostAcks > 0 { lostAcks -= 1; return true }
                }
                if kind == "playback.completed" || kind == "command.failed" { terminals.append(kind) }
                return false
            }
            if lose { request.fail(URLError(.networkConnectionLost)) }
            else { request.respond([:]) }
        } else {
            request.respond([:])
        }
    }

    func say(_ id: String, line: String, text: String = "Synthetic spoken line.") {
        emit("command.execute", ["event_id": "exec_\(id)", "call_id": "test-call", "type": "command.execute",
             "command": ["command_id": id, "kind": "say", "line_id": line, "text": text,
                         "audio_path": "/v1/calls/test-call/speech/\(id)", "duration_ms": 100,
                         "video_path": "/v1/calls/test-call/video/\(id)"]])
    }

    func cancel(_ id: String) {
        emit("command.cancel", ["event_id": "cancel_\(id)", "call_id": "test-call", "type": "command.cancel",
                                "command_id": id, "reason": "timeout"])
    }

    private func emit(_ kind: String, _ body: [String: Any]) {
        let json = try! JSONSerialization.data(withJSONObject: body)
        let bytes = Data("event: \(kind)\ndata: \(String(decoding: json, as: UTF8.self))\n\n".utf8)
        let target = lock.withLock { stream }
        if let target { target.client?.urlProtocol(target, didLoad: bytes) }
    }
}

private final class PlaybackHTTP: URLProtocol {
    private static let lock = NSLock()
    private static var servers: [String: PlaybackServer] = [:]
    static func add(_ server: PlaybackServer) { lock.withLock { servers[server.host] = server } }
    static func remove(_ host: String) { _ = lock.withLock { servers.removeValue(forKey: host) } }
    override class func canInit(with request: URLRequest) -> Bool {
        lock.withLock { request.url?.host.map { servers[$0] != nil } ?? false }
    }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        let server = Self.lock.withLock { request.url?.host.flatMap { Self.servers[$0] } }
        server?.receive(self)
    }
    override func stopLoading() {}
    func respond(_ json: [String: Any]) { respond(try! JSONSerialization.data(withJSONObject: json)) }
    func respond(_ bytes: Data) {
        client?.urlProtocol(self, didReceive: HTTPURLResponse(url: request.url!, statusCode: 200,
                                                            httpVersion: nil, headerFields: nil)!,
                            cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: bytes)
        client?.urlProtocolDidFinishLoading(self)
    }
    func openStream() {
        client?.urlProtocol(self, didReceive: HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil,
                                                            headerFields: ["Content-Type": "text/event-stream"])!,
                            cacheStoragePolicy: .notAllowed)
    }
    func fail(_ error: Error) { client?.urlProtocol(self, didFailWithError: error) }
}
