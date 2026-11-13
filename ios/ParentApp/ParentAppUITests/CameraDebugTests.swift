import XCTest

/// The camera debug screen against a running backend with Agent 3's analysis installed: Settings > Camera debug,
/// a smile test, live events, then the analysis's result. In the Simulator the frames come from the fake camera,
/// so the analysis finds no face; on a phone it is the front camera.
///
///     TEST_RUNNER_PARENT_BASE_URL=http://127.0.0.1:8000 TEST_RUNNER_PARENT_DEMO_KEY=... \
///     xcodebuild test -scheme ParentApp -destination 'platform=iOS Simulator,name=iPhone 18 Pro' \
///       -only-testing:ParentAppUITests/CameraDebugTests
final class CameraDebugTests: XCTestCase {
    func testSmileTestShowsEventsAndResult() throws {
        let env = ProcessInfo.processInfo.environment
        guard let base = env["PARENT_BASE_URL"], let key = env["PARENT_DEMO_KEY"] else {
            throw XCTSkip("Set TEST_RUNNER_PARENT_BASE_URL and TEST_RUNNER_PARENT_DEMO_KEY to run against a backend.")
        }
        let app = XCUIApplication()
        app.launchArguments = ["-parent.baseURL", base, "-parent.demoKey", key, "-parent.personId", "p_demo"]
        app.launch()
        XCTAssertTrue(app.staticTexts["Daily check-in"].waitForExistence(timeout: 10))
        app.staticTexts["Daily check-in"].press(forDuration: 2.5)

        let debug = app.buttons["Camera debug"]
        if !debug.waitForExistence(timeout: 5) {
            app.swipeUp()
        }
        XCTAssertTrue(debug.waitForExistence(timeout: 5), "Settings has no Camera debug button.")
        debug.tap()

        XCTAssertTrue(app.staticTexts["Camera debug"].waitForExistence(timeout: 5))
        app.buttons["Smile"].tap()
        app.buttons["Start smile test"].tap()
        XCTAssertTrue(app.staticTexts["Relax your face"].waitForExistence(timeout: 10), "The timeline did not start.")
        snap("relax")
        XCTAssertTrue(app.staticTexts["Smile wide!"].waitForExistence(timeout: 5), "No smile cue.")
        snap("smile")
        XCTAssertTrue(app.staticTexts["Result"].waitForExistence(timeout: 40), "No result from the analysis.")
        XCTAssertTrue(app.staticTexts["What the family would get"].waitForExistence(timeout: 5), "No verdict.")
        sleep(1)
        snap("result")
        XCTAssertTrue(app.staticTexts["Analysis events (newest first)"].exists || app.staticTexts["Latest frame"].exists
                      || app.staticTexts["Outcome"].exists)
    }

    private func snap(_ name: String) {
        let shot = XCUIScreen.main.screenshot()
        let attachment = XCTAttachment(screenshot: shot)
        attachment.name = name
        attachment.lifetime = .keepAlways
        add(attachment)
        if let dir = ProcessInfo.processInfo.environment["PARENT_SHOTS_DIR"] {
            try? shot.pngRepresentation.write(to: URL(fileURLWithPath: dir).appendingPathComponent("debug-\(name).png"))
        }
    }
}
