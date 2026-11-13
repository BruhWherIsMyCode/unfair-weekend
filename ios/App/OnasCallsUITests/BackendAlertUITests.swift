import XCTest

/// A real alert from the check-in backend: the app registers as a family device, the backend sends a change
/// message with a clip, the amber card appears, and the alert shows the backend's text and the frames.
///
///     TEST_RUNNER_ONAS_BASE_URL=http://127.0.0.1:8766 TEST_RUNNER_ONAS_DEMO_KEY=... \
///     TEST_RUNNER_SCREENSHOT_DIR=/tmp/shots xcodebuild test -scheme OnasCalls \
///       -destination 'platform=iOS Simulator,name=iPhone 18 Pro' -only-testing:OnasCallsUITests/BackendAlertUITests
///
/// Skipped without a backend. Something on the backend side must publish the alert once this phone registers
/// (a real call through Agent 3's analysis, or a test harness).
final class BackendAlertUITests: XCTestCase {
    func testAlertFromBackendShowsItsTextAndClip() throws {
        let env = ProcessInfo.processInfo.environment
        guard let base = env["ONAS_BASE_URL"], let key = env["ONAS_DEMO_KEY"] else {
            throw XCTSkip("Set TEST_RUNNER_ONAS_BASE_URL and TEST_RUNNER_ONAS_DEMO_KEY to run against a backend.")
        }
        let app = XCUIApplication()
        app.launchArguments = ["-family.baseURL", base, "-family.demoKey", key, "-family.familyId", "f_demo"]
        app.launch()
        let allow = XCUIApplication(bundleIdentifier: "com.apple.springboard").buttons["Allow"]
        if allow.waitForExistence(timeout: 3) { allow.tap() }

        // The backend publishes once this phone has registered.
        let card = app.buttons.containing(NSPredicate(format: "label CONTAINS %@", "Same on both tries")).firstMatch
        XCTAssertTrue(card.waitForExistence(timeout: 40), "No alert arrived from the backend.")
        snap("home-alert")
        card.tap()

        let report = app.staticTexts.containing(NSPredicate(format: "label CONTAINS %@", "different from his usual"))
            .firstMatch
        XCTAssertTrue(report.waitForExistence(timeout: 5), "The report does not show the backend's text.")
        XCTAssertTrue(app.staticTexts["clip, both tries"].waitForExistence(timeout: 10), "The clip did not load.")
        sleep(2)
        snap("alert-with-clip")
    }

    private func snap(_ name: String) {
        let shot = XCUIScreen.main.screenshot()
        let attachment = XCTAttachment(screenshot: shot)
        attachment.name = name
        attachment.lifetime = .keepAlways
        add(attachment)
        if let dir = ProcessInfo.processInfo.environment["SCREENSHOT_DIR"] {
            try? shot.pngRepresentation.write(to: URL(fileURLWithPath: dir).appendingPathComponent("backend-\(name).png"))
        }
    }
}
