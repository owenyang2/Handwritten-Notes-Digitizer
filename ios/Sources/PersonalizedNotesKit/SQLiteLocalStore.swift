import Foundation
import SQLite3

public enum PendingOperationState: String, Codable, Sendable {
    case pending
    case failed
    case conflicted
    case cancelled
    case acknowledged
}

public struct PendingOperation: Codable, Equatable, Hashable, Sendable, Identifiable {
    public let clientID: String
    public let clientOperationID: UUID
    public let documentID: UUID
    public var baseSyncVersion: Int
    public let entityType: String
    public let entityID: String?
    public let operationType: String
    public let payload: JSONDocument
    public var state: PendingOperationState
    public var attemptCount: Int
    public var lastError: String?
    public let createdAt: Date
    public var updatedAt: Date

    public var id: UUID { clientOperationID }

    public init(
        clientID: String,
        clientOperationID: UUID = UUID(),
        documentID: UUID,
        baseSyncVersion: Int,
        entityType: String,
        entityID: String? = nil,
        operationType: String,
        payload: JSONDocument = [:],
        state: PendingOperationState = .pending,
        attemptCount: Int = 0,
        lastError: String? = nil,
        createdAt: Date = Date(),
        updatedAt: Date = Date()
    ) {
        self.clientID = clientID
        self.clientOperationID = clientOperationID
        self.documentID = documentID
        self.baseSyncVersion = max(0, baseSyncVersion)
        self.entityType = entityType
        self.entityID = entityID
        self.operationType = operationType
        self.payload = payload
        self.state = state
        self.attemptCount = max(0, attemptCount)
        self.lastError = lastError
        self.createdAt = createdAt
        self.updatedAt = updatedAt
    }

    public var request: SyncOperationRequest {
        SyncOperationRequest(
            clientID: clientID,
            clientOperationID: clientOperationID,
            documentID: documentID,
            baseSyncVersion: baseSyncVersion,
            entityType: entityType,
            entityID: entityID,
            operationType: operationType,
            payload: payload
        )
    }
}

public struct StoredSyncConflict: Codable, Equatable, Hashable, Sendable, Identifiable {
    public let conflictID: UUID
    public let documentID: UUID
    public let clientID: String
    public let clientOperationID: UUID
    public let baseSyncVersion: Int
    public let currentSyncVersion: Int
    public let entityType: String
    public let entityID: String?
    public let operationType: String
    public let payload: JSONDocument
    public var status: String
    public var resolution: JSONDocument?
    public let createdAt: Date
    public var resolvedAt: Date?

    public var id: UUID { conflictID }

    init(serverConflict: SyncConflict) {
        self.conflictID = serverConflict.conflictID
        self.documentID = serverConflict.documentID
        self.clientID = serverConflict.clientID
        self.clientOperationID = serverConflict.clientOperationID
        self.baseSyncVersion = serverConflict.baseSyncVersion
        self.currentSyncVersion = serverConflict.currentSyncVersion
        self.entityType = serverConflict.entityType
        self.entityID = serverConflict.entityID
        self.operationType = serverConflict.operationType
        self.payload = serverConflict.payload
        self.status = serverConflict.status
        self.resolution = serverConflict.resolution
        self.createdAt = serverConflict.createdAt
        self.resolvedAt = serverConflict.resolvedAt
    }
}

public enum LocalStoreError: Error, Equatable, Sendable {
    case database(String)
    case documentNotFound
    case operationNotFound
    case conflictNotFound
    case corruptPayload
    case invalidOperation(String)
}

public protocol LocalNotesStore: Sendable {
    func upsertDocuments(_ documents: [DocumentRecord]) async throws
    func document(id: UUID) async throws -> DocumentRecord?
    func listDocuments(_ query: DocumentListQuery) async throws -> [DocumentRecord]
    func enqueueDocumentMutation(
        documentID: UUID,
        clientID: String,
        operationType: String,
        payload: JSONDocument,
        entityID: String?
    ) async throws -> PendingOperation
    func pendingOperations(limit: Int) async throws -> [PendingOperation]
    func markOperationAccepted(
        clientOperationID: UUID,
        documentID: UUID,
        syncVersion: Int
    ) async throws
    func markOperationFailed(clientOperationID: UUID, error: String) async throws
    func retryOperation(clientOperationID: UUID) async throws
    func cancelOperation(clientOperationID: UUID) async throws
    func markOperationConflict(_ conflict: SyncConflict) async throws
    func applyRemoteChange(_ change: SyncChange) async throws
    func conflicts(includeResolved: Bool) async throws -> [StoredSyncConflict]
    func resolveLocalConflict(
        conflictID: UUID,
        decision: SyncConflictDecision,
        details: JSONDocument
    ) async throws
    func createRetryOperation(
        conflictID: UUID,
        clientID: String
    ) async throws -> PendingOperation
    func syncCursor() async throws -> Int
    func setSyncCursor(_ cursor: Int) async throws
}

