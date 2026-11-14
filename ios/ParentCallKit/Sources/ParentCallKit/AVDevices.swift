#if canImport(AVFoundation) && canImport(UIKit)
import AVFoundation
import UIKit
import os

/// Real phone devices. Written for iOS 17; NOT compiled or run yet (built on Linux without AVFoundation).
/// Check on a device: playback end timing, frame orientation/mirroring, PCM conversion and permissions.
public enum PhoneDevices {
    public static func make(live: LiveAvatarPlayer? = nil) -> ParentDevices {
        let audio = AudioSessionBox.shared
        return ParentDevices(player: PCMPlayer(audio: audio), recorder: WAVRecorder(audio: audio),
                             camera: FrontCamera(), microphone: PCMMicrophone(audio: audio),
                             clips: AvatarClips(audio: audio), live: live)
    }

    /// Only the clip player (for the Simulator, whose other devices are fake).
    public static func makeClips() -> AvatarClips {
        AvatarClips(audio: AudioSessionBox.shared)
    }

    /// Configures and activates the call's audio session (play and record, speaker, mixes with other audio).
    /// For players outside this package, such as the live avatar, that need it active before they play.
    public static func activateAudio() throws {
        try AudioSessionBox.shared.activate()
    }

    public static func requestPermissions() async -> (camera: Bool, microphone: Bool) {
        let camera = await AVCaptureDevice.requestAccess(for: .video)
        let microphone = await AVAudioApplication.requestRecordPermission()
        return (camera, microphone)
    }
}

final class AudioSessionBox: @unchecked Sendable {
    static let shared = AudioSessionBox()

    private let lock = NSLock()
    private var configured = false

    /// Sets the category once; activates every time, because an interruption or CallKit may have deactivated it.
    /// `.mixWithOthers` keeps the silent keep-alive (and so the app) running when another app plays audio.
    func activate() throws {
        lock.lock()
        defer { lock.unlock() }
        let session = AVAudioSession.sharedInstance()
        if !configured {
            try session.setCategory(.playAndRecord, mode: .voiceChat,
                                    options: [.defaultToSpeaker, .allowBluetooth, .mixWithOthers])
            configured = true
        }
        try session.setActive(true)
    }
}

/// Demo workaround, not for the App Store. A silent loop on the call's audio session (with the "audio" background
/// mode) keeps the app running in the background. The event stream stays open, so a call can still ring through
/// CallKit with no VoIP push. iOS may still end the app (low memory, a restart): then she must open it again.
public final class BackgroundKeepAlive: @unchecked Sendable {
    private var player: AVAudioPlayer?
    private var observers: [NSObjectProtocol] = []
    private var watchdog: Timer?
    private var retryTimer: Timer?
    private var backgroundTask: UIBackgroundTaskIdentifier = .invalid
    /// The last activation failed: the player may report playing although the session is not active.
    private var activationFailed = false
    private let log = Logger(subsystem: "HeartCall", category: "keepalive")

    public init() {}

    /// Main thread. Whether the silent loop is playing right now.
    public var isPlaying: Bool { player?.isPlaying == true }
    /// Main thread. The last error from starting the loop, for diagnostics.
    public private(set) var lastError: String?

    /// Main thread. Safe to call any number of times: re-activates the session, recreates the player if needed
    /// and plays the loop if it is not playing. Also starts the 5 s watchdog and the notification hooks once.
    public func start() {
        _ = startLoop(force: false)
    }

    /// Main thread. Like `start()`, plus one log line (category "keepalive") when the loop really had to be
    /// restarted. Nothing is logged when it was already running.
    ///
    /// `force` stops and replays the loop even when the player says it is playing. After CallKit or an
    /// interruption has deactivated the session under it, the player can keep saying so while no sound flows,
    /// and iOS then suspends the app. Replaying silence costs nothing, so pass it at every app transition.
    public func ensureRunning(reason: String, force: Bool = false) {
        if startLoop(force: force) {
            log.info("keep-alive loop restarted, reason: \(reason, privacy: .public)")
        }
    }

