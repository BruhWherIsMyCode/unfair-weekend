import XCTest

/// Runs the stage scene in the Simulator through the tap map in docs/tasks/agent1.md.
/// Set TEST_RUNNER_SCREENSHOT_DIR to save a PNG of each step.
final class StageSceneUITests: XCTestCase {
    private let app = XCUIApplication()
    private let springboard = XCUIApplication(bundleIdentifier: "com.apple.springboard")

    override func setUp() {
        continueAfterFailure = false
        app.launch()
        allowNotificationsIfAsked()
    }

    /// Show alert now -> card -> Alert -> Call 112 (fake) -> Call Fedya (fake screen) -> Declined -> row -> He's fine.
    func testSceneWithFakeCallScreen() {
        openPanel()
        app.buttons["Reset"].tap()
        setFakeCallScreen(true)
        app.buttons["Show alert now"].tap()

        // Home (alert)
        XCTAssertTrue(app.staticTexts["Call Fedya now"].waitForExistence(timeout: 3))
        XCTAssertFalse(app.staticTexts["Ona"].exists)
        XCTAssertFalse(app.staticTexts["Aldona"].exists)
        snap("3-home-alert")

        // Amber card -> Alert
        app.buttons.containing(predicate("Same on both tries")).firstMatch.tap()
        XCTAssertTrue(app.staticTexts["Report"].waitForExistence(timeout: 3))
        XCTAssertTrue(app.staticTexts["If he seems unwell, call 112."].exists)
        sleep(3) // let the clip counter run
        snap("4-alert")

        // Back to Family, white row -> profile -> its alert banner -> Alert
        app.buttons.containing(predicate("Family")).firstMatch.tap()
        XCTAssertTrue(app.staticTexts["Call Fedya now"].waitForExistence(timeout: 3))
        app.buttons.containing(predicate("Dad, last call")).firstMatch.tap()
        XCTAssertTrue(app.buttons["profile.edit"].waitForExistence(timeout: 3))
        snap("profile-alert")
        app.buttons.containing(predicate("Open today's report")).firstMatch.tap()
        XCTAssertTrue(app.staticTexts["Report"].waitForExistence(timeout: 3))

        // Call 112: fake screen, 4 s, back to Alert. Never dials.
        app.buttons["Call 112"].tap()
        XCTAssertTrue(app.staticTexts["DEMO – not dialled"].waitForExistence(timeout: 2))
        snap("5b-fake-112")
        XCTAssertTrue(waitGone(app.staticTexts["DEMO – not dialled"], timeout: 7))
        XCTAssertTrue(app.staticTexts["Report"].exists)

        // Call Fedya with the fake call screen: 4 s, then Home (declined)
        app.buttons["Call Fedya"].tap()
        XCTAssertTrue(app.staticTexts["calling…"].waitForExistence(timeout: 2))
        snap("5-calling-fallback")
        XCTAssertTrue(app.staticTexts["Call Fedya again"].waitForExistence(timeout: 8))
        XCTAssertTrue(app.staticTexts.containing(predicate("Declined at")).firstMatch.exists)
        snap("6-declined")

        // Declined: green Call Fedya again -> calling; tap screen ends it
        app.buttons["Call Fedya"].tap()
        XCTAssertTrue(app.staticTexts["calling…"].waitForExistence(timeout: 2))
        app.staticTexts["calling…"].tap()
        XCTAssertTrue(app.staticTexts["Call Fedya again"].waitForExistence(timeout: 3))

        // Fedya row -> profile -> banner -> Alert -> He's fine -> Home (as usual)
        app.buttons.containing(predicate("you called at")).firstMatch.tap()
        app.buttons.containing(predicate("Call Fedya again")).firstMatch.tap()
        XCTAssertTrue(app.staticTexts["Report"].waitForExistence(timeout: 3))
        app.buttons["He's fine"].tap()
        XCTAssertTrue(app.staticTexts.containing(predicate("Dad, last call")).firstMatch.waitForExistence(timeout: 3))
        XCTAssertFalse(app.staticTexts["Call Fedya now"].exists)
        snap("7-as-usual")

        // The call stays in the profile's history with what the family did
        app.buttons.containing(predicate("Dad, last call")).firstMatch.tap()
        XCTAssertTrue(app.staticTexts.containing(predicate("You said he was fine")).firstMatch.waitForExistence(timeout: 3))
        app.buttons.containing(predicate("Family")).firstMatch.tap()

        openPanel()
        snap("panel")
        app.buttons["Reset"].tap()
        setFakeCallScreen(false)
        app.buttons["Done"].tap()
    }

    /// Arm with 10 s, leave the app, the notification arrives, tapping it opens Home (alert).
    func testArmDeliversNotification() {
        openPanel()
        app.buttons["Reset"].tap()
        app.buttons["10 s"].tap()
        app.buttons["Arm"].tap()
        XCTAssertTrue(app.staticTexts["Call Fedya now"].waitForExistence(timeout: 3))

        XCUIDevice.shared.press(.home)
        let banner = springboard.descendants(matching: .any).matching(predicate("He seems different from his usual")).firstMatch
        XCTAssertTrue(banner.waitForExistence(timeout: 20), "notification did not arrive")
        snap("2-notification")
        banner.tap()

        XCTAssertTrue(app.wait(for: .runningForeground, timeout: 5))
        XCTAssertTrue(app.staticTexts["Call Fedya now"].waitForExistence(timeout: 3))

        openPanel()
        app.buttons["Reset"].tap()
        app.buttons["Done"].tap()
        XCTAssertFalse(app.staticTexts["Call Fedya now"].waitForExistence(timeout: 1))
    }

    // MARK: Helpers

    private func openPanel() {
        let title = app.staticTexts["Family"]
        XCTAssertTrue(title.waitForExistence(timeout: 5))
        title.press(forDuration: 2.5)
        XCTAssertTrue(app.navigationBars["Demo panel"].waitForExistence(timeout: 3))
    }

    private func setFakeCallScreen(_ on: Bool) {
        let toggle = app.switches["Fake call screen"]
        XCTAssertTrue(toggle.waitForExistence(timeout: 2))
        if (toggle.value as? String == "1") != on {
            toggle.coordinate(withNormalizedOffset: CGVector(dx: 0.93, dy: 0.5)).tap()
        }
        XCTAssertEqual(toggle.value as? String, on ? "1" : "0")
    }

    private func allowNotificationsIfAsked() {
        let allow = springboard.buttons["Allow"]
        if allow.waitForExistence(timeout: 3) { allow.tap() }
    }

    private func predicate(_ text: String) -> NSPredicate {
        NSPredicate(format: "label CONTAINS %@", text)
    }

    private func waitGone(_ element: XCUIElement, timeout: TimeInterval) -> Bool {
        let gone = XCTNSPredicateExpectation(predicate: NSPredicate(format: "exists == false"), object: element)
        return XCTWaiter.wait(for: [gone], timeout: timeout) == .completed
    }

    private func snap(_ name: String) {
        let shot = XCUIScreen.main.screenshot()
        let attachment = XCTAttachment(screenshot: shot)
        attachment.name = name
        attachment.lifetime = .keepAlways
        add(attachment)
        if let dir = ProcessInfo.processInfo.environment["SCREENSHOT_DIR"] {
            try? shot.pngRepresentation.write(to: URL(fileURLWithPath: dir).appendingPathComponent("\(name).png"))
        }
    }
}
