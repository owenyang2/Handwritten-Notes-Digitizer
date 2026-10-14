import XCTest

final class PersonalizedNotesUITests: XCTestCase {
    @MainActor
    private func launchApp(runID: String, largeText: Bool = false) -> XCUIApplication {
        let app = XCUIApplication()
        app.launchArguments += ["--ui-testing"]
        if largeText {
            app.launchArguments += ["-UIPreferredContentSizeCategoryName", "UICTContentSizeCategoryAccessibilityXXXL"]
        }
        app.launchEnvironment["HND_UI_TEST_RUN_ID"] = runID
        app.launch()
        XCTAssertTrue(app.wait(for: .runningForeground, timeout: 15))
        XCTAssertTrue(app.windows.firstMatch.waitForExistence(timeout: 10))
        XCTAssertGreaterThan(app.windows.firstMatch.frame.height, 480, "The app must use the full device viewport, not legacy iPhone compatibility sizing.")
        return app
    }

    @MainActor
    private func openEditorMenu(_ name: String, in app: XCUIApplication) {
        // SwiftUI moves trailing toolbar menus into More on compact screens.
        if !app.buttons[name].exists { app.buttons["More"].tap() }
        XCTAssertTrue(app.buttons[name].waitForExistence(timeout: 5))
        app.buttons[name].tap()
    }

