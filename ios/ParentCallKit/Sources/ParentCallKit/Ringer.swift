#if canImport(AVFoundation) && canImport(UIKit)
import AudioToolbox
import AVFoundation
import UIKit

/// The ring the app plays on its own call screen while it is in the foreground. Written for iOS 17; NOT compiled
/// or run yet. Main thread only.
///
/// It loops a generated two-tone ring on the loudspeaker and vibrates once per ring. `stop()` leaves the audio
/// session alone: `BackgroundKeepAlive` shares it and its silent loop keeps the app alive in the background.
@MainActor
public final class Ringer {
    private var player: AVAudioPlayer?
    private var vibrationTimer: Timer?

    public private(set) var isRinging = false

    public init() {}

    /// Starts the ring. Does nothing while it is already ringing. Any audio error is ignored: the screen still works.
    public func start() {
        guard !isRinging else { return }
        do {
            try AudioSessionBox.shared.activate()
            // Lets the vibration through while the session is set to record.
            try? AVAudioSession.sharedInstance().setAllowHapticsAndSystemSoundsDuringRecording(true)
            if player == nil {
                let tone = try AVAudioPlayer(data: Self.ringWAV())
                tone.numberOfLoops = -1
                tone.volume = 1.0
                tone.prepareToPlay()
                player = tone
            }
            guard player?.play() == true else { return }
            isRinging = true
            startVibrating()
        } catch {
            // No ring sound; the Accept/Decline screen still works.
        }
    }

    /// Stops the ring and the vibration. The audio session stays active.
    public func stop() {
        vibrationTimer?.invalidate()
        vibrationTimer = nil
        player?.stop()
        player?.currentTime = 0
        isRinging = false
    }

    /// One buzz now and one at the start of every ring cycle.
    private func startVibrating() {
        vibrationTimer?.invalidate()
        AudioServicesPlaySystemSound(kSystemSoundID_Vibrate)
        vibrationTimer = Timer.scheduledTimer(withTimeInterval: Self.cycleSeconds, repeats: true) { _ in
            AudioServicesPlaySystemSound(kSystemSoundID_Vibrate)
        }
    }

    // MARK: Tone

    // `nonisolated`, so the tone generator is a pure function that tests can call off the main actor.
    nonisolated static let sampleRate = 22050
    /// Two bursts of 0.8 s with 0.2 s between, then silence to fill the cycle.
    nonisolated static let cycleSeconds: TimeInterval = 4.0
    nonisolated static let burstSeconds = 0.8
    nonisolated static let gapSeconds = 0.2
    nonisolated static let fadeSeconds = 0.010
    nonisolated static let peak = 0.8

    /// One ring cycle as 16-bit mono WAV: two warm two-tone bursts (440 Hz + 480 Hz), then silence. Loops cleanly.
    nonisolated static func ringWAV() -> Data {
        let total = Int(cycleSeconds * Double(sampleRate))
        var samples = [Int16](repeating: 0, count: total)
        let burstFrames = Int(burstSeconds * Double(sampleRate))
        let fadeFrames = max(1, Int(fadeSeconds * Double(sampleRate)))
        let starts = [0, Int((burstSeconds + gapSeconds) * Double(sampleRate))]
        for start in starts {
            for index in 0..<burstFrames where start + index < total {
                let time = Double(index) / Double(sampleRate)
                // Half of each tone, so the mix never goes past the peak.
                let mix = 0.5 * sin(2 * Double.pi * 440 * time) + 0.5 * sin(2 * Double.pi * 480 * time)
                let fadeIn = min(1.0, Double(index) / Double(fadeFrames))
                let fadeOut = min(1.0, Double(burstFrames - 1 - index) / Double(fadeFrames))
                let value = mix * min(fadeIn, fadeOut) * peak
                samples[start + index] = Int16(max(-1.0, min(1.0, value)) * 32767.0)
            }
        }
        return wav(samples: samples, rate: sampleRate)
    }

    /// 16-bit mono PCM WAV, same header layout as `BackgroundKeepAlive.silentWAV`.
    nonisolated static func wav(samples: [Int16], rate: Int) -> Data {
        let dataBytes = samples.count * 2
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
        for sample in samples {
            add16(UInt16(bitPattern: sample))
        }
        return wav
    }
}
#endif
