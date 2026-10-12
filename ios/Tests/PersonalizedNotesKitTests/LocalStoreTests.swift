import XCTest
@testable import PersonalizedNotesKit
import SQLite3

final class LocalStoreTests: XCTestCase {
    func testWebsiteMetadataAndTrashChangesUpdateExistingLocalCopy() async throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent("rest-sync-\(UUID().uuidString)")
        defer { try? FileManager.default.removeItem(at: directory) }
        let store = try SQLiteLocalStore(databaseURL: directory.appendingPathComponent("notes.sqlite3"))
        let documentID = UUID()
        try await store.upsertDocuments([DocumentRecord(documentID: documentID, title: "Old", courseContext: "Old context")])
        let payload: JSONDocument = [
            "title": .string("Website title"), "archived": .bool(true), "is_favorite": .bool(true),
            "default_language": .string("fr"), "course_context": .null,
        ]
        try await store.applyRemoteChange(SyncChange(
            serverSequence: 1, operationID: UUID(), documentID: documentID, clientID: "server:documents",
            clientOperationID: UUID(), baseSyncVersion: 0, syncVersion: 1, entityType: "document",
            entityID: documentID.uuidString, operationType: "document.metadata", payload: payload,
            createdAt: Date(), isTombstone: false
        ))
        let updated = try await store.document(id: documentID)
        XCTAssertEqual(updated?.title, "Website title")
        XCTAssertNotNil(updated?.archivedAt)
        XCTAssertEqual(updated?.isFavorite, true)
        XCTAssertEqual(updated?.defaultLanguage, "fr")
        XCTAssertNil(updated?.courseContext)
        for (index, operation) in ["document.trash", "document.restore"].enumerated() {
            try await store.applyRemoteChange(SyncChange(
                serverSequence: index + 2, operationID: UUID(), documentID: documentID, clientID: "server:documents",
                clientOperationID: UUID(), baseSyncVersion: index + 1, syncVersion: index + 2, entityType: "document",
                entityID: documentID.uuidString, operationType: operation, payload: [:], createdAt: Date(), isTombstone: false
            ))
            let document = try await store.document(id: documentID)
            XCTAssertEqual(document?.trashedAt != nil, index == 0)
        }
        let next = try await store.enqueueDocumentMutation(
            documentID: documentID, clientID: "ipad", operationType: "document.rename",
            payload: ["title": .string("Next title")], entityID: nil
        )
        XCTAssertEqual(next.baseSyncVersion, 3)
    }

    func testSchemaMigrationBackfillsNormalizedPagesAndUsesThemAfterReopen() async throws {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("legacy-local-store-\(UUID().uuidString)", isDirectory: true)
        let databaseURL = directory.appendingPathComponent("notes.sqlite3")
        let documentID = UUID()
        let pages = [
            PageSummary(
                pageID: UUID(),
                pageIndex: 0,
                title: "Lecture",
                width: 768,
                height: 1024,
                dpi: 132,
                coordinateSpace: "points",
                paperTemplate: "ruled"
            ),
            PageSummary(
                pageID: UUID(),
                pageIndex: 1,
                title: "Exercises",
                width: 1024,
                height: 768,
                dpi: 132,
                coordinateSpace: "points",
                paperTemplate: "grid"
            ),
        ]
        try makeLegacyDatabase(at: databaseURL, documentID: documentID, pages: pages)

        let store = try SQLiteLocalStore(databaseURL: databaseURL)
        let migratedSchemaVersion = try await store.schemaVersion()
        XCTAssertEqual(migratedSchemaVersion, SQLiteLocalStore.currentSchemaVersion)
        let migratedDocument = try await store.document(id: documentID)
        XCTAssertEqual(migratedDocument?.pages, pages)
        XCTAssertEqual(try normalizedPageCount(at: databaseURL, documentID: documentID), 2)

        let reopened = try SQLiteLocalStore(databaseURL: databaseURL)
        let reopenedDocument = try await reopened.document(id: documentID)
        XCTAssertEqual(reopenedDocument?.pages, pages)
        XCTAssertEqual(try normalizedPageCount(at: databaseURL, documentID: documentID), 2)
    }

    func testRevisionAndTransferQueueSurviveReopenAndRetry() async throws {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("revision-queue-store-\(UUID().uuidString)", isDirectory: true)
        let databaseURL = directory.appendingPathComponent("notes.sqlite3")
        let documentID = UUID()
        let pageID = UUID()
        let revisionID = UUID()
        let store = try SQLiteLocalStore(databaseURL: databaseURL)
        try await store.upsertDocuments([
            DocumentRecord(
                documentID: documentID,
                title: "Durable revisions",
                pages: [PageSummary(
                    pageID: pageID,
                    pageIndex: 0,
                    width: 768,
                    height: 1024,
                    dpi: 132,
                    coordinateSpace: "points"
                )]
            )
        ])

        let revision = LocalPageRevisionRecord(
            revisionID: revisionID,
            documentID: documentID,
            pageID: pageID,
            pkDrawingPath: directory.appendingPathComponent("drawing.pkdrawing").path,
            pkDrawingSHA256: "drawing-hash",
            inkBundlePath: directory.appendingPathComponent("ink.json").path,
            inkBundleSHA256: "bundle-hash"
        )
        try await store.recordPageRevision(revision)
        let containingDocumentID = try await store.documentID(containingPage: pageID)
        XCTAssertEqual(containingDocumentID, documentID)
        let initialRevisions = try await store.pageRevisions(pageID: pageID)
        XCTAssertEqual(initialRevisions.first?.localSequence, 1)

        let transfer = LocalTransferJob(
            transferID: revisionID,
            documentID: documentID,
            pageID: pageID,
            revisionID: revisionID,
            kind: "ink",
            sourcePath: revision.inkBundlePath
        )
        try await store.enqueueTransfer(transfer)
        try await store.updateTransfer(
            transferID: revisionID,
            state: .running,
            error: nil,
            nextAttemptAt: nil,
            incrementAttempt: true
        )
        try await store.updateTransfer(
            transferID: revisionID,
            state: .retryableFailed,
            error: "offline",
            nextAttemptAt: Date(timeIntervalSince1970: 1_700_000_100),
            incrementAttempt: false
        )
        // Re-enqueueing the same stable transfer ID must not erase retry state.
        try await store.enqueueTransfer(transfer)

        let reopened = try SQLiteLocalStore(databaseURL: databaseURL)
        let retryable = try await reopened.transferJobs(states: [.retryableFailed], limit: 10)
        XCTAssertEqual(retryable.count, 1)
        XCTAssertEqual(retryable.first?.transferID, revisionID)
        XCTAssertEqual(retryable.first?.attemptCount, 1)
        XCTAssertEqual(retryable.first?.lastError, "offline")

        let uploadedAt = Date(timeIntervalSince1970: 1_700_000_200)
        try await reopened.markPageRevisionUploaded(revisionID: revisionID, at: uploadedAt)
        let persistedRevision = try await reopened.pageRevisions(pageID: pageID).first
        XCTAssertEqual(persistedRevision?.uploadState, .complete)
        XCTAssertEqual(persistedRevision?.uploadedAt, uploadedAt)
    }

    func testPendingOperationsSurviveReopen() async throws {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("local-store-\(UUID().uuidString)", isDirectory: true)
        let databaseURL = directory.appendingPathComponent("notes.sqlite3")
        let documentID = UUID(uuidString: "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee")!
        let clientID = "crash-recovery-client"

        do {
            let store = try SQLiteLocalStore(databaseURL: databaseURL)
            try await store.upsertDocuments([
                DocumentRecord(documentID: documentID, title: "Before crash"),
            ])
            let operation = try await store.enqueueDocumentMutation(
                documentID: documentID,
                clientID: clientID,
                operationType: "document.rename",
                payload: ["title": .string("Survived reopen")],
                entityID: nil
            )
            XCTAssertEqual(operation.state, .pending)
            let pending = try await store.pendingOperations(limit: 10)
            XCTAssertEqual(pending.count, 1)
            XCTAssertEqual(pending.first?.clientOperationID, operation.clientOperationID)
        }

        let reopened = try SQLiteLocalStore(databaseURL: databaseURL)
        let pending = try await reopened.pendingOperations(limit: 10)
        XCTAssertEqual(pending.count, 1, "Pending operations must survive process restart")
        XCTAssertEqual(pending.first?.documentID, documentID)
        XCTAssertEqual(pending.first?.operationType, "document.rename")
        XCTAssertEqual(pending.first?.clientID, clientID)
        XCTAssertEqual(pending.first?.state, .pending)

        let document = try await reopened.document(id: documentID)
        XCTAssertEqual(document?.title, "Survived reopen")
    }

    func testArchiveMutationRemainsPendingAfterReopen() async throws {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("local-store-\(UUID().uuidString)", isDirectory: true)
        let databaseURL = directory.appendingPathComponent("notes.sqlite3")
        let documentID = UUID()

        let store = try SQLiteLocalStore(databaseURL: databaseURL)
        try await store.upsertDocuments([
            DocumentRecord(documentID: documentID, title: "Archive me"),
        ])
        _ = try await store.enqueueDocumentMutation(
            documentID: documentID,
            clientID: "client",
            operationType: "document.archive",
            payload: ["archived": .bool(true)],
            entityID: nil
        )
        let first = try await store.document(id: documentID)
        XCTAssertNotNil(first?.archivedAt)

        let reopened = try SQLiteLocalStore(databaseURL: databaseURL)
        let pending = try await reopened.pendingOperations(limit: 10)
        XCTAssertEqual(pending.count, 1)
        XCTAssertEqual(pending.first?.operationType, "document.archive")
        let reopenedDocument = try await reopened.document(id: documentID)
        XCTAssertNotNil(reopenedDocument?.archivedAt)
    }

    func testFavoriteMutationProjectsAndFiltersTheLibrary() async throws {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("favorite-store-\(UUID().uuidString)", isDirectory: true)
        let store = try SQLiteLocalStore(databaseURL: directory.appendingPathComponent("notes.sqlite3"))
        let documentID = UUID()
        try await store.upsertDocuments([
            DocumentRecord(documentID: documentID, title: "Favorite me")
        ])

        _ = try await store.enqueueDocumentMutation(
            documentID: documentID,
            clientID: "client",
            operationType: "document.favorite",
            payload: ["favorite": .bool(true)],
            entityID: nil
        )

        let document = try await store.document(id: documentID)
        XCTAssertEqual(document?.isFavorite, true)
        let favorites = try await store.listDocuments(DocumentListQuery(view: .favorites))
        XCTAssertEqual(favorites.map(\.documentID), [documentID])
    }

    func testOrganizationMetadataMutationProjectsAndSurvivesReopen() async throws {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("organization-store-\(UUID().uuidString)", isDirectory: true)
        let databaseURL = directory.appendingPathComponent("notes.sqlite3")
        let documentID = UUID()
        let store = try SQLiteLocalStore(databaseURL: databaseURL)
        try await store.upsertDocuments([
            DocumentRecord(documentID: documentID, title: "Organize me")
        ])

        _ = try await store.enqueueDocumentMutation(
            documentID: documentID,
            clientID: "client",
            operationType: "document.tags",
            payload: [
                "tags": .array([.string("math"), .string("exam")]),
            ],
            entityID: nil
        )
        _ = try await store.enqueueDocumentMutation(
            documentID: documentID,
            clientID: "client",
            operationType: "document.move",
            payload: ["folder_id": .string("semester-1")],
            entityID: nil
        )

        let projected = try await store.document(id: documentID)
        XCTAssertEqual(projected?.organizationTags, ["math", "exam"])
        XCTAssertEqual(projected?.organizationFolderID, "semester-1")

        let reopened = try SQLiteLocalStore(databaseURL: databaseURL)
        let reopenedDocument = try await reopened.document(id: documentID)
        XCTAssertEqual(reopenedDocument?.organizationTags, ["math", "exam"])
        XCTAssertEqual(reopenedDocument?.organizationFolderID, "semester-1")
    }

    func testSmartLibraryViewsAndMetadataSearch() async throws {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("smart-views-\(UUID().uuidString)", isDirectory: true)
        let store = try SQLiteLocalStore(databaseURL: directory.appendingPathComponent("notes.sqlite3"))
        let nativeID = UUID()
        let importedID = UUID()
        try await store.upsertDocuments([
            DocumentRecord(
                documentID: nativeID,
                title: "Algebra",
                sourceKind: "native_ink",
                courseContext: "Linear algebra",
                sourceMetadata: ["tags": .array([.string("exam")])]
            ),
            DocumentRecord(
                documentID: importedID,
                title: "Scanned worksheet",
                sourceKind: "image_import"
            ),
        ])

        let pending = try await store.enqueueDocumentMutation(
            documentID: nativeID,
            clientID: "client",
            operationType: "document.rename",
            payload: ["title": .string("Algebra review")],
            entityID: nil
        )
        let unsynced = try await store.listDocuments(DocumentListQuery(view: .unsynced))
        let recent = try await store.listDocuments(DocumentListQuery(view: .recent))
        let imported = try await store.listDocuments(DocumentListQuery(view: .imported))
        let metadataMatches = try await store.listDocuments(DocumentListQuery(query: "exam"))
        let courseMatches = try await store.listDocuments(DocumentListQuery(query: "linear algebra"))
        XCTAssertEqual(unsynced.map(\.documentID), [nativeID])
        XCTAssertEqual(recent.count, 2)
        XCTAssertEqual(imported.map(\.documentID), [importedID])
        XCTAssertEqual(metadataMatches.map(\.documentID), [nativeID])
        XCTAssertEqual(courseMatches.map(\.documentID), [nativeID])

        try await store.markOperationFailed(clientOperationID: pending.clientOperationID, error: "offline")
        let needsAttention = try await store.listDocuments(DocumentListQuery(view: .needsAttention))
        XCTAssertEqual(needsAttention.map(\.documentID), [nativeID])
    }

    func testPageTitleSearchFindsNotebook() async throws {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("page-title-search-\(UUID().uuidString)", isDirectory: true)
        let store = try SQLiteLocalStore(databaseURL: directory.appendingPathComponent("notes.sqlite3"))
        let documentID = UUID()
        let page = PageSummary(
            pageID: UUID(),
            pageIndex: 0,
            title: "Lecture One",
            width: 768,
            height: 1024,
            dpi: 132,
            coordinateSpace: "points"
        )
        let diacriticPage = PageSummary(
            pageID: UUID(),
            pageIndex: 1,
            title: "Résumé",
            width: 768,
            height: 1024,
            dpi: 132,
            coordinateSpace: "points"
        )
        try await store.upsertDocuments([
            DocumentRecord(
                documentID: documentID,
                title: "Biology Notes",
                sourceKind: "native_ink",
                pages: [page, diacriticPage]
            )
        ])

        let matches = try await store.listDocuments(DocumentListQuery(query: "Lecture One"))
        let diacriticMatches = try await store.listDocuments(DocumentListQuery(query: "resume"))

        XCTAssertEqual(matches.map(\.documentID), [documentID])
        XCTAssertEqual(matches.first?.pages.first?.title, "Lecture One")
        XCTAssertEqual(diacriticMatches.map(\.documentID), [documentID])
    }

    func testFailedOperationIsVisibleAndRetryable() async throws {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("failed-operation-\(UUID().uuidString)", isDirectory: true)
        let store = try SQLiteLocalStore(databaseURL: directory.appendingPathComponent("notes.sqlite3"))
        let documentID = UUID()
        try await store.upsertDocuments([
            DocumentRecord(documentID: documentID, title: "Retry me")
        ])
        let operation = try await store.enqueueDocumentMutation(
            documentID: documentID,
            clientID: "client",
            operationType: "document.rename",
            payload: ["title": .string("Retry me later")],
            entityID: nil
        )

        try await store.markOperationFailed(
            clientOperationID: operation.clientOperationID,
            error: "offline"
        )
        let pendingAfterFailure = try await store.pendingOperations(limit: 10)
        XCTAssertEqual(pendingAfterFailure.count, 0)
        let failed = try await store.failedOperations(limit: 10)
        XCTAssertEqual(failed.count, 1)
        XCTAssertEqual(failed.first?.lastError, "offline")

        try await store.retryOperation(clientOperationID: operation.clientOperationID)
        let pendingAfterRetry = try await store.pendingOperations(limit: 10)
        let failedAfterRetry = try await store.failedOperations(limit: 10)
        XCTAssertEqual(pendingAfterRetry.count, 1)
        XCTAssertTrue(failedAfterRetry.isEmpty)
    }

    func testPageMutationsProjectInOrderAndSurviveReopen() async throws {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("page-store-\(UUID().uuidString)", isDirectory: true)
        let databaseURL = directory.appendingPathComponent("notes.sqlite3")
        let documentID = UUID()
        let firstPage = PageSummary(
            pageID: UUID(), pageIndex: 0, width: 768, height: 1024, dpi: 132, coordinateSpace: "points"
        )
        let secondPage = PageSummary(
            pageID: UUID(), pageIndex: 1, width: 768, height: 1024, dpi: 132, coordinateSpace: "points"
        )
        let thirdPage = PageSummary(
            pageID: UUID(), pageIndex: 2, width: 768, height: 1024, dpi: 132, coordinateSpace: "points"
        )

        let store = try SQLiteLocalStore(databaseURL: databaseURL)
        try await store.upsertDocuments([
            DocumentRecord(documentID: documentID, title: "Pages", pages: [firstPage, secondPage])
        ])

        _ = try await store.enqueueDocumentMutation(
            documentID: documentID,
            clientID: "client",
            operationType: "page.add",
            payload: [
                "page": .object([
                    "page_id": .string(thirdPage.pageID.uuidString),
                    "page_index": .number(2),
                    "title": .string("Summary"),
                    "width": .number(Double(thirdPage.width)),
                    "height": .number(Double(thirdPage.height)),
                    "dpi": .number(Double(thirdPage.dpi)),
                    "coordinate_space": .string(thirdPage.coordinateSpace),
                ]),
                "page_index": .number(2),
            ],
            entityID: thirdPage.pageID.uuidString
        )
        _ = try await store.enqueueDocumentMutation(
            documentID: documentID,
            clientID: "client",
            operationType: "page.reorder",
            payload: [
                "page_id": .string(thirdPage.pageID.uuidString),
                "page_index": .number(0),
            ],
            entityID: thirdPage.pageID.uuidString
        )
        _ = try await store.enqueueDocumentMutation(
            documentID: documentID,
            clientID: "client",
            operationType: "page.delete",
            payload: ["page_id": .string(firstPage.pageID.uuidString)],
            entityID: firstPage.pageID.uuidString
        )

        let projected = try await store.document(id: documentID)
        XCTAssertEqual(projected?.pages.map(\.pageID), [thirdPage.pageID, secondPage.pageID])
        XCTAssertEqual(projected?.pages.map(\.pageIndex), [0, 1])
        XCTAssertEqual(projected?.pages.first?.title, "Summary")
        let pending = try await store.pendingOperations(limit: 10)
        XCTAssertEqual(pending.count, 3)

        let reopened = try SQLiteLocalStore(databaseURL: databaseURL)
        let reopenedDocument = try await reopened.document(id: documentID)
        XCTAssertEqual(reopenedDocument?.pages.map(\.pageID), [thirdPage.pageID, secondPage.pageID])
    }

    func testPageTemplateMutationProjectsAndSurvivesReopen() async throws {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("page-template-store-\(UUID().uuidString)", isDirectory: true)
        let databaseURL = directory.appendingPathComponent("notes.sqlite3")
        let documentID = UUID()
        let pageID = UUID()
        let store = try SQLiteLocalStore(databaseURL: databaseURL)
        try await store.upsertDocuments([
            DocumentRecord(
                documentID: documentID,
                title: "Templates",
                pages: [PageSummary(
                    pageID: pageID,
                    pageIndex: 0,
                    width: 768,
                    height: 1024,
                    dpi: 132,
                    coordinateSpace: "points"
                )]
            )
        ])

        _ = try await store.enqueueDocumentMutation(
            documentID: documentID,
            clientID: "client",
            operationType: "page.template",
            payload: [
                "page_id": .string(pageID.uuidString),
                "paper_template": .string("cornell"),
            ],
            entityID: pageID.uuidString
        )

        let projected = try await store.document(id: documentID)
        XCTAssertEqual(projected?.pages.first?.paperTemplate, "cornell")

        _ = try await store.enqueueDocumentMutation(
            documentID: documentID,
            clientID: "client",
            operationType: "page.rename",
            payload: [
                "page_id": .string(pageID.uuidString),
                "title": .string("Lecture notes"),
            ],
            entityID: pageID.uuidString
        )
        let renamed = try await store.document(id: documentID)
        XCTAssertEqual(renamed?.pages.first?.title, "Lecture notes")

        _ = try await store.enqueueDocumentMutation(
            documentID: documentID,
            clientID: "client",
            operationType: "page.resize",
            payload: [
                "page_id": .string(pageID.uuidString),
                "width": .number(595),
                "height": .number(842),
            ],
            entityID: pageID.uuidString
        )
        let resized = try await store.document(id: documentID)
        XCTAssertEqual(resized?.pages.first?.width, 595)
        XCTAssertEqual(resized?.pages.first?.height, 842)

        let reopened = try SQLiteLocalStore(databaseURL: databaseURL)
        let reopenedDocument = try await reopened.document(id: documentID)
        XCTAssertEqual(reopenedDocument?.pages.first?.paperTemplate, "cornell")
        XCTAssertEqual(reopenedDocument?.pages.first?.width, 595)
        XCTAssertEqual(reopenedDocument?.pages.first?.title, "Lecture notes")
    }

    func testAcknowledgedPageDeleteQueuesInkCleanupAfterProjectionRemoval() async throws {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("page-delete-purge-\(UUID().uuidString)", isDirectory: true)
        let store = try SQLiteLocalStore(databaseURL: directory.appendingPathComponent("notes.sqlite3"))
        let documentID = UUID()
        let retainedPageID = UUID()
        let deletedPageID = UUID()
        try await store.upsertDocuments([
            DocumentRecord(
                documentID: documentID,
                title: "Page cleanup",
                pages: [
                    PageSummary(
                        pageID: retainedPageID,
                        pageIndex: 0,
                        width: 768,
                        height: 1024,
                        dpi: 132,
                        coordinateSpace: "points"
                    ),
                    PageSummary(
                        pageID: deletedPageID,
                        pageIndex: 1,
                        width: 768,
                        height: 1024,
                        dpi: 132,
                        coordinateSpace: "points"
                    ),
                ]
            )
        ])
        let revisionID = UUID()
        try await store.recordPageRevision(LocalPageRevisionRecord(
            revisionID: revisionID,
            documentID: documentID,
            pageID: deletedPageID,
            pkDrawingPath: directory.appendingPathComponent("deleted.pkdrawing").path,
            pkDrawingSHA256: "drawing",
            inkBundlePath: directory.appendingPathComponent("deleted.ink.json").path,
            inkBundleSHA256: "bundle"
        ))
        try await store.enqueueTransfer(LocalTransferJob(
            transferID: revisionID,
            documentID: documentID,
            pageID: deletedPageID,
            revisionID: revisionID,
            kind: "ink"
        ))

        let operation = try await store.enqueueDocumentMutation(
            documentID: documentID,
            clientID: "client",
            operationType: "page.delete",
            payload: ["page_id": .string(deletedPageID.uuidString)],
            entityID: deletedPageID.uuidString
        )
        let projected = try await store.document(id: documentID)
        XCTAssertEqual(projected?.pages.map(\.pageID), [retainedPageID])

        try await store.markOperationAccepted(
            clientOperationID: operation.clientOperationID,
            documentID: documentID,
            syncVersion: 1
        )
        let records = try await store.pendingPagePurgeRecords()
        XCTAssertEqual(records.count, 1)
        XCTAssertEqual(records.first?.pageID, deletedPageID)
        XCTAssertEqual(records.first?.documentID, documentID)

        try await store.acknowledgePagePurge(pageID: deletedPageID)
        let recordsAfterAcknowledgement = try await store.pendingPagePurgeRecords()
        XCTAssertTrue(recordsAfterAcknowledgement.isEmpty)
        let deletedPageRevisions = try await store.pageRevisions(pageID: deletedPageID)
        let remainingTransfers = try await store.transferJobs(states: nil, limit: 10)
        XCTAssertTrue(deletedPageRevisions.isEmpty)
        XCTAssertTrue(remainingTransfers.isEmpty)
    }

    func testPermanentDeleteRemovesLocalDocumentOnlyAfterAcknowledgement() async throws {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("permanent-delete-store-\(UUID().uuidString)", isDirectory: true)
        let databaseURL = directory.appendingPathComponent("notes.sqlite3")
        let documentID = UUID()
        let pageID = UUID()
        let sourcePath = directory.appendingPathComponent("import.pdf").path
        let store = try SQLiteLocalStore(databaseURL: databaseURL)
        try await store.upsertDocuments([
            DocumentRecord(
                documentID: documentID,
                title: "Trash me",
                pages: [
                    PageSummary(
                        pageID: pageID,
                        pageIndex: 0,
                        width: 768,
                        height: 1024,
                        dpi: 132,
                        coordinateSpace: "points"
                    ),
                ],
                sourceMetadata: ["durable_file_path": .string(sourcePath)]
            )
        ])

        let trash = try await store.enqueueDocumentMutation(
            documentID: documentID,
            clientID: "client",
            operationType: "document.trash",
            payload: [:],
            entityID: nil
        )
        let delete = try await store.enqueueDocumentMutation(
            documentID: documentID,
            clientID: "client",
            operationType: "document.delete",
            payload: [:],
            entityID: nil
        )
        let beforeAcknowledgement = try await store.document(id: documentID)
        XCTAssertNotNil(beforeAcknowledgement)

        try await store.markOperationAccepted(
            clientOperationID: trash.clientOperationID,
            documentID: documentID,
            syncVersion: 1
        )
        let afterTrashAcknowledgement = try await store.document(id: documentID)
        XCTAssertNotNil(afterTrashAcknowledgement)

        try await store.markOperationAccepted(
            clientOperationID: delete.clientOperationID,
            documentID: documentID,
            syncVersion: 2
        )
        let afterDeleteAcknowledgement = try await store.document(id: documentID)
        XCTAssertNil(afterDeleteAcknowledgement)
        let pendingAfterDelete = try await store.pendingOperations(limit: 10)
        XCTAssertTrue(pendingAfterDelete.isEmpty)
        let purgeRecords = try await store.pendingPurgeRecords()
        XCTAssertEqual(purgeRecords.count, 1)
        XCTAssertEqual(purgeRecords.first?.documentID, documentID)
        XCTAssertEqual(purgeRecords.first?.pageIDs, [pageID])
        XCTAssertEqual(purgeRecords.first?.sourcePath, sourcePath)

        try await store.acknowledgePurge(documentID: documentID)
        let purgesAfterAcknowledgement = try await store.pendingPurgeRecords()
        XCTAssertTrue(purgesAfterAcknowledgement.isEmpty)
    }

    func testRemoteDocumentCreateBootstrapsLocalProjection() async throws {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("remote-create-\(UUID().uuidString)", isDirectory: true)
        let store = try SQLiteLocalStore(databaseURL: directory.appendingPathComponent("notes.sqlite3"))
        let documentID = UUID()
        let pageID = UUID()
        let now = Date(timeIntervalSince1970: 1_700_000_000)
        let change = SyncChange(
            serverSequence: 1,
            operationID: UUID(),
            documentID: documentID,
            clientID: "other-device",
            clientOperationID: UUID(),
            baseSyncVersion: 0,
            syncVersion: 1,
            entityType: "document",
            entityID: documentID.uuidString,
            operationType: "document.create",
            payload: [
                "title": .string("Remote note"),
                "source_kind": .string("native_ink"),
                "default_language": .string("en"),
                "pages": .array([
                    .object([
                        "page_id": .string(pageID.uuidString),
                        "page_index": .number(0),
                        "width": .number(768),
                        "height": .number(1024),
                        "dpi": .number(132),
                        "coordinate_space": .string("points"),
                    ])
                ]),
            ],
            createdAt: now,
            isTombstone: false
        )

        try await store.applyRemoteChange(change)
        let document = try await store.document(id: documentID)
        XCTAssertEqual(document?.title, "Remote note")
        XCTAssertEqual(document?.syncVersion, 1)
        XCTAssertEqual(document?.pages.map(\.pageID), [pageID])
    }

    private func makeLegacyDatabase(
        at url: URL,
        documentID: UUID,
        pages: [PageSummary]
    ) throws {
        try FileManager.default.createDirectory(
            at: url.deletingLastPathComponent(),
            withIntermediateDirectories: true
        )
        var handle: OpaquePointer?
        guard sqlite3_open_v2(
            url.path,
            &handle,
            SQLITE_OPEN_READWRITE | SQLITE_OPEN_CREATE | SQLITE_OPEN_FULLMUTEX,
            nil
        ) == SQLITE_OK, let handle else {
            throw LocalStoreError.database("Unable to create legacy SQLite test database")
        }
        defer { sqlite3_close(handle) }

        let pageData = try APIJSON.encoder().encode(pages)
        let imageData = try APIJSON.encoder().encode([UUID]())
        let pageHex = pageData.map { String(format: "%02x", $0) }.joined()
        let imageHex = imageData.map { String(format: "%02x", $0) }.joined()
        let sql = """
            CREATE TABLE local_documents (
                document_id TEXT PRIMARY KEY NOT NULL,
                title TEXT NOT NULL,
                created_at REAL NOT NULL,
                image_ids BLOB NOT NULL,
                source_kind TEXT NOT NULL,
                pages BLOB NOT NULL,
                active_revision_id TEXT,
                updated_at REAL NOT NULL,
                archived_at REAL,
                trashed_at REAL,
                sync_version INTEGER NOT NULL DEFAULT 0,
                default_language TEXT NOT NULL,
                course_context TEXT,
                source_metadata BLOB
            );
            INSERT INTO local_documents(
                document_id, title, created_at, image_ids, source_kind, pages,
                active_revision_id, updated_at, archived_at, trashed_at,
                sync_version, default_language, course_context, source_metadata
            ) VALUES (
                '\(documentID.uuidString)', 'Legacy notebook', 1700000000,
                X'\(imageHex)', 'native_ink', X'\(pageHex)', NULL, 1700000000,
                NULL, NULL, 0, 'en', NULL, NULL
            );
            PRAGMA user_version = 1;
            """
        var errorMessage: UnsafeMutablePointer<CChar>?
        let status = sqlite3_exec(handle, sql, nil, nil, &errorMessage)
        guard status == SQLITE_OK else {
            let message = errorMessage.map { String(cString: $0) } ?? "unknown SQLite error"
            sqlite3_free(errorMessage)
            throw LocalStoreError.database(message)
        }
    }

    private func normalizedPageCount(at url: URL, documentID: UUID) throws -> Int {
        var handle: OpaquePointer?
        guard sqlite3_open_v2(url.path, &handle, SQLITE_OPEN_READONLY, nil) == SQLITE_OK,
              let handle else {
            throw LocalStoreError.database("Unable to reopen SQLite test database")
        }
        defer { sqlite3_close(handle) }

        var statement: OpaquePointer?
        guard sqlite3_prepare_v2(
            handle,
            "SELECT COUNT(*) FROM local_pages WHERE document_id = ?",
            -1,
            &statement,
            nil
        ) == SQLITE_OK, let statement else {
            throw LocalStoreError.database("Unable to prepare normalized page count")
        }
        defer { sqlite3_finalize(statement) }
        _ = documentID.uuidString.withCString { pointer in
            sqlite3_bind_text(
                statement,
                1,
                pointer,
                -1,
                unsafeBitCast(-1, to: sqlite3_destructor_type.self)
            )
        }
        guard sqlite3_step(statement) == SQLITE_ROW else {
            throw LocalStoreError.database("Unable to read normalized page count")
        }
        return Int(sqlite3_column_int64(statement, 0))
    }
}
