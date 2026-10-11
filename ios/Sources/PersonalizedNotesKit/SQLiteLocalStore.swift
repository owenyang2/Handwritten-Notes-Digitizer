import Foundation
import SQLite3

public enum PendingOperationState: String, Codable, Sendable {
    case pending
    case failed
    case conflicted
    case cancelled
    case acknowledged
}

public enum LocalTransferState: String, Codable, Sendable {
    case pending
    case running
    case retryableFailed = "retryable_failed"
    case permanentlyFailed = "permanently_failed"
    case cancelled
    case complete
}

/// SQLite metadata for one immutable local native/canonical ink pair. The
/// bytes remain in the app's protected file store; this row makes their
/// lineage, hashes, and upload state queryable without scanning the filesystem.
public struct LocalPageRevisionRecord: Codable, Equatable, Hashable, Sendable, Identifiable {
    public let revisionID: UUID
    public let documentID: UUID
    public let pageID: UUID
    public var localSequence: Int
    public let pkDrawingPath: String
    public let pkDrawingSHA256: String
    public let inkBundlePath: String
    public let inkBundleSHA256: String
    public let thumbnailPath: String?
    public let createdAt: Date
    public var committedAt: Date?
    public var uploadedAt: Date?
    public var uploadState: LocalTransferState
    public var attemptCount: Int
    public var lastError: String?
    public var baseServerRevision: Int?

    public var id: UUID { revisionID }

    public init(
        revisionID: UUID,
        documentID: UUID,
        pageID: UUID,
        localSequence: Int = 0,
        pkDrawingPath: String,
        pkDrawingSHA256: String,
        inkBundlePath: String,
        inkBundleSHA256: String,
        thumbnailPath: String? = nil,
        createdAt: Date = Date(),
        committedAt: Date? = nil,
        uploadedAt: Date? = nil,
        uploadState: LocalTransferState = .pending,
        attemptCount: Int = 0,
        lastError: String? = nil,
        baseServerRevision: Int? = nil
    ) {
        self.revisionID = revisionID
        self.documentID = documentID
        self.pageID = pageID
        self.localSequence = max(0, localSequence)
        self.pkDrawingPath = pkDrawingPath
        self.pkDrawingSHA256 = pkDrawingSHA256
        self.inkBundlePath = inkBundlePath
        self.inkBundleSHA256 = inkBundleSHA256
        self.thumbnailPath = thumbnailPath
        self.createdAt = createdAt
        self.committedAt = committedAt
        self.uploadedAt = uploadedAt
        self.uploadState = uploadState
        self.attemptCount = max(0, attemptCount)
        self.lastError = lastError
        self.baseServerRevision = baseServerRevision
    }
}

/// Durable work item for an asset or immutable ink transfer. The same row is
/// reused across retries so a process restart cannot create duplicate upload
/// intent or lose the last user-visible error.
public struct LocalTransferJob: Codable, Equatable, Hashable, Sendable, Identifiable {
    public let transferID: UUID
    public let documentID: UUID
    public let pageID: UUID?
    public let revisionID: UUID?
    public let kind: String
    public let sourcePath: String?
    public var state: LocalTransferState
    public var attemptCount: Int
    public var nextAttemptAt: Date?
    public var lastError: String?
    public let createdAt: Date
    public var updatedAt: Date

    public var id: UUID { transferID }

    public init(
        transferID: UUID,
        documentID: UUID,
        pageID: UUID? = nil,
        revisionID: UUID? = nil,
        kind: String,
        sourcePath: String? = nil,
        state: LocalTransferState = .pending,
        attemptCount: Int = 0,
        nextAttemptAt: Date? = nil,
        lastError: String? = nil,
        createdAt: Date = Date(),
        updatedAt: Date = Date()
    ) {
        self.transferID = transferID
        self.documentID = documentID
        self.pageID = pageID
        self.revisionID = revisionID
        self.kind = kind
        self.sourcePath = sourcePath
        self.state = state
        self.attemptCount = max(0, attemptCount)
        self.nextAttemptAt = nextAttemptAt
        self.lastError = lastError
        self.createdAt = createdAt
        self.updatedAt = updatedAt
    }
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

/// Durable cleanup intent for assets that cannot live in SQLite. The record is
/// created in the same transaction that removes a permanently deleted
/// document, so an app crash cannot strand its ink or imported source files.
public struct LocalPurgeRecord: Codable, Equatable, Hashable, Sendable, Identifiable {
    public let documentID: UUID
    public let pageIDs: [UUID]
    public let sourcePath: String?
    public let createdAt: Date

    public var id: UUID { documentID }

    public init(
        documentID: UUID,
        pageIDs: [UUID],
        sourcePath: String? = nil,
        createdAt: Date = Date()
    ) {
        self.documentID = documentID
        self.pageIDs = pageIDs
        self.sourcePath = sourcePath
        self.createdAt = createdAt
    }
}

/// Durable cleanup intent for a page removed from a notebook. Page deletion
/// can be acknowledged after the local projection has already forgotten the
/// page, so the operation payload must be retained as a cleanup record.
public struct LocalPagePurgeRecord: Codable, Equatable, Hashable, Sendable, Identifiable {
    public let pageID: UUID
    public let documentID: UUID
    public let createdAt: Date

    public var id: UUID { pageID }

