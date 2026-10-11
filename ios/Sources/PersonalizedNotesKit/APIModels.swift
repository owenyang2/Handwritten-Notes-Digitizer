import Foundation

public struct LoginRequest: Codable, Equatable, Sendable {
    public let username: String
    public let password: String

    public init(username: String, password: String) {
        self.username = username
        self.password = password
    }
}

public struct RegisterRequest: Codable, Equatable, Sendable {
    public let username: String
    public let email: String
    public let password: String

    public init(username: String, email: String, password: String) {
        self.username = username
        self.email = email
        self.password = password
    }
}

public struct PasswordResetRequest: Codable, Equatable, Sendable {
    public let email: String

    public init(email: String) {
        self.email = email
    }
}

public struct PasswordResetResponse: Codable, Equatable, Sendable {
    public let status: String
    public let debugToken: String?

    public init(status: String, debugToken: String? = nil) {
        self.status = status
        self.debugToken = debugToken
    }
}

public struct PasswordResetConfirmRequest: Codable, Equatable, Sendable {
    public let token: String
    public let newPassword: String

    public init(token: String, newPassword: String) {
        self.token = token
        self.newPassword = newPassword
    }
}

public struct PasswordResetConfirmResponse: Codable, Equatable, Sendable {
    public let status: String

    public init(status: String) {
        self.status = status
    }
}

public struct LoginResponse: Codable, Equatable, Sendable {
    public let accessToken: String
    public let tokenType: String
    public let userID: UUID

    public init(accessToken: String, tokenType: String = "bearer", userID: UUID) {
        self.accessToken = accessToken
        self.tokenType = tokenType
        self.userID = userID
    }
}

public struct MeResponse: Codable, Equatable, Sendable {
    public let userID: UUID
    public let username: String
    public let email: String
}

public struct BackendHealthResponse: Codable, Equatable, Sendable {
    public let status: String
    public let capabilities: JSONDocument
    public let modelStatus: String?
}

/// A session row from `GET /account/sessions`. This is not an auth token.
public struct AccountSessionInfo: Codable, Equatable, Sendable, Identifiable {
    public let sessionID: UUID
    public let createdAt: Date
    public let expiresAt: Date
    public let lastSeenAt: Date?
    public let revokedAt: Date?
    public let deviceName: String?
    public let clientID: String?
    public let userAgent: String?
    public let isCurrent: Bool

    public init(
        sessionID: UUID,
        createdAt: Date,
        expiresAt: Date,
        lastSeenAt: Date? = nil,
        revokedAt: Date? = nil,
        deviceName: String? = nil,
        clientID: String? = nil,
        userAgent: String? = nil,
        isCurrent: Bool
    ) {
        self.sessionID = sessionID
        self.createdAt = createdAt
        self.expiresAt = expiresAt
        self.lastSeenAt = lastSeenAt
        self.revokedAt = revokedAt
        self.deviceName = deviceName
        self.clientID = clientID
        self.userAgent = userAgent
        self.isCurrent = isCurrent
    }

    public var id: UUID { sessionID }
}

public struct PageSummary: Codable, Equatable, Hashable, Sendable, Identifiable {
    public let pageID: UUID
    public let pageIndex: Int
    public let title: String?
    public let imageID: UUID?
    public let width: Int
    public let height: Int
    public let dpi: Int
    public let coordinateSpace: String
    public let paperTemplate: String?

    public init(
        pageID: UUID,
        pageIndex: Int,
        title: String? = nil,
        imageID: UUID? = nil,
        width: Int,
        height: Int,
        dpi: Int,
        coordinateSpace: String,
        paperTemplate: String? = nil
    ) {
        self.pageID = pageID
        self.pageIndex = pageIndex
        self.title = title
        self.imageID = imageID
        self.width = width
        self.height = height
        self.dpi = dpi
        self.coordinateSpace = coordinateSpace
        self.paperTemplate = paperTemplate
    }

    public var id: UUID { pageID }
}

