import Foundation
import XCTest
@testable import ParentCallKit

final class CallScreenTests: XCTestCase {
    let t0 = Date(timeIntervalSince1970: 1_000)

    func testFollowsAWholeCall() {
        var r = CallScreenReducer()
        r.apply(.ringing(callId: "c1", title: "Daily check-in"))
        XCTAssertEqual(r.screen.mode, .ringing(callId: "c1", title: "Daily check-in"))
        r.apply(.connected)
        XCTAssertEqual(r.screen.mode, .inCall)

        r.apply(.speaking(lineId: "greet", text: "Hello, Ona. It's time for our short check-in.", durationMs: 3500, at: t0))
        guard case .assistant(let greet) = r.screen.card else { return XCTFail("assistant card") }
        XCTAssertEqual(greet.words.count, 8)
        XCTAssertEqual(greet.wordStart(2).timeIntervalSince(t0), 3.5 / 8 * 2, accuracy: 0.001)
        XCTAssertEqual(r.screen.dot(.questions), .current)

        r.apply(.speaking(lineId: "q_feeling", text: "How are you feeling today?", durationMs: 1800, at: t0))
        r.apply(.listening(questionId: "feeling"))
        guard case .question(let n, 3, _, true) = r.screen.card else { return XCTFail("listening question") }
        XCTAssertEqual(n, 2)

        r.apply(.speaking(lineId: "setup_phone", text: "Please put your phone on a table.", durationMs: 2000, at: t0))
        r.apply(.speaking(lineId: "task_smile", text: "When the timer starts, smile.", durationMs: 2000, at: t0))
        XCTAssertEqual(r.screen.stage, .smile)
        XCTAssertEqual(r.screen.dot(.questions), .done)
        XCTAssertTrue(r.screen.selfViewLarge)
        r.apply(.captureCommanded(task: "smile", durationMs: 5000))
        XCTAssertEqual(r.screen.card, .task(step: 1, total: 3, task: "smile", timer: .waiting(totalSeconds: 5), hint: nil))
        r.apply(.captureStarted(durationMs: 5000, at: t0))
        XCTAssertEqual(r.screen.card, .task(step: 1, total: 3, task: "smile",
                                            timer: .running(endsAt: t0.addingTimeInterval(5), totalSeconds: 5), hint: nil))
        r.apply(.hint("look_at_camera"))
        r.apply(.captureFinished)
        XCTAssertEqual(r.screen.card, .task(step: 1, total: 3, task: "smile", timer: .finished, hint: nil))

        r.apply(.speaking(lineId: "repeat_smile", text: "Let's try once more.", durationMs: 1500, at: t0))
        r.apply(.captureCommanded(task: "smile", durationMs: 5000))
        XCTAssertEqual(r.screen.card, .task(step: 1, total: 3, task: "smile", timer: .waiting(totalSeconds: 5), hint: nil))

        r.apply(.speaking(lineId: "task_arms", text: "Raise both arms.", durationMs: 1500, at: t0))
        r.apply(.captureCommanded(task: "arms", durationMs: 12000))
        XCTAssertEqual(r.screen.card, .task(step: 2, total: 3, task: "arms", timer: .waiting(totalSeconds: 12), hint: nil))
        XCTAssertEqual(r.screen.dot(.smile), .done)

        r.apply(.speaking(lineId: "task_sentence",
                          text: "Please repeat this sentence after me: Today I am sitting by the window.",
                          durationMs: 4000, at: t0))
        XCTAssertFalse(r.screen.selfViewLarge)
        XCTAssertEqual(r.screen.card, .sentence(step: 3, total: 3, sentence: "Today I am sitting by the window.", listening: false))
        r.apply(.captureCommanded(task: "sentence", durationMs: 12000))
        r.apply(.captureStarted(durationMs: 12000, at: t0))
        XCTAssertEqual(r.screen.card, .sentence(step: 3, total: 3, sentence: "Today I am sitting by the window.", listening: true))

        r.apply(.speaking(lineId: "bye", text: "Thank you, Ona. Our check-in is finished. Goodbye.", durationMs: 3000, at: t0))
        r.apply(.ended(reason: "completed"))
        XCTAssertEqual(r.screen.mode, .done)
    }

    func testRecoveryLineDuringTaskShowsAssistantAndHangUpEnds() {
        var r = CallScreenReducer()
        r.apply(.ringing(callId: "c1", title: "Daily check-in"))
        r.apply(.connected)
        r.apply(.speaking(lineId: "task_smile", text: "Smile.", durationMs: 1000, at: t0))
        r.apply(.speaking(lineId: "camera_unavailable", text: "I can't see you right now.", durationMs: 2000, at: t0))
        guard case .assistant = r.screen.card else { return XCTFail("recovery uses the assistant card") }
        XCTAssertEqual(r.screen.stage, .smile)
        r.apply(.ended(reason: "hangup"))
        XCTAssertEqual(r.screen.mode, .ended(reason: "hangup"))
    }
}