    public init(pageID: UUID, documentID: UUID, createdAt: Date = Date()) {
        self.pageID = pageID
        self.documentID = documentID
        self.createdAt = createdAt
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
            while true {
                let result = sqlite3_step(statement)
                if result == SQLITE_DONE { return }
                // PRAGMA statements such as `journal_mode = WAL` return a row.
                if result == SQLITE_ROW { continue }
                throw databaseError()
            }
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
public actor SQLiteLocalStore {
    /// The schema version is stored in SQLite's user_version pragma. Version 1
    /// is the original blob-backed store; version 2 adds normalized pages;
    /// version 3 adds immutable local revisions and transfer jobs. The legacy
    /// page blob remains a rollback/read-repair source throughout migration.
    public static let currentSchemaVersion = 3

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
        try Self.migrate(database)
        try Self.backfillSearchText(database)
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

    public func schemaVersion() throws -> Int {
        try Self.readSchemaVersion(database)
    }

    public func documentID(containingPage pageID: UUID) throws -> UUID? {
        let rows = try database.query(
            "SELECT document_id FROM local_pages WHERE page_id = ? AND is_deleted = 0",
            bindings: [.text(pageID.uuidString)]
        )
        return rows.first.flatMap { uuid($0, "document_id") }
    }

    public func recordPageRevision(_ revision: LocalPageRevisionRecord) throws {
        try database.transaction {
            try upsertPageRevision(revision)
        }
    }

    public func recordPageRevisionAndEnqueueTransfer(
        _ revision: LocalPageRevisionRecord,
        transfer: LocalTransferJob
    ) throws {
        guard revision.documentID == transfer.documentID,
              transfer.revisionID == revision.revisionID,
              transfer.pageID == revision.pageID else {
            throw LocalStoreError.invalidOperation("Revision and transfer identities do not match.")
        }
        try database.transaction {
            try upsertPageRevision(revision)
            try upsertTransfer(transfer)
        }
    }

    private func upsertPageRevision(_ revision: LocalPageRevisionRecord) throws {
        guard try database.query(
            "SELECT document_id FROM local_pages WHERE page_id = ? AND document_id = ? AND is_deleted = 0",
            bindings: [
                .text(revision.pageID.uuidString),
                .text(revision.documentID.uuidString),
            ]
        ).first != nil else {
            throw LocalStoreError.documentNotFound
        }

        let existing = try database.query(
            "SELECT document_id, page_id, local_sequence FROM local_page_revisions WHERE revision_id = ?",
            bindings: [.text(revision.revisionID.uuidString)]
        ).first
        if let existing,
           uuid(existing, "document_id") != revision.documentID
                || uuid(existing, "page_id") != revision.pageID {
            throw LocalStoreError.invalidOperation("A local revision cannot move to another page.")
        }

        let sequence: Int
        if let existingSequence = existing.flatMap({ integer($0, "local_sequence") }) {
            sequence = Int(existingSequence)
        } else if revision.localSequence > 0 {
            sequence = revision.localSequence
        } else {
            let rows = try database.query(
                "SELECT COALESCE(MAX(local_sequence), 0) + 1 AS next_sequence "
                    + "FROM local_page_revisions WHERE page_id = ?",
                bindings: [.text(revision.pageID.uuidString)]
            )
            sequence = Int(integer(rows.first ?? [:], "next_sequence") ?? 1)
        }

        try database.execute(
            """
            INSERT INTO local_page_revisions(
                revision_id, document_id, page_id, local_sequence,
                pkdrawing_path, pkdrawing_sha256, ink_bundle_path, ink_bundle_sha256,
                thumbnail_path, created_at, committed_at, uploaded_at, upload_state,
                attempt_count, last_error, base_server_revision
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(revision_id) DO UPDATE SET
                document_id = excluded.document_id,
                page_id = excluded.page_id,
                local_sequence = excluded.local_sequence,
                pkdrawing_path = excluded.pkdrawing_path,
                pkdrawing_sha256 = excluded.pkdrawing_sha256,
                ink_bundle_path = excluded.ink_bundle_path,
                ink_bundle_sha256 = excluded.ink_bundle_sha256,
                thumbnail_path = excluded.thumbnail_path,
                created_at = excluded.created_at,
                committed_at = excluded.committed_at,
                uploaded_at = excluded.uploaded_at,
                upload_state = excluded.upload_state,
                attempt_count = excluded.attempt_count,
                last_error = excluded.last_error,
                base_server_revision = excluded.base_server_revision
            """,
            bindings: [
                .text(revision.revisionID.uuidString),
                .text(revision.documentID.uuidString),
                .text(revision.pageID.uuidString),
                .integer(Int64(sequence)),
                .text(revision.pkDrawingPath),
                .text(revision.pkDrawingSHA256),
                .text(revision.inkBundlePath),
                .text(revision.inkBundleSHA256),
                revision.thumbnailPath.map(SQLiteBinding.text) ?? .null,
                .real(revision.createdAt.timeIntervalSince1970),
                revision.committedAt.map { .real($0.timeIntervalSince1970) } ?? .null,
                revision.uploadedAt.map { .real($0.timeIntervalSince1970) } ?? .null,
                .text(revision.uploadState.rawValue),
                .integer(Int64(max(0, revision.attemptCount))),
                revision.lastError.map { .text(String($0.prefix(1_000))) } ?? .null,
                revision.baseServerRevision.map { .integer(Int64($0)) } ?? .null,
            ]
        )
    }

    public func pageRevisions(pageID: UUID) throws -> [LocalPageRevisionRecord] {
        let rows = try database.query(
            "SELECT * FROM local_page_revisions WHERE page_id = ? ORDER BY local_sequence DESC",
            bindings: [.text(pageID.uuidString)]
        )
        return try rows.map(decodePageRevision)
    }

    public func markPageRevisionUploaded(revisionID: UUID, at date: Date = Date()) throws {
        try database.execute(
            "UPDATE local_page_revisions SET upload_state = ?, uploaded_at = ?, last_error = NULL "
                + "WHERE revision_id = ?",
            bindings: [
                .text(LocalTransferState.complete.rawValue),
                .real(date.timeIntervalSince1970),
                .text(revisionID.uuidString),
            ]
        )
    }

    public func enqueueTransfer(_ job: LocalTransferJob) throws {
        guard try document(id: job.documentID) != nil else {
            throw LocalStoreError.documentNotFound
        }
        try upsertTransfer(job)
    }

    private func upsertTransfer(_ job: LocalTransferJob) throws {
        try database.execute(
            """
            INSERT INTO local_transfer_queue(
                transfer_id, document_id, page_id, revision_id, kind, source_path,
                state, attempt_count, next_attempt_at, last_error, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(transfer_id) DO UPDATE SET
                document_id = excluded.document_id,
                page_id = excluded.page_id,
                revision_id = excluded.revision_id,
                kind = excluded.kind,
                source_path = excluded.source_path
            """,
            bindings: [
                .text(job.transferID.uuidString),
                .text(job.documentID.uuidString),
                job.pageID.map { .text($0.uuidString) } ?? .null,
                job.revisionID.map { .text($0.uuidString) } ?? .null,
                .text(job.kind),
                job.sourcePath.map(SQLiteBinding.text) ?? .null,
                .text(job.state.rawValue),
                .integer(Int64(max(0, job.attemptCount))),
                job.nextAttemptAt.map { .real($0.timeIntervalSince1970) } ?? .null,
                job.lastError.map { .text(String($0.prefix(1_000))) } ?? .null,
                .real(job.createdAt.timeIntervalSince1970),
                .real(job.updatedAt.timeIntervalSince1970),
            ]
        )
    }

    public func transferJobs(
        states: Set<LocalTransferState>? = nil,
        limit: Int = 100
    ) throws -> [LocalTransferJob] {
        let cappedLimit = max(1, min(limit, 500))
        var sql = "SELECT * FROM local_transfer_queue"
        var bindings: [SQLiteBinding] = []
        if let states {
            guard !states.isEmpty else { return [] }
            let values = states.map(\.rawValue).sorted()
            sql += " WHERE state IN (" + values.map { _ in "?" }.joined(separator: ", ") + ")"
            bindings.append(contentsOf: values.map(SQLiteBinding.text))
        }
        sql += " ORDER BY COALESCE(next_attempt_at, 0) ASC, created_at ASC LIMIT ?"
        bindings.append(.integer(Int64(cappedLimit)))
        return try database.query(sql, bindings: bindings).map(decodeTransferJob)
    }

    public func updateTransfer(
        transferID: UUID,
        state: LocalTransferState,
        error: String? = nil,
        nextAttemptAt: Date? = nil,
        incrementAttempt: Bool = false
    ) throws {
        let now = Date()
        try database.execute(
            "UPDATE local_transfer_queue SET state = ?, last_error = ?, next_attempt_at = ?, "
                + "attempt_count = attempt_count + ?, updated_at = ? WHERE transfer_id = ?",
            bindings: [
                .text(state.rawValue),
                error.map { .text(String($0.prefix(1_000))) } ?? .null,
                nextAttemptAt.map { .real($0.timeIntervalSince1970) } ?? .null,
                .integer(incrementAttempt ? 1 : 0),
                .real(now.timeIntervalSince1970),
                .text(transferID.uuidString),
            ]
        )
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
        case .favorites:
            predicates = [
                "trashed_at IS NULL",
                "archived_at IS NULL",
                "is_favorite = 1",
            ]
        case .archived:
            predicates = ["trashed_at IS NULL", "archived_at IS NOT NULL"]
        case .trash:
            predicates = ["trashed_at IS NOT NULL"]
        case .recent:
            predicates = [
                "trashed_at IS NULL",
                "archived_at IS NULL",
                "updated_at >= ?",
            ]
        case .imported:
            predicates = [
                "trashed_at IS NULL",
                "archived_at IS NULL",
                "source_kind IN ('pdf_import', 'image_import')",
            ]
        case .unsynced:
            predicates = [
                "trashed_at IS NULL",
                "archived_at IS NULL",
                "EXISTS (SELECT 1 FROM pending_operations pending "
                    + "WHERE pending.document_id = local_documents.document_id "
                    + "AND pending.state IN ('pending', 'failed', 'conflicted'))",
            ]
        case .needsAttention:
            predicates = [
                "trashed_at IS NULL",
                "archived_at IS NULL",
                "(EXISTS (SELECT 1 FROM pending_operations pending "
                    + "WHERE pending.document_id = local_documents.document_id "
                    + "AND pending.state IN ('failed', 'conflicted')) "
                    + "OR EXISTS (SELECT 1 FROM local_conflicts conflict "
                    + "WHERE conflict.document_id = local_documents.document_id "
                    + "AND conflict.status = 'pending'))",
            ]
        }

        var bindings: [SQLiteBinding] = []
        if query.view == .recent {
            bindings.append(.real(Date().timeIntervalSince1970 - (7 * 24 * 60 * 60)))
        }
        if let search = query.query?.trimmingCharacters(in: .whitespacesAndNewlines), !search.isEmpty {
            predicates.append("instr(search_text, ?) > 0")
            bindings.append(.text(Self.normalizedSearchText(search)))
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

    public func failedOperations(limit: Int = 50) throws -> [PendingOperation] {
        let rows = try database.query(
            "SELECT * FROM pending_operations WHERE state = 'failed' "
                + "ORDER BY updated_at DESC LIMIT ?",
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
            let operationRow = try database.query(
                "SELECT operation_type, payload FROM pending_operations WHERE client_operation_id = ?",
                bindings: [.text(clientOperationID.uuidString)]
            ).first
            let operationType = operationRow.flatMap { text($0, "operation_type") }
            if operationType == "document.delete" {
                try enqueuePurgeRecord(documentID: documentID)
            } else if operationType == "page.delete",
                      let payloadData = operationRow.flatMap({ blob($0, "payload") }),
                      let payload = try? APIJSON.decoder().decode(JSONDocument.self, from: payloadData),
                      let pageID = jsonUUID(payload["page_id"]) {
                try enqueuePagePurgeRecord(pageID: pageID, documentID: documentID)
            }
            try database.execute(
                "DELETE FROM pending_operations WHERE client_operation_id = ?",
                bindings: [.text(clientOperationID.uuidString)]
            )
            if operationType == "document.delete" {
                // A permanent delete is acknowledged only after the server
                // accepted the tombstone. Removing the row here also cascades
                // any later local operations that were queued for the same
                // document, so they cannot resurrect it on the next retry.
                try database.execute(
                    "DELETE FROM local_documents WHERE document_id = ?",
                    bindings: [.text(documentID.uuidString)]
                )
            } else {
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
        if change.operationType == "document.delete" {
            try database.transaction {
                try enqueuePurgeRecord(documentID: change.documentID)
                try database.execute(
                    "DELETE FROM local_documents WHERE document_id = ?",
                    bindings: [.text(change.documentID.uuidString)]
                )
            }
            return
        }
        guard var document = try document(id: change.documentID) else {
            if change.operationType == "page.delete",
               let pageID = jsonUUID(change.payload["page_id"]) {
                try database.transaction {
                    try enqueuePagePurgeRecord(pageID: pageID, documentID: change.documentID)
                }
            }
            guard change.operationType == "document.create",
                  let created = documentFromCreate(change) else { return }
            try database.transaction {
                try write(created)
            }
            return
        }
        let changeDate = change.createdAt
        try database.transaction {
            if change.operationType == "page.delete",
               let pageID = jsonUUID(change.payload["page_id"]) {
                try enqueuePagePurgeRecord(pageID: pageID, documentID: change.documentID)
            }
            applyRemoteProjection(change, to: &document)
            document.syncVersion = max(document.syncVersion, change.syncVersion)
            document.updatedAt = max(document.updatedAt, changeDate)
            try write(document)
        }
    }

    /// Returns cleanup intents left by acknowledged or remotely observed
    /// permanent deletes. App-layer storage drains these after a successful
    /// sync and acknowledges each record only after filesystem cleanup.
    public func pendingPurgeRecords() throws -> [LocalPurgeRecord] {
        let rows = try database.query(
            "SELECT * FROM local_purge_queue ORDER BY created_at ASC"
        )
        return try rows.map { row in
            guard let documentID = uuid(row, "document_id"),
                  let pageData = blob(row, "page_ids"),
                  let pageIDs = try? APIJSON.decoder().decode([UUID].self, from: pageData),
                  let createdAt = date(row, "created_at") else {
                throw LocalStoreError.corruptPayload
            }
            return LocalPurgeRecord(
                documentID: documentID,
                pageIDs: pageIDs,
                sourcePath: text(row, "source_path"),
                createdAt: createdAt
            )
        }
    }

    public func acknowledgePurge(documentID: UUID) throws {
        try database.execute(
            "DELETE FROM local_purge_queue WHERE document_id = ?",
            bindings: [.text(documentID.uuidString)]
        )
    }

    public func pendingPagePurgeRecords() throws -> [LocalPagePurgeRecord] {
        let rows = try database.query(
            "SELECT * FROM local_page_purge_queue ORDER BY created_at ASC"
        )
        return try rows.map { row in
            guard let pageID = uuid(row, "page_id"),
                  let documentID = uuid(row, "document_id"),
                  let createdAt = date(row, "created_at") else {
                throw LocalStoreError.corruptPayload
            }
            return LocalPagePurgeRecord(
                pageID: pageID,
                documentID: documentID,
                createdAt: createdAt
            )
        }
    }

    public func acknowledgePagePurge(pageID: UUID) throws {
        try database.transaction {
            let pageBinding = SQLiteBinding.text(pageID.uuidString)
            try database.execute(
                "DELETE FROM local_transfer_queue WHERE page_id = ?",
                bindings: [pageBinding]
            )
            try database.execute(
                "DELETE FROM local_page_revisions WHERE page_id = ?",
                bindings: [pageBinding]
            )
            try database.execute(
                "DELETE FROM local_pages WHERE page_id = ?",
                bindings: [pageBinding]
            )
            try database.execute(
                "DELETE FROM local_page_purge_queue WHERE page_id = ?",
                bindings: [pageBinding]
            )
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

    private static func normalizedSearchText(_ value: String) -> String {
        value
            .folding(options: .diacriticInsensitive, locale: .current)
            .lowercased()
    }

    private static func searchText(
        title: String,
        sourceKind: String,
        courseContext: String?,
        pages: [PageSummary],
        sourceMetadata: JSONDocument?
    ) -> String {
        let pageText = pages
            .sorted { $0.pageIndex < $1.pageIndex }
            .flatMap { page -> [String] in
                let title = page.title?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
                return ["page \(page.pageIndex + 1)", title]
            }
        let metadataText: String?
        if let sourceMetadata {
            if let data = try? APIJSON.encoder().encode(sourceMetadata) {
                metadataText = String(data: data, encoding: .utf8)
            } else {
                metadataText = nil
            }
        } else {
            metadataText = nil
        }
        let rawText = ([title, sourceKind, courseContext ?? ""] + pageText + [metadataText ?? ""])
            .joined(separator: " ")
        return normalizedSearchText(rawText)
    }

    private static func backfillSearchText(_ database: SQLiteDatabase) throws {
        let rows = try database.query(
            "SELECT document_id, title, source_kind, course_context, pages, source_metadata "
                + "FROM local_documents WHERE search_text = ''"
        )
        for row in rows {
            let courseContext: String?
            if case let .text(value)? = row["course_context"] {
                courseContext = value
            } else {
                courseContext = nil
            }
            guard
                case let .text(documentID)? = row["document_id"],
                case let .text(title)? = row["title"],
                case let .text(sourceKind)? = row["source_kind"],
                case let .blob(pageData)? = row["pages"],
                let pages = try? APIJSON.decoder().decode([PageSummary].self, from: pageData)
            else { continue }
            let sourceMetadata: JSONDocument?
            if case let .blob(metadataData)? = row["source_metadata"] {
                sourceMetadata = try? APIJSON.decoder().decode(JSONDocument.self, from: metadataData)
            } else {
                sourceMetadata = nil
            }
            try database.execute(
                "UPDATE local_documents SET search_text = ? WHERE document_id = ?",
                bindings: [
                    .text(searchText(
                        title: title,
                        sourceKind: sourceKind,
                        courseContext: courseContext,
                        pages: pages,
                        sourceMetadata: sourceMetadata
                    )),
                    .text(documentID),
                ]
            )
        }
    }

    private static func migrate(_ database: SQLiteDatabase) throws {
        let existingVersion = try readSchemaVersion(database)
        guard existingVersion <= currentSchemaVersion else {
            throw LocalStoreError.database(
                "The local notes database requires schema version \(existingVersion), "
                    + "but this app supports through \(currentSchemaVersion)."
            )
        }
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
                    is_favorite INTEGER NOT NULL DEFAULT 0,
                    sync_version INTEGER NOT NULL DEFAULT 0,
                    default_language TEXT NOT NULL,
                    course_context TEXT,
                    source_metadata BLOB,
                    search_text TEXT NOT NULL DEFAULT ''
                )
                """)
            let columns = try database.query("PRAGMA table_info(local_documents)")
            let hasFavoriteColumn = columns.contains { row in
                guard case let .text(name)? = row["name"] else { return false }
                return name == "is_favorite"
            }
            if !hasFavoriteColumn {
                try database.execute(
                    "ALTER TABLE local_documents ADD COLUMN is_favorite INTEGER NOT NULL DEFAULT 0"
                )
            }
            let hasSearchTextColumn = columns.contains { row in
                guard case let .text(name)? = row["name"] else { return false }
                return name == "search_text"
            }
            if !hasSearchTextColumn {
                try database.execute(
                    "ALTER TABLE local_documents ADD COLUMN search_text TEXT NOT NULL DEFAULT ''"
                )
            }
            try database.execute("CREATE INDEX IF NOT EXISTS ix_local_documents_search_text ON local_documents(search_text)")
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
                CREATE TABLE IF NOT EXISTS local_purge_queue (
                    document_id TEXT PRIMARY KEY NOT NULL,
                    page_ids BLOB NOT NULL,
                    source_path TEXT,
                    created_at REAL NOT NULL
                )
                """)
            try database.execute("""
                CREATE TABLE IF NOT EXISTS local_page_purge_queue (
                    page_id TEXT PRIMARY KEY NOT NULL,
                    document_id TEXT NOT NULL,
                    created_at REAL NOT NULL
                )
                """)
            try database.execute("""
                CREATE TABLE IF NOT EXISTS sync_metadata (
                    key TEXT PRIMARY KEY NOT NULL,
                    value INTEGER NOT NULL
                )
                """)

            try database.execute("""
                CREATE TABLE IF NOT EXISTS local_pages (
                    page_id TEXT PRIMARY KEY NOT NULL,
                    document_id TEXT NOT NULL,
                    page_index INTEGER NOT NULL,
                    title TEXT,
                    image_id TEXT,
                    width INTEGER NOT NULL,
                    height INTEGER NOT NULL,
                    dpi INTEGER NOT NULL,
                    coordinate_space TEXT NOT NULL,
                    paper_template TEXT,
                    active_revision_id TEXT,
                    updated_at REAL NOT NULL,
                    is_deleted INTEGER NOT NULL DEFAULT 0,
                    FOREIGN KEY(document_id) REFERENCES local_documents(document_id) ON DELETE CASCADE
                )
                """)
            let pageColumns = try database.query("PRAGMA table_info(local_pages)")
            let hasDeletedPageColumn = pageColumns.contains { row in
                guard case let .text(name)? = row["name"] else { return false }
                return name == "is_deleted"
            }
            if !hasDeletedPageColumn {
                try database.execute(
                    "ALTER TABLE local_pages ADD COLUMN is_deleted INTEGER NOT NULL DEFAULT 0"
                )
            }
            try database.execute(
                "CREATE INDEX IF NOT EXISTS ix_local_pages_document_order "
                    + "ON local_pages(document_id, page_index)"
            )

            try database.execute("""
                CREATE TABLE IF NOT EXISTS local_page_revisions (
                    revision_id TEXT PRIMARY KEY NOT NULL,
                    document_id TEXT NOT NULL,
                    page_id TEXT NOT NULL,
                    local_sequence INTEGER NOT NULL,
                    pkdrawing_path TEXT NOT NULL,
                    pkdrawing_sha256 TEXT NOT NULL,
                    ink_bundle_path TEXT NOT NULL,
                    ink_bundle_sha256 TEXT NOT NULL,
                    thumbnail_path TEXT,
                    created_at REAL NOT NULL,
                    committed_at REAL,
                    uploaded_at REAL,
                    upload_state TEXT NOT NULL,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT,
                    base_server_revision INTEGER,
                    FOREIGN KEY(document_id) REFERENCES local_documents(document_id) ON DELETE CASCADE,
                    FOREIGN KEY(page_id) REFERENCES local_pages(page_id) ON DELETE CASCADE,
                    UNIQUE(page_id, local_sequence)
                )
                """)
            try database.execute(
                "CREATE INDEX IF NOT EXISTS ix_local_page_revisions_page_order "
                    + "ON local_page_revisions(page_id, local_sequence DESC)"
            )
            try database.execute("""
                CREATE TABLE IF NOT EXISTS local_transfer_queue (
                    transfer_id TEXT PRIMARY KEY NOT NULL,
                    document_id TEXT NOT NULL,
                    page_id TEXT,
                    revision_id TEXT,
                    kind TEXT NOT NULL,
                    source_path TEXT,
                    state TEXT NOT NULL,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    next_attempt_at REAL,
                    last_error TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    FOREIGN KEY(document_id) REFERENCES local_documents(document_id) ON DELETE CASCADE,
                    FOREIGN KEY(page_id) REFERENCES local_pages(page_id) ON DELETE CASCADE,
                    FOREIGN KEY(revision_id) REFERENCES local_page_revisions(revision_id) ON DELETE SET NULL
                )
                """)
            try database.execute(
                "CREATE INDEX IF NOT EXISTS ix_local_transfer_queue_ready "
                    + "ON local_transfer_queue(state, next_attempt_at, created_at)"
            )

            if existingVersion < 2 {
                try backfillNormalizedPages(database)
            }
            try database.execute("PRAGMA user_version = \(currentSchemaVersion)")
        }
    }

    private static func readSchemaVersion(_ database: SQLiteDatabase) throws -> Int {
        guard let row = try database.query("PRAGMA user_version").first,
              let value = row.values.first else {
            throw LocalStoreError.database("SQLite did not return a schema version.")
        }
        switch value {
        case let .integer(value): return Int(value)
        case let .real(value): return Int(value)
        case let .text(value): return Int(value) ?? 0
        default: return 0
        }
    }

    private static func backfillNormalizedPages(_ database: SQLiteDatabase) throws {
        let rows = try database.query(
            "SELECT document_id, pages, updated_at FROM local_documents"
        )
        for row in rows {
            guard case let .text(documentID)? = row["document_id"],
                  case let .blob(pageData)? = row["pages"],
                  let pages = try? APIJSON.decoder().decode([PageSummary].self, from: pageData)
            else { continue }

            try database.execute(
                "UPDATE local_pages SET is_deleted = 1 WHERE document_id = ?",
                bindings: [.text(documentID)]
            )
            let updatedAt: SQLiteBinding
            if case let .real(value)? = row["updated_at"] {
                updatedAt = .real(value)
            } else if case let .integer(value)? = row["updated_at"] {
                updatedAt = .real(TimeInterval(value))
            } else {
                updatedAt = .real(Date().timeIntervalSince1970)
            }
            for page in pages.sorted(by: { $0.pageIndex < $1.pageIndex }) {
                try insertNormalizedPage(
                    database,
                    documentID: documentID,
                    page: page,
                    updatedAt: updatedAt
                )
            }
        }
    }

    private static func insertNormalizedPage(
        _ database: SQLiteDatabase,
        documentID: String,
        page: PageSummary,
        updatedAt: SQLiteBinding
    ) throws {
        try database.execute(
            """
            INSERT INTO local_pages(
                page_id, document_id, page_index, title, image_id, width, height,
                dpi, coordinate_space, paper_template, active_revision_id, updated_at, is_deleted
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(page_id) DO UPDATE SET
                document_id = excluded.document_id,
                page_index = excluded.page_index,
                title = excluded.title,
                image_id = excluded.image_id,
                width = excluded.width,
                height = excluded.height,
                dpi = excluded.dpi,
                coordinate_space = excluded.coordinate_space,
                paper_template = excluded.paper_template,
                updated_at = excluded.updated_at,
                is_deleted = 0
            """,
            bindings: [
                .text(page.pageID.uuidString),
                .text(documentID),
                .integer(Int64(page.pageIndex)),
                page.title.map(SQLiteBinding.text) ?? .null,
                page.imageID.map { .text($0.uuidString) } ?? .null,
                .integer(Int64(max(1, page.width))),
                .integer(Int64(max(1, page.height))),
                .integer(Int64(max(1, page.dpi))),
                .text(page.coordinateSpace),
                page.paperTemplate.map(SQLiteBinding.text) ?? .null,
                .null,
                updatedAt,
                .integer(0),
            ]
        )
    }

    private func write(_ document: DocumentRecord) throws {
        let imageData = try APIJSON.encoder().encode(document.imageIDs)
        let pageData = try APIJSON.encoder().encode(document.pages)
        let metadataData = try document.sourceMetadata.map { try APIJSON.encoder().encode($0) }
        let searchText = Self.searchText(
            title: document.title,
            sourceKind: document.sourceKind,
            courseContext: document.courseContext,
            pages: document.pages,
            sourceMetadata: document.sourceMetadata
        )
        try database.execute(
            """
            INSERT INTO local_documents(
                document_id, title, created_at, image_ids, source_kind, pages,
                active_revision_id, updated_at, archived_at, trashed_at,
                is_favorite, sync_version, default_language, course_context, source_metadata, search_text
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(document_id) DO UPDATE SET
                title = excluded.title,
                image_ids = excluded.image_ids,
                source_kind = excluded.source_kind,
                pages = excluded.pages,
                active_revision_id = excluded.active_revision_id,
                updated_at = excluded.updated_at,
                archived_at = excluded.archived_at,
                trashed_at = excluded.trashed_at,
                is_favorite = excluded.is_favorite,
                sync_version = excluded.sync_version,
                default_language = excluded.default_language,
                course_context = excluded.course_context,
                source_metadata = excluded.source_metadata,
                search_text = excluded.search_text
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
                .integer(document.isFavorite ? 1 : 0),
                .integer(Int64(max(0, document.syncVersion))),
                .text(document.defaultLanguage),
                document.courseContext.map(SQLiteBinding.text) ?? .null,
                metadataData.map(SQLiteBinding.blob) ?? .null,
                .text(searchText),
            ]
        )
        try replaceNormalizedPages(for: document)
    }

    private func replaceNormalizedPages(for document: DocumentRecord) throws {
        try database.execute(
            "UPDATE local_pages SET is_deleted = 1 WHERE document_id = ?",
            bindings: [.text(document.documentID.uuidString)]
        )
        for page in document.pages.sorted(by: { $0.pageIndex < $1.pageIndex }) {
            try Self.insertNormalizedPage(
                database,
                documentID: document.documentID.uuidString,
                page: page,
                updatedAt: .real(document.updatedAt.timeIntervalSince1970)
            )
        }
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

    private func enqueuePurgeRecord(documentID: UUID) throws {
        guard let document = try document(id: documentID) else { return }
        let pageData = try APIJSON.encoder().encode(document.pages.map(\.pageID))
        let sourcePath: String?
        if case let .string(path)? = document.sourceMetadata?["durable_file_path"], !path.isEmpty {
            sourcePath = path
        } else {
            sourcePath = nil
        }
        try database.execute(
            """
            INSERT INTO local_purge_queue(document_id, page_ids, source_path, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(document_id) DO UPDATE SET
                page_ids = excluded.page_ids,
                source_path = excluded.source_path,
                created_at = MIN(local_purge_queue.created_at, excluded.created_at)
            """,
            bindings: [
                .text(documentID.uuidString),
                .blob(pageData),
                sourcePath.map(SQLiteBinding.text) ?? .null,
                .real(Date().timeIntervalSince1970),
            ]
        )
    }

    private func enqueuePagePurgeRecord(pageID: UUID, documentID: UUID) throws {
        try database.execute(
            """
            INSERT INTO local_page_purge_queue(page_id, document_id, created_at)
            VALUES (?, ?, ?)
            ON CONFLICT(page_id) DO UPDATE SET
                document_id = excluded.document_id,
                created_at = MIN(local_page_purge_queue.created_at, excluded.created_at)
            """,
            bindings: [
                .text(pageID.uuidString),
                .text(documentID.uuidString),
                .real(Date().timeIntervalSince1970),
            ]
        )
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
        case "document.delete":
            // Keep the local tombstone metadata until the server acknowledges
            // the operation; markOperationAccepted then removes the row.
            document.trashedAt = document.trashedAt ?? now
        case "document.restore":
            document.trashedAt = nil
        case "document.favorite":
            if case let .bool(favorite)? = operation.payload["favorite"] {
                document.isFavorite = favorite
            }
        case "document.tags", "document.move":
            applyOrganizationMetadata(operation.payload, to: &document)
        case "page.template":
            applyPageTemplate(operation.payload, to: &document)
        case "page.rename":
            applyPageRename(operation.payload, to: &document)
        case "page.resize":
            applyPageResize(operation.payload, to: &document)
        case "page.add":
            applyPageAdd(operation.payload, to: &document)
        case "page.delete":
            applyPageDelete(operation.payload, to: &document)
        case "page.reorder":
            applyPageReorder(operation.payload, to: &document)
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
        case "document.delete":
            document.trashedAt = change.createdAt
        case "document.restore":
            document.trashedAt = nil
        case "document.favorite":
            if case let .bool(favorite)? = change.payload["favorite"] {
                document.isFavorite = favorite
            }
        case "document.tags", "document.move":
            applyOrganizationMetadata(change.payload, to: &document)
        case "page.template":
            applyPageTemplate(change.payload, to: &document)
        case "page.rename":
            applyPageRename(change.payload, to: &document)
        case "page.resize":
            applyPageResize(change.payload, to: &document)
        case "page.add":
            applyPageAdd(change.payload, to: &document)
        case "page.delete":
            applyPageDelete(change.payload, to: &document)
        case "page.reorder":
            applyPageReorder(change.payload, to: &document)
        default:
            break
        }
    }

    private func documentFromCreate(_ change: SyncChange) -> DocumentRecord? {
        guard let title = jsonString(change.payload["title"]),
              !title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            return nil
        }
        let pages = jsonArray(change.payload["pages"])
            .compactMap(pageSummary)
            .sorted { $0.pageIndex < $1.pageIndex }
        return DocumentRecord(
            documentID: change.documentID,
            title: title,
            createdAt: change.createdAt,
            sourceKind: jsonString(change.payload["source_kind"]) ?? "native_ink",
            pages: normalizedPages(pages),
            updatedAt: change.createdAt,
            isFavorite: jsonBool(change.payload["is_favorite"]) ?? false,
            syncVersion: change.syncVersion,
            defaultLanguage: jsonString(change.payload["default_language"]) ?? "en",
            sourceMetadata: jsonObject(change.payload["source_metadata"])
        )
    }

    private func applyPageAdd(_ payload: JSONDocument, to document: inout DocumentRecord) {
        guard let rawPage = payload["page"], let page = pageSummary(rawPage) else { return }
        var pages = document.pages.filter { $0.pageID != page.pageID }
        let target = jsonInt(payload["page_index"]) ?? page.pageIndex
        let index = min(max(target, 0), pages.count)
        pages.insert(page, at: index)
        document.pages = normalizedPages(pages)
    }

    private func applyPageDelete(_ payload: JSONDocument, to document: inout DocumentRecord) {
        guard let pageID = jsonUUID(payload["page_id"]) else { return }
        let pages = document.pages.filter { $0.pageID != pageID }
        guard !pages.isEmpty else { return }
        document.pages = normalizedPages(pages)
    }

    private func applyPageReorder(_ payload: JSONDocument, to document: inout DocumentRecord) {
        var pages = document.pages.sorted { $0.pageIndex < $1.pageIndex }
        guard let pageID = jsonUUID(payload["page_id"]),
              let current = pages.firstIndex(where: { $0.pageID == pageID }) else { return }
        let page = pages.remove(at: current)
        let target = min(max(jsonInt(payload["page_index"]) ?? current, 0), pages.count)
        pages.insert(page, at: target)
        document.pages = normalizedPages(pages)
    }

    private func normalizedPages(_ pages: [PageSummary]) -> [PageSummary] {
        pages.enumerated().map { index, page in
            PageSummary(
                pageID: page.pageID,
                pageIndex: index,
                title: page.title,
                imageID: page.imageID,
                width: page.width,
                height: page.height,
                dpi: page.dpi,
                coordinateSpace: page.coordinateSpace,
                paperTemplate: page.paperTemplate
            )
        }
    }

    private func applyOrganizationMetadata(
        _ payload: JSONDocument,
        to document: inout DocumentRecord
    ) {
        var metadata = document.sourceMetadata ?? [:]
        if let tags = payload["tags"] {
            metadata["tags"] = tags
        }
        if let folderID = payload["folder_id"] {
            metadata["folder_id"] = folderID
        }
        document.sourceMetadata = metadata.isEmpty ? nil : metadata
    }

    private func applyPageTemplate(_ payload: JSONDocument, to document: inout DocumentRecord) {
        guard let pageID = jsonUUID(payload["page_id"]),
              let pageIndex = document.pages.firstIndex(where: { $0.pageID == pageID }) else {
            return
        }
        let paperTemplate: String?
        switch payload["paper_template"] {
        case let .string(value):
            paperTemplate = value
        case .null, nil:
            paperTemplate = nil
        default:
            return
        }
        let page = document.pages[pageIndex]
        var pages = document.pages
        pages[pageIndex] = PageSummary(
            pageID: page.pageID,
            pageIndex: page.pageIndex,
            title: page.title,
            imageID: page.imageID,
            width: page.width,
            height: page.height,
            dpi: page.dpi,
            coordinateSpace: page.coordinateSpace,
            paperTemplate: paperTemplate
        )
        document.pages = pages
    }

    private func applyPageRename(_ payload: JSONDocument, to document: inout DocumentRecord) {
        guard let pageID = jsonUUID(payload["page_id"]),
              let pageIndex = document.pages.firstIndex(where: { $0.pageID == pageID }) else {
            return
        }
        let title: String?
        switch payload["title"] {
        case let .string(value):
            let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !trimmed.isEmpty, trimmed.count <= 255 else { return }
            title = trimmed
        case .null, nil:
            title = nil
        default:
            return
        }
        let page = document.pages[pageIndex]
        var pages = document.pages
        pages[pageIndex] = PageSummary(
            pageID: page.pageID,
            pageIndex: page.pageIndex,
            title: title,
            imageID: page.imageID,
            width: page.width,
            height: page.height,
            dpi: page.dpi,
            coordinateSpace: page.coordinateSpace,
            paperTemplate: page.paperTemplate
        )
        document.pages = pages
    }

    private func applyPageResize(_ payload: JSONDocument, to document: inout DocumentRecord) {
        guard let pageID = jsonUUID(payload["page_id"]),
              let width = jsonInt(payload["width"]),
              let height = jsonInt(payload["height"]),
              width > 0,
              height > 0,
              let pageIndex = document.pages.firstIndex(where: { $0.pageID == pageID }) else {
            return
        }
        let page = document.pages[pageIndex]
        var pages = document.pages
        pages[pageIndex] = PageSummary(
            pageID: page.pageID,
            pageIndex: page.pageIndex,
            title: page.title,
            imageID: page.imageID,
            width: width,
            height: height,
            dpi: page.dpi,
            coordinateSpace: page.coordinateSpace,
            paperTemplate: page.paperTemplate
        )
        document.pages = pages
    }

    private func pageSummary(_ value: JSONValue) -> PageSummary? {
        guard case let .object(object) = value,
              let pageID = jsonUUID(object["page_id"]),
              let width = jsonInt(object["width"]),
              let height = jsonInt(object["height"]),
              let dpi = jsonInt(object["dpi"]),
              let coordinateSpace = jsonString(object["coordinate_space"]) else {
            return nil
        }
        return PageSummary(
            pageID: pageID,
            pageIndex: jsonInt(object["page_index"]) ?? 0,
            title: jsonString(object["title"]),
            width: max(1, width),
            height: max(1, height),
            dpi: max(1, dpi),
            coordinateSpace: coordinateSpace,
            paperTemplate: jsonString(object["paper_template"])
        )
    }

    private func jsonArray(_ value: JSONValue?) -> [JSONValue] {
        guard case let .array(values)? = value else { return [] }
        return values
    }

    private func jsonString(_ value: JSONValue?) -> String? {
        guard case let .string(value)? = value else { return nil }
        return value
    }

    private func jsonBool(_ value: JSONValue?) -> Bool? {
        guard case let .bool(value)? = value else { return nil }
        return value
    }

    private func jsonObject(_ value: JSONValue?) -> JSONDocument? {
        guard case let .object(object)? = value else { return nil }
        return object
    }

    private func jsonUUID(_ value: JSONValue?) -> UUID? {
        guard let value = jsonString(value) else { return nil }
        return UUID(uuidString: value)
    }

    private func jsonInt(_ value: JSONValue?) -> Int? {
        guard let value else { return nil }
        switch value {
        case let .number(number): return Int(number)
        case let .string(string): return Int(string)
        default: return nil
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
            let blobPages = try APIJSON.decoder().decode([PageSummary].self, from: pageData)
            let pages = try normalizedPages(documentID: documentID)
            return DocumentRecord(
                documentID: documentID,
                title: title,
                createdAt: createdAt,
                imageIDs: try APIJSON.decoder().decode([UUID].self, from: imageData),
                sourceKind: sourceKind,
                // Keep the legacy blob as a safe fallback for a partially
                // migrated database. Once normalized rows exist, reads use
                // them so page edits no longer require decoding one large
                // notebook payload.
                pages: pages.isEmpty ? blobPages : pages,
                activeRevisionID: uuid(row, "active_revision_id"),
                updatedAt: updatedAt,
                archivedAt: date(row, "archived_at"),
                trashedAt: date(row, "trashed_at"),
                isFavorite: integer(row, "is_favorite") == 1,
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

    private func normalizedPages(documentID: UUID) throws -> [PageSummary] {
        let rows = try database.query(
            "SELECT * FROM local_pages WHERE document_id = ? AND is_deleted = 0 ORDER BY page_index ASC",
            bindings: [.text(documentID.uuidString)]
        )
        return try rows.map { row in
            guard let pageID = uuid(row, "page_id"),
                  let pageIndex = integer(row, "page_index"),
                  let width = integer(row, "width"),
                  let height = integer(row, "height"),
                  let dpi = integer(row, "dpi"),
                  let coordinateSpace = text(row, "coordinate_space") else {
                throw LocalStoreError.corruptPayload
            }
            return PageSummary(
                pageID: pageID,
                pageIndex: Int(pageIndex),
                title: text(row, "title"),
                imageID: uuid(row, "image_id"),
                width: Int(width),
                height: Int(height),
                dpi: Int(dpi),
                coordinateSpace: coordinateSpace,
                paperTemplate: text(row, "paper_template")
            )
        }
    }

    private func decodePageRevision(_ row: [String: SQLiteValue]) throws -> LocalPageRevisionRecord {
        guard
            let revisionID = uuid(row, "revision_id"),
            let documentID = uuid(row, "document_id"),
            let pageID = uuid(row, "page_id"),
            let sequence = integer(row, "local_sequence"),
            let pkDrawingPath = text(row, "pkdrawing_path"),
            let pkDrawingSHA256 = text(row, "pkdrawing_sha256"),
            let inkBundlePath = text(row, "ink_bundle_path"),
            let inkBundleSHA256 = text(row, "ink_bundle_sha256"),
            let createdAt = date(row, "created_at"),
            let uploadStateValue = text(row, "upload_state"),
            let uploadState = LocalTransferState(rawValue: uploadStateValue)
        else { throw LocalStoreError.corruptPayload }
        return LocalPageRevisionRecord(
            revisionID: revisionID,
            documentID: documentID,
            pageID: pageID,
            localSequence: Int(sequence),
            pkDrawingPath: pkDrawingPath,
            pkDrawingSHA256: pkDrawingSHA256,
            inkBundlePath: inkBundlePath,
            inkBundleSHA256: inkBundleSHA256,
            thumbnailPath: text(row, "thumbnail_path"),
            createdAt: createdAt,
            committedAt: date(row, "committed_at"),
            uploadedAt: date(row, "uploaded_at"),
            uploadState: uploadState,
            attemptCount: Int(integer(row, "attempt_count") ?? 0),
            lastError: text(row, "last_error"),
            baseServerRevision: integer(row, "base_server_revision").map(Int.init)
        )
    }

    private func decodeTransferJob(_ row: [String: SQLiteValue]) throws -> LocalTransferJob {
        guard
            let transferID = uuid(row, "transfer_id"),
            let documentID = uuid(row, "document_id"),
            let kind = text(row, "kind"),
            let stateValue = text(row, "state"),
            let state = LocalTransferState(rawValue: stateValue),
            let createdAt = date(row, "created_at"),
            let updatedAt = date(row, "updated_at")
        else { throw LocalStoreError.corruptPayload }
        return LocalTransferJob(
            transferID: transferID,
            documentID: documentID,
            pageID: uuid(row, "page_id"),
            revisionID: uuid(row, "revision_id"),
            kind: kind,
            sourcePath: text(row, "source_path"),
            state: state,
            attemptCount: Int(integer(row, "attempt_count") ?? 0),
            nextAttemptAt: date(row, "next_attempt_at"),
            lastError: text(row, "last_error"),
            createdAt: createdAt,
            updatedAt: updatedAt
        )
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
        let result = StoredSyncConflict(
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
