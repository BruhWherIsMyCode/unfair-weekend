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
        XCTAssertEqual(r.screen.card, .task(step: 1, total: 3, task: "smile", part: .relax, timer: .hidden, hint: nil))
        r.apply(.captureCommanded(task: "smile", durationMs: 6000))
        r.apply(.captureStarted(durationMs: 6000, at: t0))
        r.apply(.hint("look_at_camera"))
        r.apply(.captureFinished)
        XCTAssertEqual(r.screen.card, .task(step: 1, total: 3, task: "smile", part: .relax, timer: .finished, hint: nil))

        r.apply(.speaking(lineId: "repeat_smile", text: "Let's try once more.", durationMs: 1500, at: t0))
        XCTAssertEqual(r.screen.card, .task(step: 1, total: 3, task: "smile", part: .relax, timer: .hidden, hint: nil))

        r.apply(.speaking(lineId: "task_arms", text: "Raise your left hand.", durationMs: 1500, at: t0))
        r.apply(.captureCommanded(task: "arms", durationMs: 15000))
        XCTAssertEqual(r.screen.card, .task(step: 2, total: 3, task: "arms", part: .leftHand, timer: .hidden, hint: nil))
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

    /// Instruction: no ring. Relaxed: no ring. "Now smile!": the ring counts the rest of the 6 s capture.
    func testSmileTimerRunsOnlyAfterNowSmile() {
        var r = inCall()
        r.apply(.speaking(lineId: "task_smile", text: "First keep your face relaxed.", durationMs: 6000, at: t0))
        XCTAssertEqual(r.screen.card, .task(step: 1, total: 3, task: "smile", part: .relax, timer: .hidden, hint: nil))
        r.apply(.captureCommanded(task: "smile", durationMs: 6000))
        let start = t0.addingTimeInterval(7)
        r.apply(.captureStarted(durationMs: 6000, at: start))
        XCTAssertEqual(r.screen.card, .task(step: 1, total: 3, task: "smile", part: .relax, timer: .hidden, hint: nil))

        let cue = start.addingTimeInterval(1.6)
        r.apply(.speaking(lineId: "smile_now", text: "Now smile!", durationMs: 800, at: cue))
        XCTAssertTrue(r.screen.selfViewLarge, "the cue must not shrink the self-view")
        guard case .task(_, _, "smile", .smile, .running(let endsAt, let total), nil) = r.screen.card else {
            return XCTFail("smile countdown after the cue, got \(String(describing: r.screen.card))")
        }
        XCTAssertEqual(endsAt, start.addingTimeInterval(6))
        XCTAssertEqual(total, 4.4, accuracy: 0.001)

        r.apply(.captureFinished)
        XCTAssertEqual(r.screen.card, .task(step: 1, total: 3, task: "smile", part: .smile, timer: .finished, hint: nil))
    }

    /// Left hand counts down to the swap (6.5 s), then the right hand counts down to the end of the 15 s capture.
    func testArmsTimerCountsEachHand() {
        var r = inCall()
        r.apply(.speaking(lineId: "task_arms", text: "Raise your left hand beside your face now.", durationMs: 4000, at: t0))
        r.apply(.captureCommanded(task: "arms", durationMs: 15000))
        XCTAssertEqual(r.screen.card, .task(step: 2, total: 3, task: "arms", part: .leftHand, timer: .hidden, hint: nil))
        let start = t0.addingTimeInterval(5)
        r.apply(.captureStarted(durationMs: 15000, at: start))
        XCTAssertEqual(r.screen.card, .task(step: 2, total: 3, task: "arms", part: .leftHand,
                                            timer: .running(endsAt: start.addingTimeInterval(6.5), total: 6.5), hint: nil))
        r.apply(.hint("show_both_arms"))

        let swap = start.addingTimeInterval(6.7)
        r.apply(.speaking(lineId: "swap_hands", text: "Now swap.", durationMs: 3000, at: swap))
        XCTAssertTrue(r.screen.selfViewLarge)
        guard case .task(_, _, "arms", .rightHand, .running(let endsAt, let total), nil) = r.screen.card else {
            return XCTFail("right-hand countdown after the swap, got \(String(describing: r.screen.card))")
        }
        XCTAssertEqual(endsAt, start.addingTimeInterval(15))
        XCTAssertEqual(total, 8.3, accuracy: 0.001)

        r.apply(.captureFinished)
        guard case .task(_, _, "arms", .rightHand, .finished, nil) = r.screen.card else { return XCTFail("finished") }
    }

    /// A cue for another task, or outside a task, is an ordinary assistant line.
    func testCueOutsideItsTaskIsAnAssistantLine() {
        var r = inCall()
        r.apply(.speaking(lineId: "smile_now", text: "Now smile!", durationMs: 800, at: t0))
        guard case .assistant = r.screen.card else { return XCTFail("assistant card") }
        r.apply(.speaking(lineId: "task_arms", text: "Raise your left hand.", durationMs: 1000, at: t0))
        r.apply(.speaking(lineId: "smile_now", text: "Now smile!", durationMs: 800, at: t0))
        guard case .assistant = r.screen.card else { return XCTFail("smile cue during arms is not a task cue") }
    }

    private func inCall() -> CallScreenReducer {
        var r = CallScreenReducer()
        r.apply(.ringing(callId: "c1", title: "Daily check-in"))
        r.apply(.connected)
        return r
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
