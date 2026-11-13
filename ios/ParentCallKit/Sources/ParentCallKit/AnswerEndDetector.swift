import Foundation

/// Decides from the recorder's level meter when her spoken answer is over.
///
/// The first version compared each level with a fixed -35 dB. In a noisy room the background stayed above that, so
/// the recording never saw "quiet" and ran for the full 15 s; she heard nothing and said her answer again. Now speech
/// and quiet are judged against the room itself: the quietest (smoothed) level so far is the noise floor, a level
/// `speechAboveFloorDb` over it is her voice, anything below that is quiet. Pure value type: feed it one level per
/// meter reading.
public struct AnswerEndDetector: Sendable {
    public enum Decision: Equatable, Sendable {
        case keepListening
        /// She spoke and then was quiet for `stopOnSilenceMs`.
        case finished
        /// Nobody started speaking within `noAnswerMs`.
        case noAnswer
    }

    public let stopOnSilenceMs: Int?
    public let noAnswerMs: Int
    /// How far above the room's noise floor a level must be to count as her voice.
    public let speechAboveFloorDb: Float
    /// Speech is never required to be louder than this, so a very quiet room does not make a whisper "noise".
    public let quietestSpeechDb: Float

    public private(set) var noiseFloorDb: Float?
    public private(set) var heardSpeech = false
    private var smoothedDb: Float?
    private var quietSinceMs: Int?

    public init(stopOnSilenceMs: Int?, noAnswerMs: Int = 8000, speechAboveFloorDb: Float = 12,
                quietestSpeechDb: Float = -50) {
        self.stopOnSilenceMs = stopOnSilenceMs
        self.noAnswerMs = noAnswerMs
        self.speechAboveFloorDb = speechAboveFloorDb
        self.quietestSpeechDb = quietestSpeechDb
    }

    /// The level that counts as speech right now: `speechAboveFloorDb` over the room, at least `quietestSpeechDb`.
    public var speechThresholdDb: Float {
        guard let noiseFloorDb else { return quietestSpeechDb }
        return max(quietestSpeechDb, noiseFloorDb + speechAboveFloorDb)
    }

    /// One meter reading: `levelDb` is the average power in dBFS, `atMs` the time since the recording started.
    public mutating func add(levelDb: Float, atMs: Int) -> Decision {
        let level = max(levelDb, -80)  // the meter reads -160 before the first buffer: not the room
        let smoothed = smoothedDb.map { 0.5 * $0 + 0.5 * level } ?? level
        smoothedDb = smoothed
        noiseFloorDb = min(noiseFloorDb ?? smoothed, smoothed)
        if smoothed >= speechThresholdDb {
            heardSpeech = true
            quietSinceMs = nil
            return .keepListening
        }
        if !heardSpeech {
            return atMs >= noAnswerMs ? .noAnswer : .keepListening
        }
        let since = quietSinceMs ?? atMs
        quietSinceMs = since
        if let stopOnSilenceMs, atMs - since >= stopOnSilenceMs {
            return .finished
        }
        return .keepListening
    }
}