public struct DocumentRecord: Codable, Equatable, Hashable, Sendable, Identifiable {
    public let documentID: UUID
    public var title: String
    public let createdAt: Date
    public var imageIDs: [UUID]
    public var sourceKind: String
    public var pages: [PageSummary]
    public var activeRevisionID: UUID?
    public var updatedAt: Date
    public var archivedAt: Date?
    public var trashedAt: Date?
    public var isFavorite: Bool
    public var syncVersion: Int
    public var defaultLanguage: String
    public var courseContext: String?
    public var sourceMetadata: JSONDocument?

    public init(
        documentID: UUID,
        title: String,
        createdAt: Date = Date(),
        imageIDs: [UUID] = [],
        sourceKind: String = "images",
        pages: [PageSummary] = [],
        activeRevisionID: UUID? = nil,
        updatedAt: Date = Date(),
        archivedAt: Date? = nil,
        trashedAt: Date? = nil,
        isFavorite: Bool = false,
        syncVersion: Int = 0,
        defaultLanguage: String = "en",
        courseContext: String? = nil,
        sourceMetadata: JSONDocument? = nil
    ) {
        self.documentID = documentID
        self.title = title
        self.createdAt = createdAt
        self.imageIDs = imageIDs
        self.sourceKind = sourceKind
        self.pages = pages
        self.activeRevisionID = activeRevisionID
        self.updatedAt = updatedAt
        self.archivedAt = archivedAt
        self.trashedAt = trashedAt
        self.isFavorite = isFavorite
        self.syncVersion = syncVersion
        self.defaultLanguage = defaultLanguage
        self.courseContext = courseContext
        self.sourceMetadata = sourceMetadata
    }

    private enum CodingKeys: String, CodingKey {
        case documentID
        case title
        case createdAt
        case imageIDs
        case sourceKind
        case pages
        case activeRevisionID
        case updatedAt
        case archivedAt
        case trashedAt
        case isFavorite
        case syncVersion
        case defaultLanguage
        case courseContext
        case sourceMetadata
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        documentID = try container.decode(UUID.self, forKey: .documentID)
        title = try container.decode(String.self, forKey: .title)
        createdAt = try container.decode(Date.self, forKey: .createdAt)
        imageIDs = try container.decodeIfPresent([UUID].self, forKey: .imageIDs) ?? []
        sourceKind = try container.decodeIfPresent(String.self, forKey: .sourceKind) ?? "images"
        pages = try container.decodeIfPresent([PageSummary].self, forKey: .pages) ?? []
        activeRevisionID = try container.decodeIfPresent(UUID.self, forKey: .activeRevisionID)
        updatedAt = try container.decodeIfPresent(Date.self, forKey: .updatedAt) ?? createdAt
        archivedAt = try container.decodeIfPresent(Date.self, forKey: .archivedAt)
        trashedAt = try container.decodeIfPresent(Date.self, forKey: .trashedAt)
        isFavorite = try container.decodeIfPresent(Bool.self, forKey: .isFavorite) ?? false
        syncVersion = try container.decodeIfPresent(Int.self, forKey: .syncVersion) ?? 0
        defaultLanguage = try container.decodeIfPresent(String.self, forKey: .defaultLanguage) ?? "en"
        courseContext = try container.decodeIfPresent(String.self, forKey: .courseContext)
        sourceMetadata = try container.decodeIfPresent(JSONDocument.self, forKey: .sourceMetadata)
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(documentID, forKey: .documentID)
        try container.encode(title, forKey: .title)
        try container.encode(createdAt, forKey: .createdAt)
        try container.encode(imageIDs, forKey: .imageIDs)
        try container.encode(sourceKind, forKey: .sourceKind)
        try container.encode(pages, forKey: .pages)
        try container.encodeIfPresent(activeRevisionID, forKey: .activeRevisionID)
        try container.encode(updatedAt, forKey: .updatedAt)
        try container.encodeIfPresent(archivedAt, forKey: .archivedAt)
        try container.encodeIfPresent(trashedAt, forKey: .trashedAt)
        try container.encode(isFavorite, forKey: .isFavorite)
        try container.encode(syncVersion, forKey: .syncVersion)
        try container.encode(defaultLanguage, forKey: .defaultLanguage)
        try container.encodeIfPresent(courseContext, forKey: .courseContext)
        try container.encodeIfPresent(sourceMetadata, forKey: .sourceMetadata)
    }

