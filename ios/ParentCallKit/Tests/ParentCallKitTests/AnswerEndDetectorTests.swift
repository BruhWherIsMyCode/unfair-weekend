import XCTest
@testable import ParentCallKit

final class AnswerEndDetectorTests: XCTestCase {
    /// Feeds one level every 100 ms and returns the time of the first decision that is not keepListening.
    private func run(_ detector: inout AnswerEndDetector, levels: [Float]) -> (AnswerEndDetector.Decision, Int)? {
        for (index, level) in levels.enumerated() {
            let atMs = (index + 1) * 100
            let decision = detector.add(levelDb: level, atMs: atMs)
            if decision != .keepListening {
                return (decision, atMs)
            }
        }
        return nil
    }

    private func repeated(_ level: Float, seconds: Double) -> [Float] {
        Array(repeating: level, count: Int(seconds * 10))
    }

    /// The bug from the hall: background around -32 dB never fell under the old fixed -35 dB, so it never ended.
    func testAnswerInANoisyRoomEndsAfterTheSilence() {
        var detector = AnswerEndDetector(stopOnSilenceMs: 1500)
        let levels = repeated(-32, seconds: 1) + repeated(-15, seconds: 2) + repeated(-32, seconds: 5)
        let result = run(&detector, levels: levels)
        XCTAssertEqual(result?.0, .finished)
        // speech ends at 3.0 s; the smoothed level drops under the threshold one reading later, then 1.5 s of quiet
        XCTAssertLessThanOrEqual(result?.1 ?? .max, 4700)
    }

    func testAnswerInAQuietRoomEndsAfterTheSilence() {
        var detector = AnswerEndDetector(stopOnSilenceMs: 1500)
        let levels = repeated(-65, seconds: 0.5) + repeated(-30, seconds: 1.5) + repeated(-64, seconds: 3)
        XCTAssertEqual(run(&detector, levels: levels)?.0, .finished)
    }

    func testShortPausesBetweenWordsDoNotEndTheAnswer() {
        var detector = AnswerEndDetector(stopOnSilenceMs: 1500)
        let word = repeated(-18, seconds: 0.6)
        let pause = repeated(-34, seconds: 0.8)
        let levels = repeated(-34, seconds: 0.5) + word + pause + word + pause + word
        XCTAssertNil(run(&detector, levels: levels))
        XCTAssertTrue(detector.heardSpeech)
    }

    func testNobodySpeakingStopsAtTheNoAnswerLimit() {
        var detector = AnswerEndDetector(stopOnSilenceMs: 1500, noAnswerMs: 8000)
        let result = run(&detector, levels: repeated(-34, seconds: 12))
        XCTAssertEqual(result?.0, .noAnswer)
        XCTAssertEqual(result?.1, 8000)
    }

    func testTheMeterBeforeTheFirstBufferIsNotTakenAsTheRoom() {
        var detector = AnswerEndDetector(stopOnSilenceMs: 1500)
        _ = detector.add(levelDb: -160, atMs: 100)
        XCTAssertEqual(detector.noiseFloorDb, -80)
        XCTAssertEqual(detector.speechThresholdDb, -50)  // never asks for more than a quiet voice
    }

    func testWithoutStopOnSilenceItNeverEndsOnItsOwn() {
        var detector = AnswerEndDetector(stopOnSilenceMs: nil, noAnswerMs: .max)
        let levels = repeated(-60, seconds: 1) + repeated(-20, seconds: 1) + repeated(-60, seconds: 5)
        XCTAssertNil(run(&detector, levels: levels))
    }
}