private enum SQLiteValue {
    case integer(Int64)
    case real(Double)
    case text(String)
    case blob(Data)
    case null
}

private enum SQLiteBinding {
    case integer(Int64)
    case real(Double)
    case text(String)
    case blob(Data)
    case null
}

private final class SQLiteDatabase {
    private static let transient = unsafeBitCast(-1, to: sqlite3_destructor_type.self)
    private let handle: OpaquePointer

    init(url: URL) throws {
        var database: OpaquePointer?
        let status = sqlite3_open_v2(
            url.path,
            &database,
            SQLITE_OPEN_READWRITE | SQLITE_OPEN_CREATE | SQLITE_OPEN_FULLMUTEX,
            nil
        )
        guard status == SQLITE_OK, let database else {
            let message = database.map { String(cString: sqlite3_errmsg($0)) } ?? "unable to open database"
            if let database { sqlite3_close(database) }
            throw LocalStoreError.database(message)
        }
        handle = database
        sqlite3_busy_timeout(handle, 5_000)
    }

    deinit {
        sqlite3_close(handle)
    }

    func execute(_ sql: String, bindings: [SQLiteBinding] = []) throws {
        try withStatement(sql, bindings: bindings) { statement in
            let result = sqlite3_step(statement)
            guard result == SQLITE_DONE else { throw databaseError() }
        }
    }

    func query(
        _ sql: String,
        bindings: [SQLiteBinding] = []
    ) throws -> [[String: SQLiteValue]] {
        try withStatement(sql, bindings: bindings) { statement in
            let columnCount = sqlite3_column_count(statement)
            var names: [String] = []
            names.reserveCapacity(Int(columnCount))
            for index in 0..<columnCount {
                guard let name = sqlite3_column_name(statement, index) else {
                    names.append("column_\(index)")
                    continue
                }
                names.append(String(cString: name))
            }

            var rows: [[String: SQLiteValue]] = []
            while true {
                let result = sqlite3_step(statement)
                if result == SQLITE_DONE { break }
                guard result == SQLITE_ROW else { throw databaseError() }

                var row: [String: SQLiteValue] = [:]
                for index in 0..<columnCount {
                    let type = sqlite3_column_type(statement, index)
                    let value: SQLiteValue
                    switch type {
                    case SQLITE_INTEGER:
                        value = .integer(sqlite3_column_int64(statement, index))
                    case SQLITE_FLOAT:
                        value = .real(sqlite3_column_double(statement, index))
                    case SQLITE_BLOB:
                        let byteCount = Int(sqlite3_column_bytes(statement, index))
                        if byteCount == 0 {
                            value = .blob(Data())
                        } else if let bytes = sqlite3_column_blob(statement, index) {
                            value = .blob(Data(bytes: bytes, count: byteCount))
                        } else {
                            value = .null
                        }
                    case SQLITE_TEXT:
                        if let pointer = sqlite3_column_text(statement, index) {
                            value = .text(String(cString: pointer))
                        } else {
                            value = .null
                        }
                    default:
                        value = .null
                    }
                    row[names[Int(index)]] = value
                }
                rows.append(row)
            }
            return rows
        }
    }

    func transaction<T>(_ body: () throws -> T) throws -> T {
        try execute("BEGIN IMMEDIATE")
        do {
            let value = try body()
            try execute("COMMIT")
            return value
        } catch {
            try? execute("ROLLBACK")
            throw error
        }
    }