    /// Returns true when the loop was not playing or the session had to be activated again.
    private func startLoop(force: Bool) -> Bool {
        var restarted = false
        if observers.isEmpty {
            observe()
        }
        if watchdog == nil {
            // Fires only while the app runs; `retrySoon` holds a background task when a restart fails.
            let timer = Timer(timeInterval: 5, repeats: true) { [weak self] _ in
                self?.ensureRunning(reason: "watchdog")
            }
            RunLoop.main.add(timer, forMode: .common)
            watchdog = timer
        }
        do {
            if player?.isPlaying != true {
                restarted = true
            }
            try AudioSessionBox.shared.activate()
            if player == nil {
                player = try AVAudioPlayer(data: Self.silentWAV(seconds: 2))
                player?.numberOfLoops = -1
                player?.prepareToPlay()
            }
            if activationFailed || force {
                // The session was taken from under the player, which may still say it is playing: start it again.
                player?.stop()
                restarted = true
            }
            if player?.isPlaying == false {
                restarted = player?.play() == true || restarted
            }
            activationFailed = false
            lastError = nil
            stopRetrying()
        } catch {
            // Without the loop the app still works in the foreground; it just will not ring in the background.
            activationFailed = true
            lastError = "\(error)"
            log.error("keep-alive could not start: \(error.localizedDescription, privacy: .public)")
            retrySoon()
        }
        return restarted
    }

    /// Right after a CallKit call the system still holds the audio, so activation fails ("insufficient priority").
    /// Without sound iOS suspends the app within seconds and the next call cannot ring. Hold a background task and
    /// retry twice a second until the session is back (iOS ends the task after about 30 s at most).
    private func retrySoon() {
        MainActor.assumeIsolated {
            if backgroundTask == .invalid {
                backgroundTask = UIApplication.shared.beginBackgroundTask(withName: "keep-alive") { [weak self] in
                    self?.stopRetrying()
                }
            }
        }
        guard retryTimer == nil else { return }
        let timer = Timer(timeInterval: 0.5, repeats: true) { [weak self] _ in
            self?.ensureRunning(reason: "retry")
        }
        RunLoop.main.add(timer, forMode: .common)
        retryTimer = timer
    }

    private func stopRetrying() {
        retryTimer?.invalidate()
        retryTimer = nil
        MainActor.assumeIsolated {
            if backgroundTask != .invalid {
                UIApplication.shared.endBackgroundTask(backgroundTask)
                backgroundTask = .invalid
            }
        }
    }

    private func observe() {
        let center = NotificationCenter.default
        observers = [
            // `.began` needs nothing: the watchdog and the app hooks restart the loop afterwards.
            // `.ended` restarts regardless of `shouldResume`; iOS does not always send it.
            center.addObserver(forName: AVAudioSession.interruptionNotification, object: nil, queue: .main) {
                [weak self] note in
                guard let raw = note.userInfo?[AVAudioSessionInterruptionTypeKey] as? UInt,
                      AVAudioSession.InterruptionType(rawValue: raw) == .ended else { return }
                self?.ensureRunning(reason: "interruption-ended", force: true)
            },
            center.addObserver(forName: AVAudioSession.routeChangeNotification, object: nil, queue: .main) {
                [weak self] _ in
                self?.ensureRunning(reason: "route")
            },
            center.addObserver(forName: AVAudioSession.mediaServicesWereResetNotification, object: nil,
                               queue: .main) { [weak self] _ in
                self?.player = nil
                self?.ensureRunning(reason: "media-services-reset", force: true)
            },
        ]
    }

    /// 16 kHz mono 16-bit WAV of zeros.
    static func silentWAV(seconds: Int) -> Data {
        let rate = 16000
        let dataBytes = rate * 2 * seconds
        var wav = Data()
        func add(_ value: UInt32) { withUnsafeBytes(of: value.littleEndian) { wav.append(contentsOf: $0) } }
        func add16(_ value: UInt16) { withUnsafeBytes(of: value.littleEndian) { wav.append(contentsOf: $0) } }
        wav.append(contentsOf: Array("RIFF".utf8))
        add(UInt32(36 + dataBytes))
        wav.append(contentsOf: Array("WAVEfmt ".utf8))
        add(16)
        add16(1)
        add16(1)
        add(UInt32(rate))
        add(UInt32(rate * 2))
        add16(2)
        add16(16)
        wav.append(contentsOf: Array("data".utf8))
        add(UInt32(dataBytes))
        wav.append(Data(count: dataBytes))
        return wav
    }
}