    public var id: UUID { documentID }
}

public extension DocumentRecord {
    /// Organization metadata is carried in the versioned source-metadata
    /// envelope so older servers can preserve it without changing the note
    /// identity or ink payload.
    var organizationTags: [String] {
        guard case let .array(values)? = sourceMetadata?["tags"] else { return [] }
        return values.compactMap {
            guard case let .string(value) = $0 else { return nil }
            return value
        }
    }

    var organizationFolderID: String? {
        guard case let .string(value)? = sourceMetadata?["folder_id"], !value.isEmpty else {
            return nil
        }
        return value
    }
}

public struct CreateDocumentRequest: Codable, Equatable, Sendable {
    public let title: String
    public let defaultLanguage: String
    public let courseContext: String?
    public let sourceMetadata: JSONDocument?

    public init(
        title: String,
        defaultLanguage: String = "en",
        courseContext: String? = nil,
        sourceMetadata: JSONDocument? = nil
    ) {
        self.title = title
        self.defaultLanguage = defaultLanguage
        self.courseContext = courseContext
        self.sourceMetadata = sourceMetadata
    }
}

public struct UpdateDocumentRequest: Codable, Equatable, Sendable {
    public let title: String?
    public let archived: Bool?
    public let isFavorite: Bool?
    public let defaultLanguage: String?
    public let courseContext: String?
    public let expectedSyncVersion: Int?

    public init(
        title: String? = nil,
        archived: Bool? = nil,
        isFavorite: Bool? = nil,
        defaultLanguage: String? = nil,
        courseContext: String? = nil,
        expectedSyncVersion: Int? = nil
    ) {
        self.title = title
        self.archived = archived
        self.isFavorite = isFavorite
        self.defaultLanguage = defaultLanguage
        self.courseContext = courseContext
        self.expectedSyncVersion = expectedSyncVersion
    }
}

public enum DocumentLibraryView: String, Codable, CaseIterable, Sendable {
    case active
    case favorites
    case archived
    case trash
    case recent
    case imported
    case unsynced
    case needsAttention

    /// Views backed by the canonical document partitions. Derived smart views
    /// are intentionally excluded from background upload/restore scans so a
    /// document is not visited repeatedly through overlapping queries.
    public static let syncViews: [DocumentLibraryView] = [
        .active,
        .favorites,
        .archived,
        .trash,
    ]
}

public struct DocumentListQuery: Equatable, Sendable {
    public var query: String?
    public var view: DocumentLibraryView
    public var limit: Int
    public var offset: Int

    public init(
        query: String? = nil,
        view: DocumentLibraryView = .active,
        limit: Int = 50,
        offset: Int = 0
    ) {
        self.query = query
        self.view = view
        self.limit = max(1, min(limit, 100))
        self.offset = max(0, offset)
    }
}

public struct SyncOperationRequest: Codable, Equatable, Hashable, Sendable, Identifiable {
    public let clientID: String
    public let clientOperationID: UUID
    public let documentID: UUID
    public let baseSyncVersion: Int
    public let entityType: String
    public let entityID: String?
    public let operationType: String
    public let payload: JSONDocument

    public init(
        clientID: String,
        clientOperationID: UUID = UUID(),
        documentID: UUID,
        baseSyncVersion: Int,
        entityType: String,
        entityID: String? = nil,
        operationType: String,
        payload: JSONDocument = [:]
    ) {
        self.clientID = clientID
        self.clientOperationID = clientOperationID
        self.documentID = documentID
        self.baseSyncVersion = max(0, baseSyncVersion)
        self.entityType = entityType
        self.entityID = entityID
        self.operationType = operationType
        self.payload = payload
    }

    public var id: UUID { clientOperationID }
}

public struct SyncPushRequest: Codable, Equatable, Sendable {
    public let operations: [SyncOperationRequest]

    public init(operations: [SyncOperationRequest]) {
        precondition(!operations.isEmpty, "A sync push must contain at least one operation")
        precondition(operations.count <= 50, "A sync push may contain at most 50 operations")
        self.operations = operations
    }
}