    private func withStatement<T>(
        _ sql: String,
        bindings: [SQLiteBinding],
        body: (OpaquePointer) throws -> T
    ) throws -> T {
        var statement: OpaquePointer?
        guard sqlite3_prepare_v2(handle, sql, -1, &statement, nil) == SQLITE_OK,
              let statement else {
            throw databaseError()
        }
        defer { sqlite3_finalize(statement) }
        for (offset, binding) in bindings.enumerated() {
            try bind(binding, to: statement, at: Int32(offset + 1))
        }
        return try body(statement)
    }

    private func bind(
        _ binding: SQLiteBinding,
        to statement: OpaquePointer,
        at index: Int32
    ) throws {
        let status: Int32
        switch binding {
        case let .integer(value):
            status = sqlite3_bind_int64(statement, index, value)
        case let .real(value):
            status = sqlite3_bind_double(statement, index, value)
        case let .text(value):
            status = value.withCString {
                sqlite3_bind_text(statement, index, $0, -1, Self.transient)
            }
        case let .blob(value):
            status = value.withUnsafeBytes { bytes in
                sqlite3_bind_blob(statement, index, bytes.baseAddress, Int32(value.count), Self.transient)
            }
        case .null:
            status = sqlite3_bind_null(statement, index)
        }
        guard status == SQLITE_OK else { throw databaseError() }
    }

    private func databaseError() -> LocalStoreError {
        .database(String(cString: sqlite3_errmsg(handle)))
    }
}

