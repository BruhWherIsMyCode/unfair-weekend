import XCTest

/// One whole call in the Simulator against a running backend: start the call, tap Accept, follow every
/// screen to "Thank you", then out to the home screen and, reopened, back to waiting. Screenshots are attached to the result, and also written
/// to PARENT_SHOTS_DIR when set.
///
///     TEST_RUNNER_PARENT_BASE_URL=http://127.0.0.1:8000 TEST_RUNNER_PARENT_DEMO_KEY=... \
///     TEST_RUNNER_PARENT_SHOTS_DIR=/tmp/shots xcodebuild test -scheme ParentApp \
///       -destination 'platform=iOS Simulator,name=iPhone 18 Pro'
///
/// The Simulator has no camera, so the smile and arms steps take the backend's camera-loss path.
final class CallWalkthroughTests: XCTestCase {
    private var shotsDir: URL?
    private var shotIndex = 0

    func testWholeCallAgainstLocalBackend() throws {
        let env = ProcessInfo.processInfo.environment
        guard let base = env["PARENT_BASE_URL"], let key = env["PARENT_DEMO_KEY"] else {
            throw XCTSkip("Set TEST_RUNNER_PARENT_BASE_URL and TEST_RUNNER_PARENT_DEMO_KEY to run against a backend.")
        }
        if let dir = env["PARENT_SHOTS_DIR"] {
            shotsDir = URL(fileURLWithPath: dir)
            try? FileManager.default.createDirectory(at: shotsDir!, withIntermediateDirectories: true)
        }

        let app = XCUIApplication()
        app.launchArguments = ["-parent.baseURL", base, "-parent.demoKey", key, "-parent.personId", "p_demo"]
        app.launch()
        XCTAssertTrue(app.staticTexts["Daily check-in"].waitForExistence(timeout: 10))
        // Registration and the event stream need a moment before the call is placed.
        Thread.sleep(forTimeInterval: 3)
        snap("idle")

        try startCall(base: base, key: key)
        let accept = app.buttons["Accept"]
        XCTAssertTrue(accept.waitForExistence(timeout: 15), "The ring screen did not appear.")
        Thread.sleep(forTimeInterval: 1)
        snap("ring")
        accept.tap()

        let thanks = app.staticTexts["Thank you"]
        let deadline = Date().addingTimeInterval(180)
        while Date() < deadline && !thanks.exists {
            snap("call")
            Thread.sleep(forTimeInterval: 1.2)
        }
        XCTAssertTrue(thanks.exists, "The call did not reach the Done screen.")
        Thread.sleep(forTimeInterval: 1.5)
        snap("done")

        // After about 3 s Done leaves to the iPhone home screen; reopened, the app is waiting again.
        // XCUIApplication.state does not notice a self-suspend, so look for the home screen's icon instead.
        let icon = XCUIApplication(bundleIdentifier: "com.apple.springboard").icons["Check-in"]
        let left = Date().addingTimeInterval(10)
        while Date() < left && !(icon.exists && !icon.frame.isEmpty) {
            Thread.sleep(forTimeInterval: 0.3)
        }
        XCTAssertTrue(icon.exists && !icon.frame.isEmpty, "The app did not leave to the home screen after Done.")
        snap("home-screen")
        app.activate()
        XCTAssertTrue(app.staticTexts["Daily check-in"].waitForExistence(timeout: 5))
        XCTAssertFalse(thanks.exists)
        snap("idle-again")
    }

    private func startCall(base: String, key: String) throws {
        var request = URLRequest(url: URL(string: base + "/v1/calls")!)
        request.httpMethod = "POST"
        request.setValue(key, forHTTPHeaderField: "X-Demo-Key")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = Data(#"{"person_id":"p_demo"}"#.utf8)
        let done = expectation(description: "call started")
        var status = 0
        URLSession.shared.dataTask(with: request) { _, response, _ in
            status = (response as? HTTPURLResponse)?.statusCode ?? 0
            done.fulfill()
        }.resume()
        wait(for: [done], timeout: 10)
        XCTAssertTrue((200..<300).contains(status), "POST /v1/calls returned \(status)")
    }

    private func snap(_ name: String) {
        let shot = XCUIScreen.main.screenshot()
        let attachment = XCTAttachment(screenshot: shot)
        attachment.name = name
        attachment.lifetime = .keepAlways
        add(attachment)
        if let shotsDir {
            let file = shotsDir.appendingPathComponent(String(format: "%03d-%@.png", shotIndex, name))
            try? shot.pngRepresentation.write(to: file)
        }
        shotIndex += 1
    }
}