public struct SyncAcceptedOperation: Codable, Equatable, Hashable, Sendable {
    public let clientID: String
    public let clientOperationID: UUID
    public let operationID: UUID
    public let serverSequence: Int
    public let documentID: UUID
    public let syncVersion: Int
    public let idempotent: Bool
}

public struct SyncConflict: Codable, Equatable, Hashable, Sendable, Identifiable {
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
    public let status: String
    public let resolution: JSONDocument?
    /// Optional for compatibility with servers that predate tombstone metadata.
    public let isTombstone: Bool?
    public let createdAt: Date
    public let resolvedAt: Date?

    public var id: UUID { conflictID }
}

public struct SyncPushResponse: Codable, Equatable, Sendable {
    public let accepted: [SyncAcceptedOperation]
    public let conflicts: [SyncConflict]
    public let currentVersions: [String: Int]
}

public struct SyncChange: Codable, Equatable, Hashable, Sendable, Identifiable {
    public let serverSequence: Int
    public let operationID: UUID
    public let documentID: UUID
    public let clientID: String
    public let clientOperationID: UUID
    public let baseSyncVersion: Int
    public let syncVersion: Int
    public let entityType: String
    public let entityID: String?
    public let operationType: String
    public let payload: JSONDocument
    public let createdAt: Date
    /// Optional for compatibility with servers that predate tombstone metadata.
    public let isTombstone: Bool?

    public var id: UUID { operationID }
}

public struct SyncPullResponse: Codable, Equatable, Sendable {
    public let items: [SyncChange]
    public let nextCursor: Int
    public let hasMore: Bool
}

public enum SyncConflictDecision: String, Codable, Sendable {
    case keepServer = "keep_server"
    case retryAsNew = "retry_as_new"
}

public struct ResolveSyncConflictRequest: Codable, Equatable, Sendable {
    public let decision: SyncConflictDecision
    public let details: JSONDocument

    public init(decision: SyncConflictDecision, details: JSONDocument = [:]) {
        self.decision = decision
        self.details = details
    }
}

public struct InkBundleUploadResponse: Codable, Equatable, Sendable {
    public let pageID: UUID
    public let schemaVersion: Int
    public let contentHash: String
    public let acceptedAt: Date?
    public let bundleID: UUID?
    public let schemaName: String?
    public let strokeCount: Int?
    public let pointCount: Int?
    public let assetID: UUID?
    public let revisionNumber: Int?
    public let parentBundleID: UUID?
    public let active: Bool?
    public let uploadState: String?
    public let idempotent: Bool?

    public init(
        pageID: UUID,
        schemaVersion: Int,
        contentHash: String,
        acceptedAt: Date? = nil,
        bundleID: UUID? = nil,
        schemaName: String? = nil,
        strokeCount: Int? = nil,
        pointCount: Int? = nil,
        assetID: UUID? = nil,
        revisionNumber: Int? = nil,
        parentBundleID: UUID? = nil,
        active: Bool? = nil,
        uploadState: String? = nil,
        idempotent: Bool? = nil
    ) {
        self.pageID = pageID
        self.schemaVersion = schemaVersion
        self.contentHash = contentHash
        self.acceptedAt = acceptedAt
        self.bundleID = bundleID
        self.schemaName = schemaName
        self.strokeCount = strokeCount
        self.pointCount = pointCount
        self.assetID = assetID
        self.revisionNumber = revisionNumber
        self.parentBundleID = parentBundleID
        self.active = active
        self.uploadState = uploadState
        self.idempotent = idempotent
    }
}

/// Immutable server-side ink revision metadata. The bytes are content-addressed
/// by `contentHash`; `active` only identifies which retained revision a page
/// currently uses for conversion/export.
public struct InkBundleRevision: Codable, Equatable, Sendable, Identifiable {
    public let bundleID: UUID
    public let pageID: UUID
    public let contentHash: String
    public let schemaName: String
    public let schemaVersion: Int
    public let strokeCount: Int
    public let pointCount: Int
    public let assetID: UUID
    public let createdAt: Date
    public let revisionNumber: Int
    public let parentBundleID: UUID?
    public let active: Bool
    public let uploadState: String

    public var id: UUID { bundleID }

