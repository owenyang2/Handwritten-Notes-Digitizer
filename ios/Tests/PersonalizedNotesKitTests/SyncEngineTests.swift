import XCTest
@testable import PersonalizedNotesKit

final class SyncEngineTests: XCTestCase {
    func testSyncDTOsAcceptServersWithoutTombstoneMetadata() throws {
        let response: SyncPullResponse = try APIJSON.decoder().decode(
            SyncPullResponse.self,
            from: Data(
                """
                {
                  "items": [{
                    "server_sequence": 1,
                    "operation_id": "11111111-1111-4111-8111-111111111111",
                    "document_id": "22222222-2222-4222-8222-222222222222",
                    "client_id": "legacy-client",
                    "client_operation_id": "33333333-3333-4333-8333-333333333333",
                    "base_sync_version": 0,
                    "sync_version": 1,
                    "entity_type": "document",
                    "entity_id": null,
                    "operation_type": "document.rename",
                    "payload": {},
                    "created_at": "2026-01-01T00:00:00Z"
                  }],
                  "next_cursor": 1,
                  "has_more": false
                }
                """.utf8
            )
        )

        XCTAssertNil(response.items.first?.isTombstone)
    }

    func testRecognitionRunDTOsRoundTripSnakeCaseAndExposeTerminalState() throws {
        let request = RecognitionRunRequest(
            scope: "page",
            mode: "ink",
            idempotencyKey: UUID(uuidString: "11111111-1111-4111-8111-111111111111"),
            regionIDs: [],
            inkBundleID: UUID(uuidString: "22222222-2222-4222-8222-222222222222")
        )
        let encoded = try APIJSON.encoder().encode(request)
        let encodedObject = try JSONSerialization.jsonObject(with: encoded) as? [String: Any]
        XCTAssertEqual(encodedObject?["idempotency_key"] as? String, "11111111-1111-4111-8111-111111111111")
        XCTAssertTrue((encodedObject?["region_ids"] as? [Any])?.isEmpty == true)
        XCTAssertEqual(encodedObject?["ink_bundle_id"] as? String, "22222222-2222-4222-8222-222222222222")

        let run: RecognitionRun = try APIJSON.decoder().decode(
            RecognitionRun.self,
            from: Data(
                """
                {
                  "run_id": "22222222-2222-4222-8222-222222222222",
                  "document_id": "33333333-3333-4333-8333-333333333333",
                  "page_id": "44444444-4444-4444-8444-444444444444",
                  "state": "failed",
                  "scope": "page",
                  "mode": "ink",
                  "error_message": "model_unavailable",
                  "result_ids": [],
                  "created_at": "2026-01-01T00:00:00Z"
                }
                """.utf8
            )
        )

        XCTAssertEqual(run.state, "failed")
        XCTAssertEqual(run.errorMessage, "model_unavailable")
        XCTAssertTrue(run.isTerminal)
        XCTAssertFalse(run.isSuccessful)

        let persistedRun = try APIJSON.decoder().decode(
            RecognitionRun.self,
            from: APIJSON.encoder().encode(run)
        )
        XCTAssertEqual(persistedRun, run)
    }

    func testBackendHealthDTODecodesCapabilityAndModelReadiness() throws {
        let health: BackendHealthResponse = try APIJSON.decoder().decode(
            BackendHealthResponse.self,
            from: Data(
                """
                {
                  "status": "ok",
                  "capabilities": {
                    "recognition": false,
                    "sync": true
                  },
                  "model_status": "unavailable",
                  "model": {"loaded": false}
                }
                """.utf8
            )
        )

        XCTAssertEqual(health.status, "ok")
        XCTAssertEqual(health.modelStatus, "unavailable")
        XCTAssertEqual(health.capabilities["sync"], .bool(true))
        XCTAssertEqual(health.capabilities["recognition"], .bool(false))
    }

    func testSyncNowPushesPendingOperationAndPullsEmptyFeed() async throws {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("sync-engine-\(UUID().uuidString)", isDirectory: true)
        let store = try SQLiteLocalStore(
            databaseURL: directory.appendingPathComponent("notes.sqlite3")
        )
        let documentID = UUID()
        try await store.upsertDocuments([
            DocumentRecord(documentID: documentID, title: "Sync me"),
        ])
        let operation = try await store.enqueueDocumentMutation(
            documentID: documentID,
            clientID: "sync-client",
            operationType: "document.rename",
            payload: ["title": .string("Pushed")],
            entityID: nil
        )

        let service = FakeSyncService()
        await service.prepareAcceptance(syncVersion: 3)
        let engine = SyncEngine(localStore: store, service: service, clientID: "sync-client")
        let summary = try await engine.syncNow()

        XCTAssertEqual(summary.acknowledgedOperations, 1)
        XCTAssertEqual(summary.conflicts, 0)
        XCTAssertEqual(summary.pulledChanges, 0)
        let pending = try await engine.pendingOperations()
        XCTAssertTrue(pending.isEmpty)
        let document = try await store.document(id: documentID)
        XCTAssertEqual(document?.syncVersion, 3)
        let pushed = await service.pushedOperationIDs
        XCTAssertEqual(pushed, [operation.clientOperationID])
        let state = await engine.currentState()
        if case .online = state {
            // expected
        } else {
            XCTFail("Expected online state, got \(state)")
        }
    }
}

actor FakeSyncService: SyncService {
    private(set) var pushedOperationIDs: [UUID] = []
    private var syncVersion = 1

    func prepareAcceptance(syncVersion: Int) {
        self.syncVersion = syncVersion
    }

    func push(_ request: SyncPushRequest) async throws -> SyncPushResponse {
        pushedOperationIDs.append(contentsOf: request.operations.map(\.clientOperationID))
        return try Self.makeAcceptedResponse(for: request.operations, syncVersion: syncVersion)
    }

    func pull(cursor: Int, limit: Int) async throws -> SyncPullResponse {
        try Self.decode(
            """
            {"items":[],"next_cursor":\(cursor),"has_more":false}
            """
        )
    }

    func conflicts(includeResolved: Bool) async throws -> [SyncConflict] {
        []
    }

    func resolveConflict(
        conflictID: UUID,
        request: ResolveSyncConflictRequest
    ) async throws -> SyncConflict {
        throw APIError.invalidResponse
    }

    private static func makeAcceptedResponse(
        for operations: [SyncOperationRequest],
        syncVersion: Int
    ) throws -> SyncPushResponse {
        let accepted = operations.enumerated().map { index, operation in
            """
            {
              "client_id": "\(operation.clientID)",
              "client_operation_id": "\(operation.clientOperationID.uuidString)",
              "operation_id": "\(UUID().uuidString)",
              "server_sequence": \(index + 1),
              "document_id": "\(operation.documentID.uuidString)",
              "sync_version": \(syncVersion),
              "idempotent": false
            }
            """
        }.joined(separator: ",")
        let versions = operations
            .map { "\"\($0.documentID.uuidString)\": \(syncVersion)" }
            .joined(separator: ",")
        return try decode(
            """
            {"accepted":[\(accepted)],"conflicts":[],"current_versions":{\(versions)}}
            """
        )
    }

    private static func decode<T: Decodable>(_ json: String) throws -> T {
        try APIJSON.decoder().decode(T.self, from: Data(json.utf8))
    }
}
