import Foundation

public enum SyncState: Equatable, Sendable {
    case idle
    case syncing
    case online(lastSyncedAt: Date)
    case offline(reason: String)
    case failed(reason: String)
}

public struct SyncRunSummary: Equatable, Sendable {
    public let acknowledgedOperations: Int
    public let conflicts: Int
    public let pulledChanges: Int
    public let finishedAt: Date

    public init(
        acknowledgedOperations: Int,
        conflicts: Int,
        pulledChanges: Int,
        finishedAt: Date = Date()
    ) {
        self.acknowledgedOperations = acknowledgedOperations
        self.conflicts = conflicts
        self.pulledChanges = pulledChanges
        self.finishedAt = finishedAt
    }
}

public enum SyncEngineError: Error, Equatable, Sendable {
    case alreadyRunning
    case noSession
}

/// Coordinates local-first mutations with the server-sequenced sync API.
///
/// At most one operation per document is pushed in a batch. That preserves
/// the server's optimistic `base_sync_version` contract while allowing
/// unrelated documents to sync together. Accepted operations rebase the next
/// local operation in the same document; conflicts remain durable until a
/// user-visible decision is recorded.
public actor SyncEngine {
    private let localStore: any LocalNotesStore
    private let service: any SyncService
    public let clientID: String
    private var state: SyncState = .idle

    public init(
        localStore: any LocalNotesStore,
        service: any SyncService,
        clientID: String
    ) {
        self.localStore = localStore
        self.service = service
        self.clientID = clientID
    }

    public func currentState() -> SyncState { state }

    public func enqueueDocumentMutation(
        documentID: UUID,
        operationType: String,
        payload: JSONDocument,
        entityID: String? = nil
    ) async throws -> PendingOperation {
        try await localStore.enqueueDocumentMutation(
            documentID: documentID,
            clientID: clientID,
            operationType: operationType,
            payload: payload,
            entityID: entityID
        )
    }

    public func pendingOperations(limit: Int = 50) async throws -> [PendingOperation] {
        try await localStore.pendingOperations(limit: limit)
    }

    public func retry(operationID: UUID) async throws {
        try await localStore.retryOperation(clientOperationID: operationID)
    }

    public func cancel(operationID: UUID) async throws {
        try await localStore.cancelOperation(clientOperationID: operationID)
    }

    public func resolveConflict(
        conflictID: UUID,
        decision: SyncConflictDecision,
        details: JSONDocument = [:]
    ) async throws {
        _ = try await service.resolveConflict(
            conflictID: conflictID,
            request: ResolveSyncConflictRequest(decision: decision, details: details)
        )
        try await localStore.resolveLocalConflict(
            conflictID: conflictID,
            decision: decision,
            details: details
        )
        if decision == .retryAsNew {
            _ = try await localStore.createRetryOperation(
                conflictID: conflictID,
                clientID: clientID
            )
        }
    }

    public func localConflicts(includeResolved: Bool = false) async throws -> [StoredSyncConflict] {
        try await localStore.conflicts(includeResolved: includeResolved)
    }

    public func syncNow() async throws -> SyncRunSummary {
        guard state != .syncing else { throw SyncEngineError.alreadyRunning }
        state = .syncing
        do {
            let acknowledged = try await pushPendingOperations()
            let pulled = try await pullChanges()
            let summary = SyncRunSummary(
                acknowledgedOperations: acknowledged.acknowledged,
                conflicts: acknowledged.conflicts,
                pulledChanges: pulled,
                finishedAt: Date()
            )
            state = .online(lastSyncedAt: summary.finishedAt)
            return summary
        } catch is CancellationError {
            state = .idle
            throw CancellationError()
        } catch let error as APIError where error == .cancelled {
            state = .idle
            throw error
        } catch let error as APIError where error.isRetryable {
            state = .offline(reason: error.localizedDescription)
            throw error
        } catch {
            state = .failed(reason: error.localizedDescription)
            throw error
        }
    }

    private func pushPendingOperations() async throws -> (acknowledged: Int, conflicts: Int) {
        var acknowledged = 0
        var conflicts = 0

        while true {
            try Task.checkCancellation()
            let pending = try await localStore.pendingOperations(limit: 50)
            guard !pending.isEmpty else { break }

            var seenDocuments: Set<UUID> = []
            let batch = pending.filter { seenDocuments.insert($0.documentID).inserted }
            guard !batch.isEmpty else { break }

            do {
                let response = try await service.push(
                    SyncPushRequest(operations: batch.map(\.request))
                )
                for accepted in response.accepted {
                    try await localStore.markOperationAccepted(
                        clientOperationID: accepted.clientOperationID,
                        documentID: accepted.documentID,
                        syncVersion: accepted.syncVersion
                    )
                    acknowledged += 1
                }
                for conflict in response.conflicts {
                    try await localStore.markOperationConflict(conflict)
                    conflicts += 1
                }

                // A well-formed server either accepts or conflicts each new
                // operation. Avoid a tight loop if a future server returns an
                // empty response for a non-empty batch.
                if response.accepted.isEmpty && response.conflicts.isEmpty {
                    break
                }
            } catch let error as APIError where error == .cancelled {
                throw error
            } catch is CancellationError {
                throw CancellationError()
            } catch {
                for operation in batch {
                    try? await localStore.markOperationFailed(
                        clientOperationID: operation.clientOperationID,
                        error: error.localizedDescription
                    )
                }
                throw error
            }
        }
        return (acknowledged, conflicts)
    }

    private func pullChanges() async throws -> Int {
        var cursor = try await localStore.syncCursor()
        var pulled = 0
        while true {
            try Task.checkCancellation()
            let response = try await service.pull(cursor: cursor, limit: 50)
            for change in response.items {
                try await localStore.applyRemoteChange(change)
                pulled += 1
            }
            let nextCursor = max(cursor, response.nextCursor)
            if nextCursor != cursor {
                try await localStore.setSyncCursor(nextCursor)
                cursor = nextCursor
            }
            if !response.hasMore || response.items.isEmpty { break }
        }
        return pulled
    }
}
