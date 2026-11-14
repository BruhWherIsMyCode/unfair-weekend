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

/// Whether a failed live line can be attempted by another player without repeating audio.
public enum LivePlaybackFailure: Error, Equatable, Sendable {
    /// No audio was submitted for this line; another player may play it.
    case notStarted
    /// Audio may have been heard. Report failure without replaying the same line.
    case interrupted
}

/// A Simli live avatar for the whole call: the phone streams each line's PCM to Simli, and Simli sends the talking
/// avatar back (video and the line's own voice). The app shows its video as the avatar while it is connected.
public protocol LiveAvatarPlayer: Sendable {
    /// Connects with a session the backend minted. Returns once the avatar's video track has arrived; throws if it
    /// cannot connect.
    func connect(_ session: LiveAvatarSession) async throws
    /// Streams one line (signed 16-bit little-endian mono PCM, 16 kHz). Returns when Simli reports the avatar has
    /// spoken it and gone silent. Only LivePlaybackFailure.notStarted permits same-line fallback;
    /// all other errors mean playback may have begun. Cancellation stops without fallback.
    func speak(pcm16k: Data, durationMs: Int) async throws
    /// Silences the current line and keeps the session.
    func stop() async
    /// Ends the session; the app goes back to the clip player or its placeholder.
    func disconnect() async
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
    /// Optional: tried first for every line when the backend offers a live session; clips and PCM are the fallback.
    public let live: LiveAvatarPlayer?

    public init(player: SpeechPlayer, recorder: AnswerRecorder, camera: CameraSource, microphone: MicrophoneSource,
                clips: AvatarClipPlayer? = nil, live: LiveAvatarPlayer? = nil) {
        self.player = player
        self.recorder = recorder
        self.camera = camera
        self.microphone = microphone
        self.clips = clips
        self.live = live
    }
}