/// Plays prepared avatar clips with their own audio. Show `player` full screen (AVPlayerLayer) as the avatar;
/// between lines it keeps the last frame. `play` returns only on AVPlayerItemDidPlayToEndTime; a failed item,
/// a failed-to-play-to-end notification or `stop()` throw instead, so a timer is never taken as completion.
public final class AvatarClips: AvatarClipPlayer, @unchecked Sendable {
    public let player: AVPlayer
    private let audio: AudioSessionBox
    private let lock = NSLock()
    private var waiter: CheckedContinuation<Void, Error>?
    private var generation = 0
    private var observers: [NSObjectProtocol] = []
    private var status: NSKeyValueObservation?
    private var file: URL?

    enum ClipError: Error {
        case failed
    }

    init(audio: AudioSessionBox) {
        self.audio = audio
        player = AVPlayer()
        player.actionAtItemEnd = .pause
        player.automaticallyWaitsToMinimizeStalling = false
    }

    public func play(mp4: Data) async throws {
        try audio.activate()
        let url = FileManager.default.temporaryDirectory.appendingPathComponent("clip-\(UUID().uuidString).mp4")
        try mp4.write(to: url, options: .atomic)
        let ticket = lock.withLock { generation }
        try await withTaskCancellationHandler {
            try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
                DispatchQueue.main.async {
                    self.start(url, ticket: ticket, continuation: continuation)
                }
            }
        } onCancel: {
            self.halt()
        }
    }

    public func stop() async {
        halt()
    }

    /// Main thread.
    private func start(_ url: URL, ticket: Int, continuation: CheckedContinuation<Void, Error>) {
        let stale = lock.withLock { () -> Bool in
            if ticket != generation {
                return true
            }
            waiter = continuation
            return false
        }
        if stale {
            try? FileManager.default.removeItem(at: url)
            continuation.resume(throwing: CancellationError())
            return
        }
        clearObservers()
        let item = AVPlayerItem(url: url)
        let center = NotificationCenter.default
        observers = [
            center.addObserver(forName: AVPlayerItem.didPlayToEndTimeNotification, object: item, queue: .main) {
                [weak self] _ in self?.finish(nil)
            },
            center.addObserver(forName: AVPlayerItem.failedToPlayToEndTimeNotification, object: item, queue: .main) {
                [weak self] _ in self?.finish(ClipError.failed)
            },
        ]
        status = item.observe(\.status, options: [.new]) { [weak self] item, _ in
            if item.status == .failed {
                self?.finish(ClipError.failed)
            }
        }
        let previous = file
        file = url
        player.replaceCurrentItem(with: item)
        player.play()
        if let previous {
            try? FileManager.default.removeItem(at: previous)
        }
    }

    private func finish(_ error: Error?) {
        let continuation = lock.withLock { () -> CheckedContinuation<Void, Error>? in
            let waiting = waiter
            waiter = nil
            return waiting
        }
        guard let continuation else { return }
        DispatchQueue.main.async { self.clearObservers() }
        if let error {
            continuation.resume(throwing: error)
        } else {
            continuation.resume()
        }
    }

    /// Stops the current clip (keeping its frame) and invalidates any clip that has not started yet.
    private func halt() {
        lock.withLock { generation += 1 }
        DispatchQueue.main.async { self.player.pause() }
        finish(CancellationError())
    }

    private func clearObservers() {
        for observer in observers {
            NotificationCenter.default.removeObserver(observer)
        }
        observers = []
        status = nil
    }
}