    @MainActor
    private func revealMenuItem(_ name: String, in app: XCUIApplication) -> XCUIElement {
        let item = app.buttons[name].firstMatch
        let menu = app.collectionViews.allElementsBoundByIndex.first { $0.frame.height > 200 } ?? app.collectionViews.firstMatch
        // The overflow menu can extend behind PencilKit's floating palette. Keep drags
        // inside its visible upper area; the default swipe starts under the palette.
        let upper = menu.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.15))
        let lower = menu.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.55))
        for _ in 0..<4 {
            if item.exists && item.isHittable { return item }
            lower.press(forDuration: 0.05, thenDragTo: upper)
        }
        for _ in 0..<8 {
            if item.exists && item.isHittable { return item }
            upper.press(forDuration: 0.05, thenDragTo: lower)
        }
        let screenshot = XCTAttachment(screenshot: app.screenshot())
        screenshot.name = "Unreachable menu item: \(name)"
        screenshot.lifetime = .keepAlways
        add(screenshot)
        let hierarchy = XCTAttachment(string: app.debugDescription)
        hierarchy.lifetime = .keepAlways
        add(hierarchy)
        return item
    }

    @MainActor
    func testWelcomeAndOfflineNotebookCreationPersistAfterRelaunch() throws {
        continueAfterFailure = false
        let runID = UUID().uuidString
        let app = launchApp(runID: runID)
        let continueOffline = app.buttons["Continue Offline"]
        XCTAssertTrue(continueOffline.waitForExistence(timeout: 10))
        continueOffline.tap()

        XCTAssertTrue(app.navigationBars["Library"].waitForExistence(timeout: 15))
        XCTAssertTrue(app.buttons["Getting Started"].waitForExistence(timeout: 10))
        app.buttons["Getting Started"].tap()
        XCTAssertTrue(app.navigationBars["Getting Started"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.buttons["Done"].waitForExistence(timeout: 5))
        app.buttons["Done"].tap()

        let newButton = app.buttons["New"]
        XCTAssertTrue(newButton.waitForExistence(timeout: 10))
        newButton.tap()

        let newNotebook = app.buttons["New Notebook"]
        XCTAssertTrue(newNotebook.waitForExistence(timeout: 5))
        newNotebook.tap()

        let title = "UI Test Notebook \(UUID().uuidString.prefix(8))"
        let titleField = app.textFields["Title"]
        XCTAssertTrue(titleField.waitForExistence(timeout: 5))
        titleField.tap()
        titleField.typeText(title)
        app.buttons["Create"].tap()

        let editorTitle = app.navigationBars[title]
        XCTAssertTrue(editorTitle.waitForExistence(timeout: 10))
        XCTAssertTrue(app.staticTexts["Pages"].waitForExistence(timeout: 5))

        app.terminate()
        app.launchEnvironment["HND_UI_TEST_RUN_ID"] = runID
        app.launch()
        XCTAssertTrue(app.wait(for: .runningForeground, timeout: 15))
        XCTAssertTrue(app.buttons["Continue Offline"].waitForExistence(timeout: 10))
        app.buttons["Continue Offline"].tap()

        XCTAssertTrue(app.buttons[title].waitForExistence(timeout: 10))
    }

    @MainActor
    func testAppearancePersistsAfterRelaunch() throws {
        continueAfterFailure = false
        let runID = UUID().uuidString
        let app = launchApp(runID: runID)
        app.buttons["Continue Offline"].tap()
        XCTAssertTrue(app.buttons["library-more"].waitForExistence(timeout: 15))
        app.buttons["library-more"].tap()
        app.buttons["Settings"].tap()
        let appearance = app.buttons["appearance-picker"]
        XCTAssertTrue(appearance.waitForExistence(timeout: 5))
        appearance.tap()
        app.buttons["Dark"].tap()
        app.buttons["Done"].tap()
        let libraryAttachment = XCTAttachment(screenshot: app.screenshot())
        libraryAttachment.name = "Dark Library and primary action"
        libraryAttachment.lifetime = .keepAlways
        add(libraryAttachment)
        app.terminate()
        app.launch()
        XCTAssertTrue(app.buttons["Continue Offline"].waitForExistence(timeout: 10))
        app.buttons["Continue Offline"].tap()
        XCTAssertTrue(app.buttons["library-more"].waitForExistence(timeout: 15), app.debugDescription)
        app.buttons["library-more"].tap()
        app.buttons["Settings"].tap()
        XCTAssertTrue(app.buttons["appearance-picker"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.buttons["appearance-picker"].label.contains("Dark"))
        let attachment = XCTAttachment(screenshot: app.screenshot())
        attachment.name = "Persisted dark appearance"
        attachment.lifetime = .keepAlways
        add(attachment)
        app.buttons["appearance-picker"].tap()
        app.buttons["System"].tap()
    }

    @MainActor
    func testLargestDynamicTypeKeepsLibraryAndSettingsReachable() throws {
        continueAfterFailure = false
        let app = launchApp(runID: UUID().uuidString, largeText: true)
        let offline = app.buttons["Continue Offline"]
        for _ in 0..<5 {
            if offline.isHittable { break }
            app.scrollViews.firstMatch.swipeUp()
        }
        XCTAssertTrue(offline.waitForExistence(timeout: 10))
        let welcomeAttachment = XCTAttachment(screenshot: app.screenshot())
        welcomeAttachment.name = "Largest Dynamic Type welcome"
        welcomeAttachment.lifetime = .keepAlways
        add(welcomeAttachment)
        XCTAssertTrue(offline.isHittable)
        offline.tap()
        XCTAssertTrue(app.buttons["library-more"].waitForExistence(timeout: 15))
        app.buttons["library-more"].tap()
        app.buttons["Settings"].tap()
        let appearance = app.buttons["appearance-picker"]
        XCTAssertTrue(appearance.waitForExistence(timeout: 10))
        XCTAssertTrue(appearance.isHittable)
        appearance.tap()
        app.buttons["Dark"].tap()
        let attachment = XCTAttachment(screenshot: app.screenshot())
        attachment.name = "Largest Dynamic Type Settings"
        attachment.lifetime = .keepAlways
        add(attachment)
        app.buttons["Done"].tap()
        let newNotebookReady = expectation(for: NSPredicate(format: "hittable == true"), evaluatedWith: app.buttons["New"])
        wait(for: [newNotebookReady], timeout: 10)
        app.buttons["New"].tap()
        app.buttons["New Notebook"].tap()
        let title = app.textFields["Title"]
        XCTAssertTrue(title.waitForExistence(timeout: 5))
        title.tap()
        title.typeText("Large text notebook")
        XCTAssertTrue(app.buttons["Create"].isHittable)
        let keyboardAttachment = XCTAttachment(screenshot: app.screenshot())
        keyboardAttachment.name = "Largest Dynamic Type keyboard and create"
        keyboardAttachment.lifetime = .keepAlways
        add(keyboardAttachment)
        app.buttons["Create"].tap()
        XCTAssertTrue(app.navigationBars["Large text notebook"].waitForExistence(timeout: 10))
    }

    @MainActor
    func testAuthFormsExposeAccessibleValidationControls() throws {
        continueAfterFailure = false
        let app = launchApp(runID: UUID().uuidString)
        XCTAssertTrue(app.buttons["Create Account"].waitForExistence(timeout: 10))
        app.buttons["Create Account"].tap()

        XCTAssertTrue(app.textFields["Username"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.textFields["Email"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.secureTextFields["Password"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.secureTextFields["Confirm password"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.buttons["Show password"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.buttons["Continue Offline"].waitForExistence(timeout: 5))

        XCTAssertTrue(app.buttons["Back to Welcome"].waitForExistence(timeout: 5))
        app.buttons["Back to Welcome"].tap()
        XCTAssertTrue(app.buttons["Sign In"].waitForExistence(timeout: 5))
        app.buttons["Sign In"].tap()
        XCTAssertTrue(app.buttons["Forgot password?"].waitForExistence(timeout: 5))
        app.buttons["Forgot password?"].tap()
        XCTAssertTrue(app.textFields["Email"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.buttons["Send Reset Token"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.buttons["Back to Sign In"].waitForExistence(timeout: 5))
    }

    @MainActor
    func testEditorPageActionsExposeAddedAndDuplicatedPages() throws {
        continueAfterFailure = false
        let app = launchApp(runID: UUID().uuidString)
        app.buttons["Continue Offline"].tap()
        app.buttons["New"].tap()
        app.buttons["New Notebook"].tap()

        let title = "Page UI Test \(UUID().uuidString.prefix(8))"
        let titleField = app.textFields["Title"]
        XCTAssertTrue(titleField.waitForExistence(timeout: 5))
        titleField.tap()
        titleField.typeText(title)
        app.buttons["Create"].tap()

        XCTAssertTrue(app.staticTexts["Page 1"].waitForExistence(timeout: 10))
        XCTAssertTrue(
            app.descendants(matching: .any)["Writing canvas for page 1"]
                .waitForExistence(timeout: 5)
        )
        openEditorMenu("Tools", in: app)
        XCTAssertTrue(app.buttons["Import into This Notebook"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.buttons["Import as New Notebook"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.buttons["Import Photos into This Notebook"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.buttons["Import Photos as New Notebook"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.buttons["Black Pen"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.buttons["Blue Pen"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.buttons["Yellow Highlighter"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.buttons["Vector Eraser"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.buttons["Pen"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.buttons["Pencil"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.buttons["Marker"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.buttons["Eraser"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.buttons["Lasso"].waitForExistence(timeout: 5))
        XCTAssertTrue(revealMenuItem("Toggle Ruler", in: app).exists)
        app.buttons["Eraser"].firstMatch.tap()

        openEditorMenu("Editor actions", in: app)
        XCTAssertTrue(app.buttons["Convert Page"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.buttons["Convert Notebook"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.buttons["Rename Page"].waitForExistence(timeout: 5))
        app.buttons["Rename Page"].tap()
        let pageTitleField = app.textFields["Page title"]
        XCTAssertTrue(pageTitleField.waitForExistence(timeout: 5))
        pageTitleField.tap()
        pageTitleField.typeText("Introduction")
        app.buttons["Save"].tap()
        XCTAssertTrue(app.staticTexts["Introduction"].waitForExistence(timeout: 5))
        XCTAssertFalse(app.staticTexts["Page 1"].waitForExistence(timeout: 3))

        openEditorMenu("Editor actions", in: app)
        XCTAssertTrue(app.buttons["Paper Template"].waitForExistence(timeout: 5))
        app.buttons["Paper Template"].tap()
        XCTAssertTrue(app.buttons["Ruled"].waitForExistence(timeout: 5))
        app.buttons["Ruled"].tap()
        openEditorMenu("Editor actions", in: app)
        XCTAssertTrue(app.buttons["Add Page"].waitForExistence(timeout: 5))
        app.buttons["Add Page"].tap()
        XCTAssertTrue(app.staticTexts["Page 2"].waitForExistence(timeout: 10))

        openEditorMenu("Editor actions", in: app)
        XCTAssertTrue(app.buttons["Duplicate Page"].waitForExistence(timeout: 5))
        app.buttons["Duplicate Page"].tap()
        XCTAssertTrue(app.staticTexts["Page 3"].waitForExistence(timeout: 10))

        openEditorMenu("Editor actions", in: app)
        XCTAssertTrue(app.buttons["Move Page Up"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.buttons["Move Page Down"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.buttons["Delete Page"].waitForExistence(timeout: 5))
        app.buttons["Delete Page"].tap()
        XCTAssertTrue(app.buttons["Delete"].waitForExistence(timeout: 5))
        app.buttons["Delete"].tap()
        let pageRemoved = expectation(for: NSPredicate(format: "exists == false"), evaluatedWith: app.staticTexts["Page 3"])
        wait(for: [pageRemoved], timeout: 10)
        XCTAssertTrue(app.staticTexts["Page 2"].waitForExistence(timeout: 5))
    }

    @MainActor
    func testEditorExposesPageAndNotebookExportActions() throws {
        continueAfterFailure = false
        let app = launchApp(runID: UUID().uuidString)
        app.buttons["Continue Offline"].tap()
        app.buttons["New"].tap()
        app.buttons["New Notebook"].tap()

        let title = "Export UI Test \(UUID().uuidString.prefix(8))"
        let titleField = app.textFields["Title"]
        XCTAssertTrue(titleField.waitForExistence(timeout: 5))
        titleField.tap()
        titleField.typeText(title)
        app.buttons["Create"].tap()

        XCTAssertTrue(app.staticTexts["Page 1"].waitForExistence(timeout: 10))
        openEditorMenu("Editor actions", in: app)
        XCTAssertTrue(revealMenuItem("Export Page as PDF", in: app).exists)
        XCTAssertTrue(revealMenuItem("Export Notebook as PDF", in: app).exists)
        XCTAssertTrue(revealMenuItem("Export Page as PNG", in: app).exists)
        XCTAssertTrue(revealMenuItem("Export Page as JPEG", in: app).exists)
        XCTAssertTrue(revealMenuItem("Export Page as ink_v1", in: app).exists)
        XCTAssertTrue(revealMenuItem("Export Native Notebook Archive", in: app).exists)
        XCTAssertTrue(revealMenuItem("Save Notebook PDF to Files", in: app).exists)
        XCTAssertTrue(revealMenuItem("Print Notebook", in: app).exists)
        XCTAssertTrue(revealMenuItem("Save Native Archive to Files", in: app).exists)

        revealMenuItem("Export Page as PDF", in: app).tap()
        // UIActivityViewController is hosted by a system remote container on iPad;
        // its internal navigation-bar label varies by OS. The stable contract is
        // that the share UI presents a dismiss button.
        XCTAssertTrue(app.buttons["Close"].waitForExistence(timeout: 10))
        app.buttons["Close"].tap()
    }

    @MainActor
    func testLibraryPageTitleSearchOpensMatchingPage() throws {
        continueAfterFailure = false
        let app = launchApp(runID: UUID().uuidString)
        app.buttons["Continue Offline"].tap()
        app.buttons["New"].tap()
        app.buttons["New Notebook"].tap()

        let title = "Search UI Test (UUID().uuidString.prefix(8))"
        let titleField = app.textFields["Title"]
        XCTAssertTrue(titleField.waitForExistence(timeout: 5))
        titleField.tap()
        titleField.typeText(title)
        app.buttons["Create"].tap()

        XCTAssertTrue(app.staticTexts["Page 1"].waitForExistence(timeout: 10))
        openEditorMenu("Editor actions", in: app)
        XCTAssertTrue(app.buttons["Rename Page"].waitForExistence(timeout: 5))
        app.buttons["Rename Page"].tap()
        let pageTitleField = app.textFields["Page title"]
        XCTAssertTrue(pageTitleField.waitForExistence(timeout: 5))
        pageTitleField.tap()
        pageTitleField.typeText("Introduction")
        app.buttons["Save"].tap()
        XCTAssertTrue(app.staticTexts["Introduction"].waitForExistence(timeout: 5))

        app.buttons["Library"].tap()
        let searchField = app.searchFields.firstMatch
        XCTAssertTrue(searchField.waitForExistence(timeout: 10))
        searchField.tap()
        searchField.typeText("Introduction")

        let notebook = app.buttons[title]
        XCTAssertTrue(notebook.waitForExistence(timeout: 10))
        notebook.tap()
        XCTAssertTrue(
            app.descendants(matching: .any)["Writing canvas for Introduction"]
                .waitForExistence(timeout: 10)
        )
    }

    @MainActor
    func testFavoriteNotebookAppearsInFavoritesLibraryView() throws {
        continueAfterFailure = false
        let app = launchApp(runID: UUID().uuidString)
        app.buttons["Continue Offline"].tap()
        app.buttons["New"].tap()
        app.buttons["New Notebook"].tap()

        let title = "Favorite UI Test \(UUID().uuidString.prefix(8))"
        let titleField = app.textFields["Title"]
        XCTAssertTrue(titleField.waitForExistence(timeout: 5))
        titleField.tap()
        titleField.typeText(title)
        app.buttons["Create"].tap()

        app.buttons["Library"].tap()
        let notebook = app.buttons[title]
        XCTAssertTrue(notebook.waitForExistence(timeout: 10))
        app.buttons["Actions for \(title)"].tap()
        XCTAssertTrue(app.buttons["Edit Tags"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.buttons["Move to Folder"].waitForExistence(timeout: 5))
        app.buttons["Edit Tags"].tap()
        let tagsField = app.textFields["Tags separated by commas"]
        XCTAssertTrue(tagsField.waitForExistence(timeout: 5))
        tagsField.tap()
        tagsField.typeText("math, exam")
        app.buttons["Save"].tap()
        XCTAssertTrue(notebook.waitForExistence(timeout: 5))
        app.buttons["Actions for \(title)"].tap()
        let addFavorite = app.buttons["Add to Favorites"]
        XCTAssertTrue(addFavorite.waitForExistence(timeout: 5))
        addFavorite.tap()

        app.buttons["library-view-picker"].tap()
        let favorites = app.buttons["Favorites"]
        XCTAssertTrue(favorites.waitForExistence(timeout: 5))
        favorites.tap()
        XCTAssertTrue(app.buttons[title].waitForExistence(timeout: 10))
    }
}