    public init(
        bundleID: UUID,
        pageID: UUID,
        contentHash: String,
        schemaName: String,
        schemaVersion: Int,
        strokeCount: Int,
        pointCount: Int,
        assetID: UUID,
        createdAt: Date,
        revisionNumber: Int,
        parentBundleID: UUID? = nil,
        active: Bool,
        uploadState: String
    ) {
        self.bundleID = bundleID
        self.pageID = pageID
        self.contentHash = contentHash
        self.schemaName = schemaName
        self.schemaVersion = schemaVersion
        self.strokeCount = strokeCount
        self.pointCount = pointCount
        self.assetID = assetID
        self.createdAt = createdAt
        self.revisionNumber = revisionNumber
        self.parentBundleID = parentBundleID
        self.active = active
        self.uploadState = uploadState
    }
}

public struct InkBundleManifestResponse: Codable, Equatable, Sendable {
    public let pageID: UUID
    public let activeBundleID: UUID?
    public let revisions: [InkBundleRevision]
}

/// Exact immutable `ink_v1` bytes returned for second-client restoration.
///
/// This DTO is intentionally separate from `InkBundle`: its stored
/// `contentHash` is checked against a freshly constructed domain bundle before
/// the downloaded native drawing is allowed into the local cache.
public struct InkBundleContentResponse: Codable, Equatable, Sendable {
    public let schema: String
    public let schemaVersion: Int
    public let pageID: UUID
    public let coordinateSpace: InkCoordinateSpace
    public let strokes: [InkStroke]
    public let capture: InkCaptureCapabilities
    public let nativeDrawing: NativeDrawing?
    public let contentHash: String

    public func validatedBundle() throws -> InkBundle {
        let bundle = InkBundle(
            pageID: pageID,
            coordinateSpace: coordinateSpace,
            strokes: strokes,
            capture: capture,
            nativeDrawing: nativeDrawing
        )
        guard schema == bundle.schema,
              schemaVersion == bundle.schemaVersion,
              contentHash == bundle.contentHash else {
            throw InkCodecError.integrityFailure
        }
        try bundle.validate()
        return bundle
    }
}

/// One immutable part recorded by the authenticated resumable-upload service.
public struct ResumableUploadPart: Codable, Equatable, Sendable {
    public let partNumber: Int
    public let sizeBytes: Int
    public let sha256: String

    public init(partNumber: Int, sizeBytes: Int, sha256: String) {
        self.partNumber = partNumber
        self.sizeBytes = sizeBytes
        self.sha256 = sha256
    }
}

/// Durable server state for a multi-part source or ink upload.
public struct ResumableUploadSession: Codable, Equatable, Sendable, Identifiable {
    public let uploadID: UUID
    public let purpose: String
    public let filename: String
    public let contentType: String?
    public let expectedSize: Int
    public let expectedSHA256: String
    public let receivedSize: Int
    public let partSize: Int
    public let status: String
    public let documentID: UUID?
    public let pageID: UUID?
    public let inkBundleID: UUID?
    public let assembledRef: String?
    public let parts: [ResumableUploadPart]
    public let expiresAt: Date
    public let errorCode: String?
    public let errorMessage: String?
    public let completedAt: Date?
    public let idempotent: Bool

    public var id: UUID { uploadID }

    public init(
        uploadID: UUID,
        purpose: String,
        filename: String,
        contentType: String?,
        expectedSize: Int,
        expectedSHA256: String,
        receivedSize: Int,
        partSize: Int,
        status: String,
        documentID: UUID? = nil,
        pageID: UUID? = nil,
        inkBundleID: UUID? = nil,
        assembledRef: String? = nil,
        parts: [ResumableUploadPart] = [],
        expiresAt: Date,
        errorCode: String? = nil,
        errorMessage: String? = nil,
        completedAt: Date? = nil,
        idempotent: Bool = false
    ) {
        self.uploadID = uploadID
        self.purpose = purpose
        self.filename = filename
        self.contentType = contentType
        self.expectedSize = expectedSize
        self.expectedSHA256 = expectedSHA256
        self.receivedSize = receivedSize
        self.partSize = partSize
        self.status = status
        self.documentID = documentID
        self.pageID = pageID
        self.inkBundleID = inkBundleID
        self.assembledRef = assembledRef
        self.parts = parts
        self.expiresAt = expiresAt
        self.errorCode = errorCode
        self.errorMessage = errorMessage
        self.completedAt = completedAt
        self.idempotent = idempotent
    }
}