final class PCMPlayer: SpeechPlayer, @unchecked Sendable {
    private let audio: AudioSessionBox
    private let engine = AVAudioEngine()
    private let node = AVAudioPlayerNode()
    private let format = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: 16000, channels: 1,
                                       interleaved: false)!
    private var attached = false
    private var waiter: CheckedContinuation<Void, Error>?
    private let lock = NSLock()

    init(audio: AudioSessionBox) {
        self.audio = audio
    }

    func play(pcm16k: Data, durationMs: Int) async throws {
        try audio.activate()
        if !attached {
            engine.attach(node)
            engine.connect(node, to: engine.mainMixerNode, format: format)
            attached = true
        }
        if !engine.isRunning {
            try engine.start()
        }
        let frames = pcm16k.count / 2
        guard frames > 0, let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: AVAudioFrameCount(frames)) else {
            return
        }
        buffer.frameLength = AVAudioFrameCount(frames)
        let out = buffer.floatChannelData![0]
        pcm16k.withUnsafeBytes { raw in
            let samples = raw.bindMemory(to: Int16.self)
            for index in 0..<frames {
                out[index] = Float(Int16(littleEndian: samples[index])) / 32768.0
            }
        }
        try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
            lock.lock()
            waiter = continuation
            lock.unlock()
            node.scheduleBuffer(buffer, completionCallbackType: .dataPlayedBack) { [weak self] _ in
                self?.resume(nil)
            }
            node.play()
        }
    }

    private func resume(_ error: Error?) {
        lock.lock()
        let continuation = waiter
        waiter = nil
        lock.unlock()
        if let error {
            continuation?.resume(throwing: error)
        } else {
            continuation?.resume()
        }
    }

    func stop() async {
        node.stop()
        resume(CancellationError())
    }
}

final class WAVRecorder: AnswerRecorder, @unchecked Sendable {
    private let audio: AudioSessionBox
    private var recorder: AVAudioRecorder?
    private var stopped = false

    init(audio: AudioSessionBox) {
        self.audio = audio
    }

    func record(maxMs: Int, stopOnSilenceMs: Int?) async throws -> (Data, String) {
        try audio.activate()
        let url = FileManager.default.temporaryDirectory.appendingPathComponent("answer-\(UUID().uuidString).wav")
        let settings: [String: Any] = [
            AVFormatIDKey: kAudioFormatLinearPCM,
            AVSampleRateKey: 16000,
            AVNumberOfChannelsKey: 1,
            AVLinearPCMBitDepthKey: 16,
            AVLinearPCMIsFloatKey: false,
            AVLinearPCMIsBigEndianKey: false,
        ]
        let recorder = try AVAudioRecorder(url: url, settings: settings)
        recorder.isMeteringEnabled = true
        self.recorder = recorder
        stopped = false
        recorder.record()
        let started = Date()
        // Speech and quiet are judged against the room's own noise (AnswerEndDetector): a fixed level never saw
        // "quiet" in a noisy room, so every answer ran to maxMs and she said it again.
        var detector = AnswerEndDetector(stopOnSilenceMs: stopOnSilenceMs,
                                         noAnswerMs: stopOnSilenceMs == nil ? Int.max : 8000)
        while !stopped {
            try await Task.sleep(nanoseconds: 100_000_000)
            let elapsedMs = Int(Date().timeIntervalSince(started) * 1000)
            if elapsedMs >= maxMs {
                break
            }
            recorder.updateMeters()
            if detector.add(levelDb: recorder.averagePower(forChannel: 0), atMs: elapsedMs) != .keepListening {
                break
            }
        }
        recorder.stop()
        self.recorder = nil
        defer { try? FileManager.default.removeItem(at: url) }
        try Task.checkCancellation()
        return (try Data(contentsOf: url), "audio/wav")
    }

    func stop() async {
        stopped = true
        recorder?.stop()
    }
}

final class FrontCamera: NSObject, CameraSource, AVCaptureVideoDataOutputSampleBufferDelegate, @unchecked Sendable {
    private let session = AVCaptureSession()
    private let queue = DispatchQueue(label: "parent.camera")
    private let context = CIContext()
    private let lock = NSLock()
    private var latest: Data?
    private var configured = false

