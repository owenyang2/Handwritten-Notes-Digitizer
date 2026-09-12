import XCTest
@testable import PersonalizedNotesKit

final class LocalStoreTests: XCTestCase {
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
}
