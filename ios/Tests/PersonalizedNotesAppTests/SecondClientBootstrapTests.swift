import XCTest

@testable import PersonalizedNotes
import PersonalizedNotesKit

#if canImport(PencilKit)
import PencilKit
#endif

final class SecondClientBootstrapTests: XCTestCase {
    func testReconnectBootstrapsStableAggregateAndExactInkRevision() async throws {
        let firstDirectory = FileManager.default.temporaryDirectory
            .appendingPathComponent("first-client-\(UUID().uuidString)", isDirectory: true)
        let secondDirectory = FileManager.default.temporaryDirectory
            .appendingPathComponent("second-client-\(UUID().uuidString)", isDirectory: true)
        defer {
            try? FileManager.default.removeItem(at: firstDirectory)
            try? FileManager.default.removeItem(at: secondDirectory)
        }
        let firstStore = try SQLiteLocalStore(
            databaseURL: firstDirectory.appendingPathComponent("notes.sqlite3")
        )
        let secondStore = try SQLiteLocalStore(
            databaseURL: secondDirectory.appendingPathComponent("notes.sqlite3")
        )
        let documentID = UUID()
        let pageID = UUID()
        let page = PageSummary(
            pageID: pageID,
            pageIndex: 0,
            width: 768,
            height: 1024,
            dpi: 132,
            coordinateSpace: "points"
        )
        let document = DocumentRecord(
            documentID: documentID,
            title: "Cross-client notes",
            sourceKind: "native_ink",
            pages: [page]
        )
        try await firstStore.upsertDocuments([document])

        let service = TwoClientSyncService()
        let firstEngine = SyncEngine(
            localStore: firstStore,
            service: service,
            clientID: "ipad-first"
        )
        _ = try await firstEngine.enqueueDocumentMutation(
            documentID: documentID,
            operationType: "document.create",
            payload: [
                "title": .string(document.title),
                "source_kind": .string(document.sourceKind),
                "default_language": .string(document.defaultLanguage),
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
            entityID: documentID.uuidString
        )
        _ = try await firstEngine.syncNow()

        #if canImport(PencilKit)
        let remoteNativeData = PKDrawing().dataRepresentation()
        #else
        let remoteNativeData = Data("exact-native-revision".utf8)
        #endif
        let remoteBundle = InkBundle(
            pageID: pageID,
            coordinateSpace: InkCoordinateSpace(width: 768, height: 1024),
            strokes: [
                InkStroke(
                    points: [
                        InkPoint(x: 10, y: 20, timeOffset: 0, pressure: 0.5),
                        InkPoint(x: 40, y: 60, timeOffset: 0.1, pressure: 0.8),
                    ]
                )
            ],
            nativeDrawing: NativeDrawing(data: remoteNativeData)
        )
        let serverRevision = InkBundleRevision(
            bundleID: UUID(),
            pageID: pageID,
            contentHash: remoteBundle.contentHash,
            schemaName: remoteBundle.schema,
            schemaVersion: remoteBundle.schemaVersion,
            strokeCount: 1,
            pointCount: 2,
            assetID: UUID(),
            createdAt: Date(timeIntervalSince1970: 1_700_000_000),
            revisionNumber: 1,
            active: true,
            uploadState: "complete"
        )
        defer { LocalInkCache.remove(pageID: pageID) }

        let secondEngine = SyncEngine(
            localStore: secondStore,
            service: service,
            clientID: "ipad-second"
        )
        await service.failNextPull()
        do {
            _ = try await secondEngine.syncNow()
            XCTFail("The first reconnect attempt should fail")
        } catch let error as APIError {
            XCTAssertEqual(error, .transport("offline"))
        }
        let missingAfterFailure = try await secondStore.document(id: documentID)
        XCTAssertNil(missingAfterFailure)

        let retry = try await secondEngine.syncNow()
        XCTAssertEqual(retry.pulledChanges, 1)
        let restoredCandidate = try await secondStore.document(id: documentID)
        let restoredDocument = try XCTUnwrap(restoredCandidate)
        XCTAssertEqual(restoredDocument.documentID, documentID)
        XCTAssertEqual(restoredDocument.pages.map(\.pageID), [pageID])

        XCTAssertTrue(LocalInkCache.shouldRestoreRemoteRevision(
            pageID: pageID,
            localBundle: nil,
            activeRevision: serverRevision
        ))
        let restoredRevision = try LocalInkCache.installRemoteBundle(
            remoteBundle,
            revision: serverRevision
        )
        XCTAssertEqual(restoredRevision.revisionID, serverRevision.bundleID)
        XCTAssertEqual(LocalInkCache.readInkBundle(pageID: pageID), remoteBundle)
        XCTAssertEqual(
            LocalInkCache.readPKDrawing(pageID: pageID),
            remoteBundle.nativeDrawing?.data
        )
    }
}

private actor TwoClientSyncService: SyncService {
    private var operations: [SyncOperationRequest] = []
    private var shouldFailNextPull = false

    func failNextPull() {
        shouldFailNextPull = true
    }

    func push(_ request: SyncPushRequest) async throws -> SyncPushResponse {
        operations.append(contentsOf: request.operations)
        let accepted = request.operations.enumerated().map { index, operation -> [String: Any] in
            [
                "client_id": operation.clientID,
                "client_operation_id": operation.clientOperationID.uuidString,
                "operation_id": UUID().uuidString,
                "server_sequence": operations.count - request.operations.count + index + 1,
                "document_id": operation.documentID.uuidString,
                "sync_version": index + 1,
                "idempotent": false,
            ]
        }
        let data = try JSONSerialization.data(withJSONObject: [
            "accepted": accepted,
            "conflicts": [],
            "current_versions": Dictionary(
                uniqueKeysWithValues: request.operations.map { ($0.documentID.uuidString, 1) }
            ),
        ])
        return try APIJSON.decoder().decode(SyncPushResponse.self, from: data)
    }

    func pull(cursor: Int, limit: Int) async throws -> SyncPullResponse {
        if shouldFailNextPull {
            shouldFailNextPull = false
            throw APIError.transport("offline")
        }
        let unseen = Array(operations.dropFirst(cursor).prefix(limit))
        let items = try unseen.enumerated().map { offset, operation -> [String: Any] in
            let payload = try JSONSerialization.jsonObject(
                with: APIJSON.encoder().encode(operation.payload)
            )
            return [
                "server_sequence": cursor + offset + 1,
                "operation_id": UUID().uuidString,
                "document_id": operation.documentID.uuidString,
                "client_id": operation.clientID,
                "client_operation_id": operation.clientOperationID.uuidString,
                "base_sync_version": operation.baseSyncVersion,
                "sync_version": 1,
                "entity_type": operation.entityType,
                "entity_id": operation.entityID as Any,
                "operation_type": operation.operationType,
                "payload": payload,
                "created_at": "2026-09-12T12:00:00Z",
                "is_tombstone": false,
            ]
        }
        let nextCursor = cursor + unseen.count
        let data = try JSONSerialization.data(withJSONObject: [
            "items": items,
            "next_cursor": nextCursor,
            "has_more": nextCursor < operations.count,
        ])
        return try APIJSON.decoder().decode(SyncPullResponse.self, from: data)
    }

    func conflicts(includeResolved: Bool) async throws -> [SyncConflict] { [] }

    func resolveConflict(
        conflictID: UUID,
        request: ResolveSyncConflictRequest
    ) async throws -> SyncConflict {
        throw APIError.invalidResponse
    }
}