public struct CreateResumableUploadRequest: Codable, Equatable, Sendable {
    public let purpose: String
    public let filename: String
    public let contentType: String?
    public let expectedSize: Int
    public let expectedSHA256: String
    public let partSize: Int?
    public let idempotencyKey: UUID
    public let documentID: UUID?
    public let pageID: UUID?

    public init(
        purpose: String,
        filename: String,
        contentType: String?,
        expectedSize: Int,
        expectedSHA256: String,
        partSize: Int? = nil,
        idempotencyKey: UUID,
        documentID: UUID? = nil,
        pageID: UUID? = nil
    ) {
        self.purpose = purpose
        self.filename = filename
        self.contentType = contentType
        self.expectedSize = expectedSize
        self.expectedSHA256 = expectedSHA256
        self.partSize = partSize
        self.idempotencyKey = idempotencyKey
        self.documentID = documentID
        self.pageID = pageID
    }
}

public struct RecognitionRunRequest: Codable, Equatable, Sendable {
    public let scope: String
    public let mode: String
    public let idempotencyKey: UUID?
    public let regionIDs: [UUID]
    public let inkBundleID: UUID?

    private enum CodingKeys: String, CodingKey {
        case scope
        case mode
        case idempotencyKey
        case regionIDs
        case inkBundleID
    }

    private enum WireCodingKeys: String, CodingKey {
        case scope
        case mode
        case idempotencyKey = "idempotency_key"
        case regionIDs = "region_ids"
        case inkBundleID = "ink_bundle_id"
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        scope = try container.decode(String.self, forKey: .scope)
        mode = try container.decode(String.self, forKey: .mode)
        idempotencyKey = try container.decodeIfPresent(UUID.self, forKey: .idempotencyKey)
        regionIDs = try container.decodeIfPresent([UUID].self, forKey: .regionIDs) ?? []
        inkBundleID = try container.decodeIfPresent(UUID.self, forKey: .inkBundleID)
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: WireCodingKeys.self)
        try container.encode(scope, forKey: .scope)
        try container.encode(mode, forKey: .mode)
        try container.encodeIfPresent(idempotencyKey, forKey: .idempotencyKey)
        try container.encode(regionIDs, forKey: .regionIDs)
        try container.encodeIfPresent(inkBundleID, forKey: .inkBundleID)
    }

    public init(
        scope: String = "page",
        mode: String = "ink",
        idempotencyKey: UUID? = UUID(),
        regionIDs: [UUID] = [],
        inkBundleID: UUID? = nil
    ) {
        self.scope = scope
        self.mode = mode
        self.idempotencyKey = idempotencyKey
        self.regionIDs = regionIDs
        self.inkBundleID = inkBundleID
    }
}

public struct RecognitionRun: Codable, Equatable, Sendable, Identifiable {
    public let runID: UUID
    public let documentID: UUID
    public let pageID: UUID
    public let state: String
    public let scope: String
    public let mode: String
    public let errorMessage: String?
    public let resultIDs: [JSONValue]
    public let createdAt: Date

    private enum CodingKeys: String, CodingKey {
        case runID
        case documentID
        case pageID
        case state
        case scope
        case mode
        case errorMessage
        case resultIDs
        case createdAt
    }

