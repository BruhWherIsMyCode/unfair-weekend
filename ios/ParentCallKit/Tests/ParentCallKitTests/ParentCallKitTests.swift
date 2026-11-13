import Foundation
import XCTest
@testable import ParentCallKit

final class SSEParserTests: XCTestCase {
    func testParsesSplitChunksCommentsAndCRLF() {
        var parser = SSEParser()
        var got = parser.feed(Data("retry: 1000\r\n\r\n: keepalive\n\nid: e1\nevent: call.inc".utf8))
        XCTAssertEqual(got, [])
        got = parser.feed(Data("oming\ndata: {\"a\":1}\n\nid: e2\ndata: x\ndata: y\n\n".utf8))
        XCTAssertEqual(got, [
            SSEMessage(id: "e1", event: "call.incoming", data: "{\"a\":1}"),
            SSEMessage(id: "e2", event: "message", data: "x\ny"),
        ])
    }
}

final class CommandDecodingTests: XCTestCase {
    func testDecodesSayAndCaptureCommands() throws {
        let say = """
        {"event_id":"exec_c1","call_id":"call_1","type":"command.execute","command":{"command_id":"c1","kind":"say",
        "line_id":"greet","text":"Hello, Ona.","audio_path":"/v1/calls/call_1/speech/c1","duration_ms":2400}}
        """
        let event = try JSON.decoder.decode(CommandExecuteEvent.self, from: Data(say.utf8))
        XCTAssertEqual(event.command, .say(SayCommand(commandId: "c1", lineId: "greet", text: "Hello, Ona.",
                                                      audioPath: "/v1/calls/call_1/speech/c1", durationMs: 2400,
                                                      videoPath: nil)))
        let capture = """
        {"event_id":"exec_A1","call_id":"call_1","type":"command.execute","command":{"command_id":"A1","kind":"capture",
        "purpose":{"kind":"task","task":"smile"},"settings":{"media_kind":"video","duration_ms":5000,"prepare_ms":0,
        "stop_on_silence_ms":null,"format":"jpeg","sample_period_ms":200},"transport":"sample_post"}}
        """
        let decoded = try JSON.decoder.decode(CommandExecuteEvent.self, from: Data(capture.utf8))
        guard case .capture(let command) = decoded.command else {
            return XCTFail("expected a capture command")
        }
        XCTAssertEqual(command.purpose.task, "smile")
        XCTAssertEqual(command.settings.samplePeriodMs, 200)
        XCTAssertNil(command.settings.stopOnSilenceMs)
    }

    func testDecodesOptionalAvatarClip() throws {
        let say = """
        {"event_id":"exec_c2","call_id":"call_1","type":"command.execute","command":{"command_id":"c2","kind":"say",
        "line_id":"greet","text":"Hello, Ona.","audio_path":"/v1/calls/call_1/speech/c2","duration_ms":2800,
        "video_path":"/v1/calls/call_1/video/c2"}}
        """
        let event = try JSON.decoder.decode(CommandExecuteEvent.self, from: Data(say.utf8))
        guard case .say(let command) = event.command else {
            return XCTFail("expected a say command")
        }
        XCTAssertEqual(command.videoPath, "/v1/calls/call_1/video/c2")
        XCTAssertEqual(command.audioPath, "/v1/calls/call_1/speech/c2")
    }

    func testDecodesLiveAvatarSession() throws {
        let body = #"{"session_token":"tok","ws_url":"wss://api.simli.ai/compose/webrtc/livekit"}"#
        let session = try JSON.decoder.decode(LiveAvatarSession.self, from: Data(body.utf8))
        XCTAssertEqual(session, LiveAvatarSession(sessionToken: "tok", wsUrl: "wss://api.simli.ai/compose/webrtc/livekit"))
    }

    #if canImport(AVFoundation) && canImport(UIKit)
    func testKeepAliveSilenceIsAValidWAV() {
        let wav = BackgroundKeepAlive.silentWAV(seconds: 1)
        XCTAssertEqual(wav.count, 44 + 32000)
        XCTAssertEqual(String(decoding: wav.prefix(4), as: UTF8.self), "RIFF")
    }
    #endif

    func testFakeMediaIsWellFormed() {
        let jpeg = FakeDevices.tinyJPEG(width: 640, height: 480)
        XCTAssertEqual(Array(jpeg.prefix(2)), [0xFF, 0xD8])
        XCTAssertEqual(Array(jpeg.suffix(2)), [0xFF, 0xD9])
        XCTAssertEqual(FakeDevices.silentWAV(ms: 250).count, 44 + 8000)
    }
}
