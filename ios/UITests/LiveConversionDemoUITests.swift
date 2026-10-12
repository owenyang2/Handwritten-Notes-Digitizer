import XCTest

/// Opt-in acceptance against a disposable local account and installed models.
/// Normal CI skips this test instead of replacing recognition with a fixture.
final class LiveConversionDemoUITests: XCTestCase {
    @MainActor
    func testLiveFormulaAndProseReviewExport() throws {
        let environment = ProcessInfo.processInfo.environment
        guard let username = environment["HND_LIVE_DEMO_USERNAME"],
              let password = environment["HND_LIVE_DEMO_PASSWORD"] else {
            throw XCTSkip("Requires a disposable local demo account and real models.")
        }
        continueAfterFailure = false
        let app = XCUIApplication()
        app.launchArguments = ["--ui-testing"]
        app.launchEnvironment["HND_UI_TEST_RUN_ID"] = UUID().uuidString
        app.launch()
        XCTAssertTrue(app.buttons["Sign In"].waitForExistence(timeout: 15))
        app.buttons["Sign In"].tap()
        app.textFields["Username"].tap()
        app.textFields["Username"].typeText(username)
        app.secureTextFields["Password"].tap()
        app.secureTextFields["Password"].typeText(password)
        app.buttons["Sign In"].tap()

        for (title, action, kind) in [
            ("Formula recognition demo", "Convert Page", "Formula"),
            ("Handwritten prose demo", "Convert Prose", "Prose")
        ] {
            let notebook = app.buttons[title].firstMatch
            XCTAssertTrue(notebook.waitForExistence(timeout: 60), app.debugDescription)
            notebook.tap()
            XCTAssertTrue(app.navigationBars[title].waitForExistence(timeout: 15), app.debugDescription)
            let actions = app.buttons["Editor actions"]
            XCTAssertTrue(actions.waitForExistence(timeout: 15))
            actions.tap()
            XCTAssertTrue(app.buttons[action].waitForExistence(timeout: 5), app.debugDescription)
            app.buttons[action].tap()
            let review = app.buttons["Review Conversion"]
            XCTAssertTrue(review.waitForExistence(timeout: 90), app.debugDescription)
            review.tap()
            XCTAssertTrue(app.buttons[kind].waitForExistence(timeout: 15), app.debugDescription)
            XCTAssertTrue(app.textViews.firstMatch.waitForExistence(timeout: 10))
            let original = app.textViews.firstMatch.value as? String
            XCTAssertFalse((original ?? "").isEmpty)
            app.buttons["Accept / Save"].tap()
            XCTAssertTrue(app.staticTexts["Review saved."].waitForExistence(timeout: 10), app.debugDescription)
            let exportButton = app.buttons["conversion-export"]
            XCTAssertTrue(exportButton.waitForExistence(timeout: 5), app.debugDescription)
            let exportReady = expectation(for: NSPredicate { _, _ in exportButton.isHittable }, evaluatedWith: exportButton)
            wait(for: [exportReady], timeout: 15)
            exportButton.tap()
            XCTAssertTrue(app.buttons["Markdown"].waitForExistence(timeout: 5))
            app.buttons["Markdown"].tap()
            XCTAssertTrue(app.buttons["Close"].waitForExistence(timeout: 15), app.debugDescription)
            app.buttons["Close"].tap()
            app.buttons["Done"].tap()
            review.tap()
            XCTAssertTrue(app.textViews.firstMatch.waitForExistence(timeout: 10))
            XCTAssertEqual(app.textViews.firstMatch.value as? String, original)
            let attachment = XCTAttachment(screenshot: app.screenshot())
            attachment.name = "Saved \(kind) review"
            attachment.lifetime = .keepAlways
            add(attachment)
            app.buttons["Done"].tap()
            app.buttons["Library"].tap()
        }
    }
}