    private enum WireCodingKeys: String, CodingKey {
        case runID = "run_id"
        case documentID = "document_id"
        case pageID = "page_id"
        case state
        case scope
        case mode
        case errorMessage = "error_message"
        case resultIDs = "result_ids"
        case createdAt = "created_at"
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        runID = try container.decode(UUID.self, forKey: .runID)
        documentID = try container.decode(UUID.self, forKey: .documentID)
        pageID = try container.decode(UUID.self, forKey: .pageID)
        state = try container.decode(String.self, forKey: .state)
        scope = try container.decode(String.self, forKey: .scope)
        mode = try container.decode(String.self, forKey: .mode)
        errorMessage = try container.decodeIfPresent(String.self, forKey: .errorMessage)
        resultIDs = try container.decodeIfPresent([JSONValue].self, forKey: .resultIDs) ?? []
        createdAt = try container.decode(Date.self, forKey: .createdAt)
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: WireCodingKeys.self)
        try container.encode(runID, forKey: .runID)
        try container.encode(documentID, forKey: .documentID)
        try container.encode(pageID, forKey: .pageID)
        try container.encode(state, forKey: .state)
        try container.encode(scope, forKey: .scope)
        try container.encode(mode, forKey: .mode)
        try container.encodeIfPresent(errorMessage, forKey: .errorMessage)
        try container.encode(resultIDs, forKey: .resultIDs)
        try container.encode(createdAt, forKey: .createdAt)
    }

    public var id: UUID { runID }

    public var isTerminal: Bool {
        switch state.lowercased() {
        case "complete", "completed", "failed", "cancelled", "canceled", "partial":
            return true
        default:
            return false
        }
    }

    public var isSuccessful: Bool {
        switch state.lowercased() {
        case "complete", "completed", "partial":
            return true
        default:
            return false
        }
    }
}

public struct ImportedPageResponse: Codable, Equatable, Sendable {
    public let pageID: UUID
    public let pageIndex: Int
    public let title: String?
    public let imageID: UUID?
    public let width: Int
    public let height: Int
    public let dpi: Int
    public let coordinateSpace: String
    public let paperTemplate: String?
}

public struct DocumentSourceUploadResponse: Codable, Equatable, Sendable {
    public let documentID: UUID
    public let title: String
    public let sourceKind: String
    public let sourceSHA256: String
    public let pageCount: Int
    public let pages: [ImportedPageResponse]
}

public struct AccountExportResponse: Codable, Equatable, Sendable {
    public let exportKind: String
    public let exportedAt: Date
    public let user: JSONDocument
    public let documents: [JSONDocument]
    public let imageCount: Int
    public let sessionCount: Int
    public let uploadCount: Int
}

public struct DeleteAccountRequest: Codable, Equatable, Sendable {
    public let confirmation: String

    public init(confirmation: String) {
        self.confirmation = confirmation
    }
}

public struct AccountDeletionResponse: Codable, Equatable, Sendable {
    public let jobID: UUID
    public let status: String
    public let targetCount: Int
    public let deletedCount: Int
    public let targetRefs: [JSONValue]
    public let errorMessage: String?
    public let createdAt: Date
    public let completedAt: Date?
}

public enum APIError: Error, Equatable, Sendable {
    case invalidBaseURL
    case invalidResponse
    case transport(String)
    case server(statusCode: Int, code: String?, message: String?)
    case encoding(String)
    case decoding(String)
    case cancelled

    public var isRetryable: Bool {
        switch self {
        case .transport, .server(statusCode: 408, _, _), .server(statusCode: 429, _, _):
            return true
        case let .server(statusCode, _, _):
            return statusCode >= 500
        case .cancelled, .invalidBaseURL, .invalidResponse, .encoding, .decoding:
            return false
        }
    }
}

extension APIError: LocalizedError {
    public var errorDescription: String? {
        switch self {
        case .invalidBaseURL: return "The backend address is invalid."
        case .invalidResponse: return "The backend returned an invalid response."
        case .transport(let message): return "Connection failed: \(message)"
        case .server(let statusCode, _, let message): return message ?? "The server request failed (\(statusCode))."
        case .encoding(let message): return "The request could not be prepared: \(message)"
        case .decoding(let message): return "The server response could not be read: \(message)"
        case .cancelled: return "The request was cancelled."
        }
    }
}

public protocol SyncService: Sendable {
    func push(_ request: SyncPushRequest) async throws -> SyncPushResponse
    func pull(cursor: Int, limit: Int) async throws -> SyncPullResponse
    func conflicts(includeResolved: Bool) async throws -> [SyncConflict]
    func resolveConflict(
        conflictID: UUID,
        request: ResolveSyncConflictRequest
    ) async throws -> SyncConflict
}
