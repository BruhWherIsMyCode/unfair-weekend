import AVFoundation

/// Demo workaround, not for the App Store. A silent loop with the "audio" background mode keeps the app running
/// when the phone is locked or another app is open, so the backend's event stream stays connected and an alert
/// still arrives as a notification. There is no push (no paid team). If iOS ends the app, it must be opened again.
@MainActor
final class KeepAlive {
    private var player: AVAudioPlayer?
    private var observers: [NSObjectProtocol] = []

    /// Safe to call again; restarts after an interruption (a phone call) or a media-services reset.
    func start() {
        do {
            let session = AVAudioSession.sharedInstance()
            // Mixes with other audio, so it never stops music and is not stopped by it.
            try session.setCategory(.playback, options: [.mixWithOthers])
            try session.setActive(true)
            if player == nil {
                player = try AVAudioPlayer(data: Self.silence)
                player?.numberOfLoops = -1
                observe()
            }
            if player?.isPlaying == false {
                player?.play()
            }
        } catch {
            // Without it the app still works in front; alerts only arrive while it is open.
        }
    }

    private func observe() {
        let center = NotificationCenter.default
        observers = [
            center.addObserver(forName: AVAudioSession.interruptionNotification, object: nil, queue: .main) { note in
                guard let raw = note.userInfo?[AVAudioSessionInterruptionTypeKey] as? UInt,
                      AVAudioSession.InterruptionType(rawValue: raw) == .ended else { return }
                MainActor.assumeIsolated { self.start() }
            },
            center.addObserver(forName: AVAudioSession.mediaServicesWereResetNotification, object: nil,
                               queue: .main) { _ in
                MainActor.assumeIsolated {
                    self.player = nil
                    self.start()
                }
            },
        ]
    }

    /// Two seconds of 16 kHz mono 16-bit silence as WAV.
    private static let silence: Data = {
        let rate = 16000, bytes = rate * 2 * 2
        var wav = Data()
        func u32(_ v: Int) { withUnsafeBytes(of: UInt32(v).littleEndian) { wav.append(contentsOf: $0) } }
        func u16(_ v: Int) { withUnsafeBytes(of: UInt16(v).littleEndian) { wav.append(contentsOf: $0) } }
        wav.append(contentsOf: Array("RIFF".utf8)); u32(36 + bytes)
        wav.append(contentsOf: Array("WAVEfmt ".utf8)); u32(16); u16(1); u16(1); u32(rate); u32(rate * 2); u16(2); u16(16)
        wav.append(contentsOf: Array("data".utf8)); u32(bytes)
        wav.append(Data(count: bytes))
        return wav
    }()
}
