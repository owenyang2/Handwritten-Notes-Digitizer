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

public struct PageSummary: Codable, Equatable, Hashable, Sendable, Identifiable {
    public let pageID: UUID
    public let pageIndex: Int
    public let imageID: UUID?
    public let width: Int
    public let height: Int
    public let dpi: Int
    public let coordinateSpace: String

    public init(
        pageID: UUID,
        pageIndex: Int,
        imageID: UUID? = nil,
        width: Int,
        height: Int,
        dpi: Int,
        coordinateSpace: String
    ) {
        self.pageID = pageID
        self.pageIndex = pageIndex
        self.imageID = imageID
        self.width = width
        self.height = height
        self.dpi = dpi
        self.coordinateSpace = coordinateSpace
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
        self.syncVersion = syncVersion
        self.defaultLanguage = defaultLanguage
        self.courseContext = courseContext
        self.sourceMetadata = sourceMetadata
    }

    public var id: UUID { documentID }
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
    public let defaultLanguage: String?
    public let courseContext: String?
    public let expectedSyncVersion: Int?

    public init(
        title: String? = nil,
        archived: Bool? = nil,
        defaultLanguage: String? = nil,
        courseContext: String? = nil,
        expectedSyncVersion: Int? = nil
    ) {
        self.title = title
        self.archived = archived
        self.defaultLanguage = defaultLanguage
        self.courseContext = courseContext
        self.expectedSyncVersion = expectedSyncVersion
    }
}

public enum DocumentLibraryView: String, Codable, CaseIterable, Sendable {
    case active
    case archived
    case trash
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

    public init(pageID: UUID, schemaVersion: Int, contentHash: String, acceptedAt: Date? = nil) {
        self.pageID = pageID
        self.schemaVersion = schemaVersion
        self.contentHash = contentHash
        self.acceptedAt = acceptedAt
    }
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

public protocol SyncService: Sendable {
    func push(_ request: SyncPushRequest) async throws -> SyncPushResponse
    func pull(cursor: Int, limit: Int) async throws -> SyncPullResponse
    func conflicts(includeResolved: Bool) async throws -> [SyncConflict]
    func resolveConflict(
        conflictID: UUID,
        request: ResolveSyncConflictRequest
    ) async throws -> SyncConflict
}
