import Foundation

/// Devices without hardware: for the simulator executable, tests and UI previews.
/// They produce tiny synthetic input, never a recording of a person.
public enum FakeDevices {
    public static func make(timeScale: Double = 1.0) -> ParentDevices {
        ParentDevices(player: FakePlayer(timeScale: timeScale), recorder: FakeRecorder(),
                      camera: FakeCamera(), microphone: FakeMicrophone(timeScale: timeScale))
    }

    public static func tinyJPEG(width: UInt16 = 8, height: UInt16 = 8) -> Data {
        var data = Data([0xFF, 0xD8, 0xFF, 0xC0, 0x00, 0x0B, 0x08])
        data.append(contentsOf: [UInt8(height >> 8), UInt8(height & 0xFF), UInt8(width >> 8), UInt8(width & 0xFF)])
        data.append(contentsOf: [0x01, 0x01, 0x11, 0x00, 0xFF, 0xD9])
        return data
    }

    public static func silentWAV(ms: Int = 200) -> Data {
        let pcmBytes = 16000 * 2 * ms / 1000
        var data = Data("RIFF".utf8)
        data.append(le32(UInt32(36 + pcmBytes)))
        data.append(Data("WAVEfmt ".utf8))
        data.append(le32(16))
        data.append(le16(1))
        data.append(le16(1))
        data.append(le32(16000))
        data.append(le32(32000))
        data.append(le16(2))
        data.append(le16(16))
        data.append(Data("data".utf8))
        data.append(le32(UInt32(pcmBytes)))
        data.append(Data(count: pcmBytes))
        return data
    }

    static func le32(_ value: UInt32) -> Data {
        withUnsafeBytes(of: value.littleEndian) { Data($0) }
    }

    static func le16(_ value: UInt16) -> Data {
        withUnsafeBytes(of: value.littleEndian) { Data($0) }
    }
}

struct FakePlayer: SpeechPlayer {
    let timeScale: Double

    func play(pcm16k: Data, durationMs: Int) async throws {
        try await Task.sleep(nanoseconds: UInt64(Double(durationMs) * timeScale * 1_000_000))
    }

    func stop() async {}
}

struct FakeRecorder: AnswerRecorder {
    func record(maxMs: Int, stopOnSilenceMs: Int?) async throws -> (Data, String) {
        (FakeDevices.silentWAV(), "audio/wav")
    }

    func stop() async {}
}

struct FakeCamera: CameraSource {
    func start() async throws {}

    func jpegFrame() async throws -> Data? {
        FakeDevices.tinyJPEG()
    }

    func stop() async {}
}

struct FakeMicrophone: MicrophoneSource {
    let timeScale: Double

    func start() async throws {}

    func pcmChunk(ms: Int) async throws -> Data {
        try await Task.sleep(nanoseconds: UInt64(Double(ms) * timeScale * 1_000_000))
        return Data(count: 16000 * 2 * ms / 1000)
    }

    func stop() async {}
}
