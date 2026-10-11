import XCTest

final class PersonalizedNotesUITests: XCTestCase {
    @MainActor
    private func launchApp(runID: String) -> XCUIApplication {
        let app = XCUIApplication()
        app.launchArguments += ["--ui-testing"]
        app.launchEnvironment["HND_UI_TEST_RUN_ID"] = runID
        app.launch()
        XCTAssertTrue(app.wait(for: .runningForeground, timeout: 15))
        return app
    }

    @MainActor
    func testWelcomeAndOfflineNotebookCreationPersistAfterRelaunch() throws {
        continueAfterFailure = false
        let runID = UUID().uuidString
        let app = launchApp(runID: runID)
        let continueOffline = app.buttons["Continue Offline"]
        XCTAssertTrue(continueOffline.waitForExistence(timeout: 10))
        continueOffline.tap()

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
        app.buttons["Tools"].tap()
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
        XCTAssertTrue(app.buttons["Toggle Ruler"].waitForExistence(timeout: 5))
        app.buttons["Eraser"].firstMatch.tap()

        app.buttons["Editor actions"].tap()
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

        app.buttons["Editor actions"].tap()
        XCTAssertTrue(app.buttons["Paper Template"].waitForExistence(timeout: 5))
        app.buttons["Paper Template"].tap()
        XCTAssertTrue(app.buttons["Ruled"].waitForExistence(timeout: 5))
        app.buttons["Ruled"].tap()
        app.buttons["Editor actions"].tap()
        XCTAssertTrue(app.buttons["Add Page"].waitForExistence(timeout: 5))
        app.buttons["Add Page"].tap()
        XCTAssertTrue(app.staticTexts["Page 2"].waitForExistence(timeout: 10))

        app.buttons["Editor actions"].tap()
        XCTAssertTrue(app.buttons["Duplicate Page"].waitForExistence(timeout: 5))
        app.buttons["Duplicate Page"].tap()
        XCTAssertTrue(app.staticTexts["Page 3"].waitForExistence(timeout: 10))

        app.buttons["Editor actions"].tap()
        XCTAssertTrue(app.buttons["Move Page Up"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.buttons["Move Page Down"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.buttons["Delete Page"].waitForExistence(timeout: 5))
        app.buttons["Delete Page"].tap()
        XCTAssertTrue(app.buttons["Delete"].waitForExistence(timeout: 5))
        app.buttons["Delete"].tap()
        XCTAssertFalse(app.staticTexts["Page 3"].waitForExistence(timeout: 3))
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
        app.buttons["Editor actions"].tap()
        XCTAssertTrue(app.buttons["Export Page as PDF"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.buttons["Export Notebook as PDF"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.buttons["Export Page as PNG"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.buttons["Export Page as JPEG"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.buttons["Export Page as ink_v1"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.buttons["Export Native Notebook Archive"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.buttons["Save Notebook PDF to Files"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.buttons["Print Notebook"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.buttons["Save Native Archive to Files"].waitForExistence(timeout: 5))

        app.buttons["Export Page as PDF"].tap()
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
        app.buttons["Editor actions"].tap()
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
        notebook.press(forDuration: 1.0)
        XCTAssertTrue(app.buttons["Edit Tags"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.buttons["Move to Folder"].waitForExistence(timeout: 5))
        app.buttons["Edit Tags"].tap()
        let tagsField = app.textFields["Tags separated by commas"]
        XCTAssertTrue(tagsField.waitForExistence(timeout: 5))
        tagsField.tap()
        tagsField.typeText("math, exam")
        app.buttons["Save"].tap()
        XCTAssertTrue(notebook.waitForExistence(timeout: 5))
        notebook.press(forDuration: 1.0)
        let addFavorite = app.buttons["Add to Favorites"]
        XCTAssertTrue(addFavorite.waitForExistence(timeout: 5))
        addFavorite.tap()

        let favorites = app.buttons["Favorites"]
        XCTAssertTrue(favorites.waitForExistence(timeout: 5))
        favorites.tap()
        XCTAssertTrue(app.buttons[title].waitForExistence(timeout: 10))
    }
}