    func start() async throws {
        if !configured {
            session.beginConfiguration()
            session.sessionPreset = .vga640x480
            guard let device = AVCaptureDevice.default(.builtInWideAngleCamera, for: .video, position: .front) else {
                session.commitConfiguration()
                throw APIError.badResponse
            }
            let input = try AVCaptureDeviceInput(device: device)
            if session.canAddInput(input) {
                session.addInput(input)
            }
            let output = AVCaptureVideoDataOutput()
            output.alwaysDiscardsLateVideoFrames = true
            output.setSampleBufferDelegate(self, queue: queue)
            if session.canAddOutput(output) {
                session.addOutput(output)
            }
            if let connection = output.connection(with: .video) {
                if connection.isVideoRotationAngleSupported(90) {
                    connection.videoRotationAngle = 90
                }
                connection.isVideoMirrored = false
            }
            session.commitConfiguration()
            configured = true
        }
        lock.lock()
        latest = nil
        lock.unlock()
        queue.async { [session] in
            session.startRunning()
        }
    }

    func captureOutput(_ output: AVCaptureOutput, didOutput sampleBuffer: CMSampleBuffer,
                       from connection: AVCaptureConnection) {
        guard let pixels = CMSampleBufferGetImageBuffer(sampleBuffer) else { return }
        let image = CIImage(cvPixelBuffer: pixels)
        guard let space = CGColorSpace(name: CGColorSpace.sRGB),
              let jpeg = context.jpegRepresentation(of: image, colorSpace: space,
                                                    options: [kCGImageDestinationLossyCompressionQuality as CIImageRepresentationOption: 0.7])
        else { return }
        lock.lock()
        latest = jpeg
        lock.unlock()
    }

    func jpegFrame() async throws -> Data? {
        lock.lock()
        defer { lock.unlock() }
        return latest
    }

    func stop() async {
        queue.async { [session] in
            if session.isRunning {
                session.stopRunning()
            }
        }
    }
}

final class PCMMicrophone: MicrophoneSource, @unchecked Sendable {
    private let audio: AudioSessionBox
    private let engine = AVAudioEngine()
    private let target = AVAudioFormat(commonFormat: .pcmFormatInt16, sampleRate: 16000, channels: 1, interleaved: true)!
    private let lock = NSLock()
    private var pending = Data()
    private var running = false

    init(audio: AudioSessionBox) {
        self.audio = audio
    }

    func start() async throws {
        try audio.activate()
        let input = engine.inputNode
        let source = input.outputFormat(forBus: 0)
        guard let converter = AVAudioConverter(from: source, to: target) else {
            throw APIError.badResponse
        }
        lock.lock()
        pending = Data()
        lock.unlock()
        input.removeTap(onBus: 0)
        input.installTap(onBus: 0, bufferSize: 1024, format: source) { [weak self] buffer, _ in
            self?.convert(buffer, with: converter)
        }
        try engine.start()
        running = true
    }

    private func convert(_ buffer: AVAudioPCMBuffer, with converter: AVAudioConverter) {
        let ratio = target.sampleRate / buffer.format.sampleRate
        let capacity = AVAudioFrameCount(Double(buffer.frameLength) * ratio) + 16
        guard let out = AVAudioPCMBuffer(pcmFormat: target, frameCapacity: capacity) else { return }
        var supplied = false
        var error: NSError?
        converter.convert(to: out, error: &error) { _, status in
            if supplied {
                status.pointee = .noDataNow
                return nil
            }
            supplied = true
            status.pointee = .haveData
            return buffer
        }
        guard error == nil, let channel = out.int16ChannelData else { return }
        let bytes = Data(bytes: channel[0], count: Int(out.frameLength) * 2)
        lock.lock()
        pending.append(bytes)
        lock.unlock()
    }

    func pcmChunk(ms: Int) async throws -> Data {
        let need = 16000 * 2 * ms / 1000
        while true {
            try Task.checkCancellation()
            lock.lock()
            if pending.count >= need {
                let chunk = pending.prefix(need)
                pending.removeFirst(need)
                lock.unlock()
                return Data(chunk)
            }
            lock.unlock()
            try await Task.sleep(nanoseconds: 20_000_000)
        }
    }

    func stop() async {
        if running {
            engine.inputNode.removeTap(onBus: 0)
            engine.stop()
            running = false
        }
    }
}
#endif