/// SQLite-backed local-first storage. The database is private to the account
/// on the device and uses WAL + FULL synchronous mode. Each local projection
/// update and its pending operation are committed in one `BEGIN IMMEDIATE`
/// transaction, so a crash cannot leave a visible edit without its replayable
/// intent (or vice versa).
public actor SQLiteLocalStore: LocalNotesStore {
    public let databaseURL: URL
    private let database: SQLiteDatabase

    public init(databaseURL: URL) throws {
        self.databaseURL = databaseURL
        try FileManager.default.createDirectory(
            at: databaseURL.deletingLastPathComponent(),
            withIntermediateDirectories: true
        )
        self.database = try SQLiteDatabase(url: databaseURL)
        try database.execute("PRAGMA foreign_keys = ON")
        try database.execute("PRAGMA journal_mode = WAL")
        try database.execute("PRAGMA synchronous = FULL")
        try migrate()
        #if os(iOS)
        try? FileManager.default.setAttributes(
            [.protectionKey: FileProtectionType.completeUntilFirstUserAuthentication],
            ofItemAtPath: databaseURL.path
        )
        #endif
    }

    public init(applicationSupportDirectory: URL? = nil) throws {
        let directory = applicationSupportDirectory
            ?? FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
                .appendingPathComponent("PersonalizedNotes", isDirectory: true)
        try self.init(databaseURL: directory.appendingPathComponent("notes.sqlite3"))
    }

    public func upsertDocuments(_ documents: [DocumentRecord]) throws {
        try database.transaction {
            for document in documents {
                try write(document)
            }
        }
    }

    public func document(id: UUID) throws -> DocumentRecord? {
        let rows = try database.query(
            "SELECT * FROM local_documents WHERE document_id = ?",
            bindings: [.text(id.uuidString)]
        )
        guard let row = rows.first else { return nil }
        return try decodeDocument(row)
    }

    public func listDocuments(_ query: DocumentListQuery = DocumentListQuery()) throws -> [DocumentRecord] {
        var predicates: [String]
        switch query.view {
        case .active:
            predicates = ["trashed_at IS NULL", "archived_at IS NULL"]
        case .archived:
            predicates = ["trashed_at IS NULL", "archived_at IS NOT NULL"]
        case .trash:
            predicates = ["trashed_at IS NOT NULL"]
        }

        var bindings: [SQLiteBinding] = []
        if let search = query.query?.trimmingCharacters(in: .whitespacesAndNewlines), !search.isEmpty {
            predicates.append("title LIKE ? COLLATE NOCASE")
            bindings.append(.text("%\(search.replacingOccurrences(of: "%", with: "\\%"))%"))
        }
        bindings.append(.integer(Int64(query.limit)))
        bindings.append(.integer(Int64(query.offset)))
        let rows = try database.query(
            "SELECT * FROM local_documents WHERE \(predicates.joined(separator: " AND ")) "
                + "ORDER BY updated_at DESC LIMIT ? OFFSET ?",
            bindings: bindings
        )
        return try rows.map(decodeDocument)
    }

    public func enqueueDocumentMutation(
        documentID: UUID,
        clientID: String,
        operationType: String,
        payload: JSONDocument,
        entityID: String? = nil
    ) throws -> PendingOperation {
        guard var document = try document(id: documentID) else {
            throw LocalStoreError.documentNotFound
        }
        let now = Date()
        let operation = PendingOperation(
            clientID: clientID,
            documentID: documentID,
            baseSyncVersion: document.syncVersion,
            entityType: "document",
            entityID: entityID,
            operationType: operationType,
            payload: payload,
            createdAt: now,
            updatedAt: now
        )
        try database.transaction {
            applyLocalProjection(operation, to: &document, now: now)
            try write(document)
            try insert(operation)
        }
        return operation
    }

    public func pendingOperations(limit: Int = 50) throws -> [PendingOperation] {
        let rows = try database.query(
            "SELECT * FROM pending_operations WHERE state = 'pending' "
                + "ORDER BY created_at ASC LIMIT ?",
            bindings: [.integer(Int64(max(1, min(limit, 50))))]
        )
        return try rows.map(decodeOperation)
    }

    public func markOperationAccepted(
        clientOperationID: UUID,
        documentID: UUID,
        syncVersion: Int
    ) throws {
        let now = Date()
        try database.transaction {
            guard try operationExists(clientOperationID) else {
                throw LocalStoreError.operationNotFound
            }
            try database.execute(
                "UPDATE local_documents SET sync_version = MAX(sync_version, ?), "
                    + "updated_at = MAX(updated_at, ?) WHERE document_id = ?",
                bindings: [
                    .integer(Int64(max(0, syncVersion))),
                    .real(now.timeIntervalSince1970),
                    .text(documentID.uuidString),
                ]
            )
            try database.execute(
                "DELETE FROM pending_operations WHERE client_operation_id = ?",
                bindings: [.text(clientOperationID.uuidString)]
            )
            try database.execute(
                "UPDATE pending_operations SET base_sync_version = ?, updated_at = ? "
                    + "WHERE document_id = ? AND state = 'pending' AND base_sync_version < ?",
                bindings: [
                    .integer(Int64(max(0, syncVersion))),
                    .real(now.timeIntervalSince1970),
                    .text(documentID.uuidString),
                    .integer(Int64(max(0, syncVersion))),
                ]
            )
        }
    }

    public func markOperationFailed(clientOperationID: UUID, error: String) throws {
        let now = Date()
        try database.execute(
            "UPDATE pending_operations SET state = 'failed', attempt_count = attempt_count + 1, "
                + "last_error = ?, updated_at = ? WHERE client_operation_id = ?",
            bindings: [
                .text(String(error.prefix(1_000))),
                .real(now.timeIntervalSince1970),
                .text(clientOperationID.uuidString),
            ]
        )
    }

    public func retryOperation(clientOperationID: UUID) throws {
        let now = Date()
        try database.execute(
            "UPDATE pending_operations SET state = 'pending', last_error = NULL, updated_at = ? "
                + "WHERE client_operation_id = ? AND state = 'failed'",
            bindings: [.real(now.timeIntervalSince1970), .text(clientOperationID.uuidString)]
        )
    }

    public func cancelOperation(clientOperationID: UUID) throws {
        let now = Date()
        try database.execute(
            "UPDATE pending_operations SET state = 'cancelled', updated_at = ? "
                + "WHERE client_operation_id = ? AND state IN ('pending', 'failed')",
            bindings: [.real(now.timeIntervalSince1970), .text(clientOperationID.uuidString)]
        )
    }

    public func markOperationConflict(_ conflict: SyncConflict) throws {
        let now = Date()
        try database.transaction {
            try database.execute(
                "UPDATE pending_operations SET state = 'conflicted', updated_at = ? "
                    + "WHERE client_operation_id = ?",
                bindings: [.real(now.timeIntervalSince1970), .text(conflict.clientOperationID.uuidString)]
            )
            try insert(conflict)
        }
    }

    public func applyRemoteChange(_ change: SyncChange) throws {
        guard var document = try document(id: change.documentID) else { return }
        let changeDate = change.createdAt
        try database.transaction {
            applyRemoteProjection(change, to: &document)
            document.syncVersion = max(document.syncVersion, change.syncVersion)
            document.updatedAt = max(document.updatedAt, changeDate)
            try write(document)
        }
    }

    public func conflicts(includeResolved: Bool = false) throws -> [StoredSyncConflict] {
        let sql = includeResolved
            ? "SELECT * FROM local_conflicts ORDER BY created_at DESC"
            : "SELECT * FROM local_conflicts WHERE status = 'pending' ORDER BY created_at DESC"
        return try database.query(sql).map(decodeConflict)
    }

    public func resolveLocalConflict(
        conflictID: UUID,
        decision: SyncConflictDecision,
        details: JSONDocument = [:]
    ) throws {
        let now = Date()
        let resolution: JSONDocument = [
            "decision": .string(decision.rawValue),
            "details": .object(details),
        ]
        let resolutionData = try APIJSON.encoder().encode(resolution)
        try database.transaction {
            guard try database.query(
                "SELECT conflict_id FROM local_conflicts WHERE conflict_id = ?",
                bindings: [.text(conflictID.uuidString)]
            ).first != nil else { throw LocalStoreError.conflictNotFound }
            try database.execute(
                "UPDATE local_conflicts SET status = 'resolved', resolution = ?, resolved_at = ? "
                    + "WHERE conflict_id = ?",
                bindings: [
                    .blob(resolutionData),
                    .real(now.timeIntervalSince1970),
                    .text(conflictID.uuidString),
                ]
            )
            if decision == .keepServer {
                let rows = try database.query(
                    "SELECT client_operation_id FROM local_conflicts WHERE conflict_id = ?",
                    bindings: [.text(conflictID.uuidString)]
                )
                if let operationID = rows.first.flatMap({ text($0, "client_operation_id") }) {
                    try database.execute(
                        "DELETE FROM pending_operations WHERE client_operation_id = ?",
                        bindings: [.text(operationID)]
                    )
                }
            }
        }
    }

    public func createRetryOperation(
        conflictID: UUID,
        clientID: String
    ) throws -> PendingOperation {
        guard let conflict = try conflicts(includeResolved: true).first(where: { $0.conflictID == conflictID }) else {
            throw LocalStoreError.conflictNotFound
        }
        guard let document = try document(id: conflict.documentID) else {
            throw LocalStoreError.documentNotFound
        }
        let now = Date()
        let operation = PendingOperation(
            clientID: clientID,
            documentID: conflict.documentID,
            baseSyncVersion: document.syncVersion,
            entityType: conflict.entityType,
            entityID: conflict.entityID,
            operationType: conflict.operationType,
            payload: conflict.payload,
            createdAt: now,
            updatedAt: now
        )
        try database.transaction {
            try insert(operation)
            var projected = document
            applyLocalProjection(operation, to: &projected, now: now)
            try write(projected)
        }
        return operation
    }

    public func syncCursor() throws -> Int {
        guard let row = try database.query(
            "SELECT value FROM sync_metadata WHERE key = 'server_cursor'"
        ).first else { return 0 }
        return Int(integer(row, "value") ?? 0)
    }

    public func setSyncCursor(_ cursor: Int) throws {
        try database.execute(
            "INSERT INTO sync_metadata(key, value) VALUES('server_cursor', ?) "
                + "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            bindings: [.integer(Int64(max(0, cursor)))]
        )
    }

    private func migrate() throws {
        try database.transaction {
            try database.execute("""
                CREATE TABLE IF NOT EXISTS local_documents (
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
                )
                """)
            try database.execute("""
                CREATE TABLE IF NOT EXISTS pending_operations (
                    client_operation_id TEXT PRIMARY KEY NOT NULL,
                    client_id TEXT NOT NULL,
                    document_id TEXT NOT NULL,
                    base_sync_version INTEGER NOT NULL,
                    entity_type TEXT NOT NULL,
                    entity_id TEXT,
                    operation_type TEXT NOT NULL,
                    payload BLOB NOT NULL,
                    state TEXT NOT NULL,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    FOREIGN KEY(document_id) REFERENCES local_documents(document_id) ON DELETE CASCADE
                )
                """)
            try database.execute("""
                CREATE INDEX IF NOT EXISTS ix_pending_operations_ready
                ON pending_operations(state, created_at)
                """)
            try database.execute("""
                CREATE TABLE IF NOT EXISTS local_conflicts (
                    conflict_id TEXT PRIMARY KEY NOT NULL,
                    document_id TEXT NOT NULL,
                    client_id TEXT NOT NULL,
                    client_operation_id TEXT NOT NULL,
                    base_sync_version INTEGER NOT NULL,
                    current_sync_version INTEGER NOT NULL,
                    entity_type TEXT NOT NULL,
                    entity_id TEXT,
                    operation_type TEXT NOT NULL,
                    payload BLOB NOT NULL,
                    status TEXT NOT NULL,
                    resolution BLOB,
                    created_at REAL NOT NULL,
                    resolved_at REAL,
                    UNIQUE(client_id, client_operation_id),
                    FOREIGN KEY(document_id) REFERENCES local_documents(document_id) ON DELETE CASCADE
                )
                """)
            try database.execute("""
                CREATE TABLE IF NOT EXISTS sync_metadata (
                    key TEXT PRIMARY KEY NOT NULL,
                    value INTEGER NOT NULL
                )
                """)
        }
    }

    private func write(_ document: DocumentRecord) throws {
        let imageData = try APIJSON.encoder().encode(document.imageIDs)
        let pageData = try APIJSON.encoder().encode(document.pages)
        let metadataData = try document.sourceMetadata.map { try APIJSON.encoder().encode($0) }
        try database.execute(
            """
            INSERT INTO local_documents(
                document_id, title, created_at, image_ids, source_kind, pages,
                active_revision_id, updated_at, archived_at, trashed_at, sync_version,
                default_language, course_context, source_metadata
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(document_id) DO UPDATE SET
                title = excluded.title,
                image_ids = excluded.image_ids,
                source_kind = excluded.source_kind,
                pages = excluded.pages,
                active_revision_id = excluded.active_revision_id,
                updated_at = excluded.updated_at,
                archived_at = excluded.archived_at,
                trashed_at = excluded.trashed_at,
                sync_version = excluded.sync_version,
                default_language = excluded.default_language,
                course_context = excluded.course_context,
                source_metadata = excluded.source_metadata
            """,
            bindings: [
                .text(document.documentID.uuidString),
                .text(document.title),
                .real(document.createdAt.timeIntervalSince1970),
                .blob(imageData),
                .text(document.sourceKind),
                .blob(pageData),
                document.activeRevisionID.map { .text($0.uuidString) } ?? .null,
                .real(document.updatedAt.timeIntervalSince1970),
                document.archivedAt.map { .real($0.timeIntervalSince1970) } ?? .null,
                document.trashedAt.map { .real($0.timeIntervalSince1970) } ?? .null,
                .integer(Int64(max(0, document.syncVersion))),
                .text(document.defaultLanguage),
                document.courseContext.map(SQLiteBinding.text) ?? .null,
                metadataData.map(SQLiteBinding.blob) ?? .null,
            ]
        )
    }

    private func insert(_ operation: PendingOperation) throws {
        let payload = try APIJSON.encoder().encode(operation.payload)
        try database.execute(
            """
            INSERT INTO pending_operations(
                client_operation_id, client_id, document_id, base_sync_version,
                entity_type, entity_id, operation_type, payload, state,
                attempt_count, last_error, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            bindings: [
                .text(operation.clientOperationID.uuidString),
                .text(operation.clientID),
                .text(operation.documentID.uuidString),
                .integer(Int64(operation.baseSyncVersion)),
                .text(operation.entityType),
                operation.entityID.map(SQLiteBinding.text) ?? .null,
                .text(operation.operationType),
                .blob(payload),
                .text(operation.state.rawValue),
                .integer(Int64(operation.attemptCount)),
                operation.lastError.map(SQLiteBinding.text) ?? .null,
                .real(operation.createdAt.timeIntervalSince1970),
                .real(operation.updatedAt.timeIntervalSince1970),
            ]
        )
    }

    private func insert(_ conflict: SyncConflict) throws {
        let payload = try APIJSON.encoder().encode(conflict.payload)
        let resolution = try conflict.resolution.map { try APIJSON.encoder().encode($0) }
        try database.execute(
            """
            INSERT INTO local_conflicts(
                conflict_id, document_id, client_id, client_operation_id,
                base_sync_version, current_sync_version, entity_type, entity_id,
                operation_type, payload, status, resolution, created_at, resolved_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(client_id, client_operation_id) DO UPDATE SET
                current_sync_version = excluded.current_sync_version,
                status = excluded.status,
                resolution = excluded.resolution,
                resolved_at = excluded.resolved_at
            """,
            bindings: [
                .text(conflict.conflictID.uuidString),
                .text(conflict.documentID.uuidString),
                .text(conflict.clientID),
                .text(conflict.clientOperationID.uuidString),
                .integer(Int64(conflict.baseSyncVersion)),
                .integer(Int64(conflict.currentSyncVersion)),
                .text(conflict.entityType),
                conflict.entityID.map(SQLiteBinding.text) ?? .null,
                .text(conflict.operationType),
                .blob(payload),
                .text(conflict.status),
                resolution.map(SQLiteBinding.blob) ?? .null,
                .real(conflict.createdAt.timeIntervalSince1970),
                conflict.resolvedAt.map { .real($0.timeIntervalSince1970) } ?? .null,
            ]
        )
    }

    private func operationExists(_ operationID: UUID) throws -> Bool {
        try !database.query(
            "SELECT client_operation_id FROM pending_operations WHERE client_operation_id = ?",
            bindings: [.text(operationID.uuidString)]
        ).isEmpty
    }

    private func applyLocalProjection(
        _ operation: PendingOperation,
        to document: inout DocumentRecord,
        now: Date
    ) {
        switch operation.operationType {
        case "document.rename":
            if case let .string(title)? = operation.payload["title"], !title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                document.title = title.trimmingCharacters(in: .whitespacesAndNewlines)
            }
        case "document.archive":
            if case let .bool(archived)? = operation.payload["archived"] {
                document.archivedAt = archived ? now : nil
            }
        case "document.trash":
            document.trashedAt = now
        case "document.restore":
            document.trashedAt = nil
        default:
            break
        }
        document.updatedAt = now
    }

    private func applyRemoteProjection(
        _ change: SyncChange,
        to document: inout DocumentRecord
    ) {
        switch change.operationType {
        case "document.rename":
            if case let .string(title)? = change.payload["title"] {
                document.title = title
            }
        case "document.archive":
            if case let .bool(archived)? = change.payload["archived"] {
                document.archivedAt = archived ? change.createdAt : nil
            }
        case "document.trash":
            document.trashedAt = change.createdAt
        case "document.restore":
            document.trashedAt = nil
        default:
            break
        }
    }

    private func decodeDocument(_ row: [String: SQLiteValue]) throws -> DocumentRecord {
        guard
            let documentID = uuid(row, "document_id"),
            let title = text(row, "title"),
            let createdAt = date(row, "created_at"),
            let imageData = blob(row, "image_ids"),
            let sourceKind = text(row, "source_kind"),
            let pageData = blob(row, "pages"),
            let updatedAt = date(row, "updated_at"),
            let syncVersion = integer(row, "sync_version"),
            let language = text(row, "default_language")
        else { throw LocalStoreError.corruptPayload }
        do {
            return DocumentRecord(
                documentID: documentID,
                title: title,
                createdAt: createdAt,
                imageIDs: try APIJSON.decoder().decode([UUID].self, from: imageData),
                sourceKind: sourceKind,
                pages: try APIJSON.decoder().decode([PageSummary].self, from: pageData),
                activeRevisionID: uuid(row, "active_revision_id"),
                updatedAt: updatedAt,
                archivedAt: date(row, "archived_at"),
                trashedAt: date(row, "trashed_at"),
                syncVersion: Int(syncVersion),
                defaultLanguage: language,
                courseContext: text(row, "course_context"),
                sourceMetadata: try blob(row, "source_metadata").map {
                    try APIJSON.decoder().decode(JSONDocument.self, from: $0)
                }
            )
        } catch {
            throw LocalStoreError.corruptPayload
        }
    }

    private func decodeOperation(_ row: [String: SQLiteValue]) throws -> PendingOperation {
        guard
            let clientID = text(row, "client_id"),
            let operationID = uuid(row, "client_operation_id"),
            let documentID = uuid(row, "document_id"),
            let base = integer(row, "base_sync_version"),
            let entityType = text(row, "entity_type"),
            let operationType = text(row, "operation_type"),
            let payloadData = blob(row, "payload"),
            let stateValue = text(row, "state"),
            let state = PendingOperationState(rawValue: stateValue),
            let createdAt = date(row, "created_at"),
            let updatedAt = date(row, "updated_at")
        else { throw LocalStoreError.corruptPayload }
        guard let payload = try? APIJSON.decoder().decode(JSONDocument.self, from: payloadData) else {
            throw LocalStoreError.corruptPayload
        }
        return PendingOperation(
            clientID: clientID,
            clientOperationID: operationID,
            documentID: documentID,
            baseSyncVersion: Int(base),
            entityType: entityType,
            entityID: text(row, "entity_id"),
            operationType: operationType,
            payload: payload,
            state: state,
            attemptCount: Int(integer(row, "attempt_count") ?? 0),
            lastError: text(row, "last_error"),
            createdAt: createdAt,
            updatedAt: updatedAt
        )
    }

    private func decodeConflict(_ row: [String: SQLiteValue]) throws -> StoredSyncConflict {
        guard
            let conflictID = uuid(row, "conflict_id"),
            let documentID = uuid(row, "document_id"),
            let clientID = text(row, "client_id"),
            let operationID = uuid(row, "client_operation_id"),
            let base = integer(row, "base_sync_version"),
            let current = integer(row, "current_sync_version"),
            let entityType = text(row, "entity_type"),
            let operationType = text(row, "operation_type"),
            let payloadData = blob(row, "payload"),
            let status = text(row, "status"),
            let createdAt = date(row, "created_at")
        else { throw LocalStoreError.corruptPayload }
        guard let payload = try? APIJSON.decoder().decode(JSONDocument.self, from: payloadData) else {
            throw LocalStoreError.corruptPayload
        }
        let resolution = try blob(row, "resolution").map {
            try APIJSON.decoder().decode(JSONDocument.self, from: $0)
        }
        var result = StoredSyncConflict(
            conflictID: conflictID,
            documentID: documentID,
            clientID: clientID,
            clientOperationID: operationID,
            baseSyncVersion: Int(base),
            currentSyncVersion: Int(current),
            entityType: entityType,
            entityID: text(row, "entity_id"),
            operationType: operationType,
            payload: payload,
            status: status,
            resolution: resolution,
            createdAt: createdAt,
            resolvedAt: date(row, "resolved_at")
        )
        return result
    }

    private func uuid(_ row: [String: SQLiteValue], _ key: String) -> UUID? {
        guard let value = text(row, key) else { return nil }
        return UUID(uuidString: value)
    }

    private func text(_ row: [String: SQLiteValue], _ key: String) -> String? {
        guard let value = row[key], case let .text(text) = value else { return nil }
        return text
    }

    private func integer(_ row: [String: SQLiteValue], _ key: String) -> Int64? {
        guard let value = row[key] else { return nil }
        switch value {
        case let .integer(value): return value
        case let .real(value): return Int64(value)
        case let .text(value): return Int64(value)
        default: return nil
        }
    }

    private func blob(_ row: [String: SQLiteValue], _ key: String) -> Data? {
        guard let value = row[key] else { return nil }
        switch value {
        case let .blob(value): return value
        case let .text(value): return Data(value.utf8)
        default: return nil
        }
    }

    private func date(_ row: [String: SQLiteValue], _ key: String) -> Date? {
        guard let value = row[key] else { return nil }
        switch value {
        case let .real(value): return Date(timeIntervalSince1970: value)
        case let .integer(value): return Date(timeIntervalSince1970: TimeInterval(value))
        case let .text(value): return Double(value).map { Date(timeIntervalSince1970: $0) }
        default: return nil
        }
    }
}

private extension StoredSyncConflict {
    init(
        conflictID: UUID,
        documentID: UUID,
        clientID: String,
        clientOperationID: UUID,
        baseSyncVersion: Int,
        currentSyncVersion: Int,
        entityType: String,
        entityID: String?,
        operationType: String,
        payload: JSONDocument,
        status: String,
        resolution: JSONDocument?,
        createdAt: Date,
        resolvedAt: Date?
    ) {
        self.conflictID = conflictID
        self.documentID = documentID
        self.clientID = clientID
        self.clientOperationID = clientOperationID
        self.baseSyncVersion = baseSyncVersion
        self.currentSyncVersion = currentSyncVersion
        self.entityType = entityType
        self.entityID = entityID
        self.operationType = operationType
        self.payload = payload
        self.status = status
        self.resolution = resolution
        self.createdAt = createdAt
        self.resolvedAt = resolvedAt
    }
}
