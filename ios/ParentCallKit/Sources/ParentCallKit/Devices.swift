import Foundation

/// Plays one prepared line: signed 16-bit little-endian mono PCM at 16 kHz.
/// Returns when playback has actually ended on the device, not when it was queued.
public protocol SpeechPlayer: Sendable {
    func play(pcm16k: Data, durationMs: Int) async throws
    func stop() async
}

/// Plays one prepared avatar clip: an MP4 whose own audio track is the line. The app shows the video full screen
/// as the avatar. Returns only when the clip has played to its end; throws when it fails or is stopped.
public protocol AvatarClipPlayer: Sendable {
    func play(mp4: Data) async throws
    func stop() async
}

/// Records one spoken answer. Returns the audio and its content type (audio/wav, audio/mp4 or audio/webm).
public protocol AnswerRecorder: Sendable {
    func record(maxMs: Int, stopOnSilenceMs: Int?) async throws -> (Data, String)
    func stop() async
}

/// Front camera frames for a task. `jpegFrame` returns the latest frame as JPEG, at most 640x480
/// (or 480x640 in portrait), or nil when no frame is available yet.
public protocol CameraSource: Sendable {
    func start() async throws
    func jpegFrame() async throws -> Data?
    func stop() async
}

/// Microphone audio for the sentence task. `pcmChunk` waits until `ms` of audio has been captured and returns it
/// as signed 16-bit little-endian mono PCM at 16 kHz (8000 bytes for 250 ms).
public protocol MicrophoneSource: Sendable {
    func start() async throws
    func pcmChunk(ms: Int) async throws -> Data
    func stop() async
}

public struct ParentDevices: Sendable {
    public let player: SpeechPlayer
    public let recorder: AnswerRecorder
    public let camera: CameraSource
    public let microphone: MicrophoneSource
    /// Optional: without it, every line plays as PCM even when the backend offers a clip.
    public let clips: AvatarClipPlayer?

    public init(player: SpeechPlayer, recorder: AnswerRecorder, camera: CameraSource, microphone: MicrophoneSource,
                clips: AvatarClipPlayer? = nil) {
        self.player = player
        self.recorder = recorder
        self.camera = camera
        self.microphone = microphone
        self.clips = clips
    }
}
