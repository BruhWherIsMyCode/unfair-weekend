import XCTest

/// The profile: open it from the person's row, edit and save the profile, edit the call history, and keep a
/// call's clip (Save to Photos and the share sheet). The clip is drawn colour frames from `-uitest.framesClip`.
final class ProfileUITests: XCTestCase {
    private let app = XCUIApplication()
    private let springboard = XCUIApplication(bundleIdentifier: "com.apple.springboard")

    override func setUp() {
        continueAfterFailure = false
        app.launchArguments = ["-uitest.framesClip", "-uitest.resetProfile"]
        app.launch()
        let allow = springboard.buttons["Allow"].firstMatch
        if allow.waitForExistence(timeout: 3) { allow.tap() }
    }

    func testEditProfile() {
        resetAll()
        openProfile()

        app.buttons["profile.edit"].tap()
        XCTAssertTrue(app.navigationBars["Edit profile"].waitForExistence(timeout: 3))
        replace(app.textFields["profile.name"], with: "Rasa")
        replace(app.textFields["profile.relation"], with: "Mum")
        app.buttons["She"].tap()
        replace(app.textFields["profile.notes"], with: "Takes her pills at 9")
        snap("profile-edit")
        app.navigationBars["Edit profile"].buttons["Save"].tap()

        XCTAssertTrue(app.staticTexts["Rasa"].waitForExistence(timeout: 3))
        XCTAssertTrue(app.staticTexts["Takes her pills at 9"].exists)
        snap("profile-edited")

        // Saved: Family and the alert use the new name and "she"
        app.buttons.containing(predicate("Family")).firstMatch.tap()
        XCTAssertTrue(app.staticTexts.containing(predicate("Mum, last call")).firstMatch.waitForExistence(timeout: 3))
        openPanel()
        app.buttons["Show alert now"].tap()
        XCTAssertTrue(app.staticTexts["Call Rasa now"].waitForExistence(timeout: 3))
        XCTAssertTrue(app.staticTexts["NOT HER USUAL"].exists)

        // Kept after a relaunch
        app.terminate()
        app.launchArguments = []
        app.launch()
        XCTAssertTrue(app.staticTexts["Call Rasa now"].waitForExistence(timeout: 5))

        // Back to Fedya for the stage scene
        openPanel()
        app.buttons["Reset"].tap()
        app.buttons["Done"].tap()
        openProfile(row: "Mum, last call")
        app.buttons["profile.edit"].tap()
        replace(app.textFields["profile.name"], with: "Fedya")
        replace(app.textFields["profile.relation"], with: "Dad")
        app.buttons["He"].tap()
        replace(app.textFields["profile.notes"], with: "")
        app.navigationBars["Edit profile"].buttons["Save"].tap()
        XCTAssertTrue(app.staticTexts["Fedya"].waitForExistence(timeout: 3))
    }

    func testHistoryNoteDeleteAndClip() {
        resetAll()
        // resetAll restored the sample week; add the drawn-clip call again.
        app.terminate()
        app.launch()
        openProfile()

        // Open the call with a clip, keep it
        let call = app.buttons.containing(predicate("UI test call with a drawn clip")).firstMatch
        XCTAssertTrue(call.waitForExistence(timeout: 3))
        call.tap()
        XCTAssertTrue(app.staticTexts["clip, both tries"].waitForExistence(timeout: 5))
        let save = app.buttons["Save to Photos"]
        XCTAssertTrue(save.waitForExistence(timeout: 15), "The clip was not prepared.")
        snap("call-detail")

        let note = app.textViews.firstMatch.exists ? app.textViews.firstMatch : app.textFields.firstMatch
        note.tap()
        note.typeText("Called her, all fine")
        app.swipeDown()

        app.buttons["Share"].tap()
        XCTAssertTrue(app.otherElements["ActivityListView"].waitForExistence(timeout: 5)
                      || app.collectionViews.firstMatch.waitForExistence(timeout: 5))
        snap("share-sheet")
        let close = app.buttons["Close"]
        if close.exists { close.tap() } else { app.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.1)).tap() }

        save.tap()
        for label in ["Allow", "Allow Access", "Allow Full Access", "OK"] {
            let button = springboard.buttons[label].firstMatch
            if button.waitForExistence(timeout: 2) { button.tap(); break }
        }
        XCTAssertTrue(app.staticTexts["Saved to Photos."].waitForExistence(timeout: 10))
        snap("saved")

        // The note shows in the list
        app.navigationBars.buttons.firstMatch.tap()
        XCTAssertTrue(app.buttons.containing(predicate("Note: Called her, all fine")).firstMatch.waitForExistence(timeout: 3))

        // Edit history: delete a sample call
        let sample = app.buttons.containing(predicate("Answered every question")).firstMatch
        XCTAssertTrue(sample.exists)
        app.buttons["Edit history"].tap()
        XCTAssertTrue(app.buttons["Done"].waitForExistence(timeout: 3))
        snap("history-edit-mode")
        app.buttons["Done"].tap()
        sample.swipeLeft()
        let delete = app.buttons["Delete"].firstMatch
        XCTAssertTrue(delete.waitForExistence(timeout: 3))
        delete.tap()
        XCTAssertTrue(waitGone(sample, timeout: 3))
        snap("history-edited")

        app.buttons.containing(predicate("Family")).firstMatch.tap()
        resetAll()
    }

    // MARK: Helpers

    private func resetAll() {
        openPanel()
        app.buttons["Reset"].tap()
        let resetHistory = app.buttons["Reset call history"]
        if !resetHistory.isHittable { app.swipeUp() }
        resetHistory.tap()
        app.buttons["Done"].tap()
    }

    private func openPanel() {
        let title = app.staticTexts["Family"]
        XCTAssertTrue(title.waitForExistence(timeout: 5))
        title.press(forDuration: 2.5)
        XCTAssertTrue(app.navigationBars["Demo panel"].waitForExistence(timeout: 3))
    }

    private func openProfile(row: String = "Dad, last call") {
        let button = app.buttons.containing(predicate(row)).firstMatch
        XCTAssertTrue(button.waitForExistence(timeout: 3))
        button.tap()
        XCTAssertTrue(app.buttons["profile.edit"].waitForExistence(timeout: 3))
    }

    private func replace(_ field: XCUIElement, with text: String) {
        XCTAssertTrue(field.waitForExistence(timeout: 3))
        field.tap()
        if let value = field.value as? String, !value.isEmpty, value != field.placeholderValue {
            field.typeText(String(repeating: XCUIKeyboardKey.delete.rawValue, count: value.count))
        }
        field.typeText(text)
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
            try? shot.pngRepresentation.write(to: URL(fileURLWithPath: dir).appendingPathComponent("profile-\(name).png"))
        }
    }
}
