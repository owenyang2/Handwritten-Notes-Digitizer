import Combine
import Foundation
import PersonalizedNotesKit
import ShareInbox
import SwiftUI

#if canImport(Network)
import Network
#endif

@MainActor
final class AppModel: ObservableObject {
    enum Route: Equatable {
        case loading
        case login
        case library
        case notebook(UUID)
    }

    @Published var route: Route = .loading
    @Published var documents: [DocumentRecord] = []
    @Published var libraryView: DocumentLibraryView = .active
    @Published var searchQuery: String = ""
    @Published var syncState: SyncState = .idle
    @Published var pendingCount: Int = 0
    @Published var errorMessage: String?
    @Published var settings: AppSettings
    @Published var bootstrapError: String?

    let store: SQLiteLocalStore?
    let client: SharedBackendClient
    let sessionController: AccountSessionController
    let syncEngine: SyncEngine?
    let clientID: String

    static func bootstrap() -> AppModel {
        let clientID = OfflineStoreBootstrap.clientID()
        let client = SharedBackendClient(baseURL: OfflineStoreBootstrap.backendURL())
        let sessionController = AccountSessionController(
            vault: OfflineStoreBootstrap.sessionVault(),
            client: client
        )
        do {
            let store = try OfflineStoreBootstrap.openStore()
            return AppModel(
                store: store,
                client: client,
                sessionController: sessionController,
                clientID: clientID,
                bootstrapError: nil
            )
        } catch {
            return AppModel(
                store: nil,
                client: client,
                sessionController: sessionController,
                clientID: clientID,
                bootstrapError: error.localizedDescription
            )
        }
    }

    init(
        store: SQLiteLocalStore?,
        client: SharedBackendClient,
        sessionController: AccountSessionController,
        clientID: String,
        bootstrapError: String?
    ) {
        self.store = store
        self.client = client
        self.sessionController = sessionController
        self.clientID = clientID
        self.bootstrapError = bootstrapError
        self.settings = AppSettings.load()
        if let store {
            self.syncEngine = SyncEngine(localStore: store, service: client, clientID: clientID)
        } else {
            self.syncEngine = nil
        }
    }

    func start() async {
        if bootstrapError != nil {
            route = .login
            return
        }
        do {
            let session = try await sessionController.restore()
            if session == nil {
                route = .login
                return
            }
            await ingestPendingShareImports()
            try await reloadLibrary()
            route = .library
            await refreshSyncStatus()
        } catch {
            errorMessage = error.localizedDescription
            route = .login
        }
    }

    func login(username: String, password: String) async {
        errorMessage = nil
        do {
            let session = try await client.login(username: username, password: password)
            try await sessionController.install(session)
            await ingestPendingShareImports()
            try await reloadLibrary()
            route = .library
            await syncNow()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func logout() async {
        try? await sessionController.logout()
        documents = []
        route = .login
    }

    func reloadLibrary() async throws {
        guard let store else { return }
        documents = try await store.listDocuments(
            DocumentListQuery(query: searchQuery.isEmpty ? nil : searchQuery, view: libraryView)
        )
        if let syncEngine {
            pendingCount = try await syncEngine.pendingOperations().count
            syncState = await syncEngine.currentState()
        }
    }

    func refreshLibrary() async {
        do {
            try await reloadLibrary()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func createNotebook(title: String) async {
        guard let store else { return }
        let trimmed = title.trimmingCharacters(in: .whitespacesAndNewlines)
        let page = PageSummary(
            pageID: UUID(),
            pageIndex: 0,
            width: 768,
            height: 1024,
            dpi: 132,
            coordinateSpace: "points"
        )
        let document = DocumentRecord(
            documentID: UUID(),
            title: trimmed.isEmpty ? "Untitled Notebook" : trimmed,
            sourceKind: "native_ink",
            pages: [page]
        )
        do {
            try await store.upsertDocuments([document])
            try await reloadLibrary()
            openNotebook(document.documentID)
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func archiveDocument(_ documentID: UUID, archived: Bool) async {
        await mutateDocument(
            documentID,
            operationType: "document.archive",
            payload: ["archived": .bool(archived)]
        )
    }

    func trashDocument(_ documentID: UUID) async {
        await mutateDocument(documentID, operationType: "document.trash", payload: [:])
    }

    func restoreDocument(_ documentID: UUID) async {
        await mutateDocument(documentID, operationType: "document.restore", payload: [:])
    }

    func openNotebook(_ documentID: UUID) {
        route = .notebook(documentID)
    }

    func closeNotebook() {
        route = .library
        Task { await refreshLibrary() }
    }

    func document(id: UUID) -> DocumentRecord? {
        documents.first { $0.documentID == id }
    }

    /// Persist the native drawing locally. Callers must not await network.
    func commitDrawingLocally(pageID: UUID, pkDrawingData: Data, width: Double, height: Double) {
        Task.detached(priority: .utility) {
            do {
                try LocalInkCache.writePKDrawing(pkDrawingData, pageID: pageID)
                let bundle = InkBundle(
                    pageID: pageID,
                    coordinateSpace: InkCoordinateSpace(
                        width: max(width, 1),
                        height: max(height, 1)
                    ),
                    strokes: [],
                    capture: InkCaptureCapabilities(
                        pressure: true,
                        azimuth: true,
                        altitude: true,
                        timing: true
                    ),
                    nativeDrawing: NativeDrawing(data: pkDrawingData)
                )
                try LocalInkCache.writeInkBundle(bundle)
            } catch {
                // Local commit failures are visible on the next launch; never
                // surface them by stalling the pencil stroke.
            }
        }
    }

    func syncNow() async {
        guard let syncEngine else { return }
        if settings.wifiOnlyUploads, await !Connectivity.allowsUnmeteredUploads() {
            syncState = .offline(reason: "Waiting for Wi-Fi")
            return
        }
        do {
            syncState = .syncing
            _ = try await syncEngine.syncNow()
            try await reloadLibrary()
        } catch {
            syncState = await syncEngine.currentState()
            errorMessage = error.localizedDescription
        }
    }

    func refreshSyncStatus() async {
        guard let syncEngine else { return }
        syncState = await syncEngine.currentState()
        pendingCount = (try? await syncEngine.pendingOperations().count) ?? pendingCount
    }

    func updateSettings(_ newSettings: AppSettings) {
        settings = newSettings
        newSettings.save()
    }

    private func mutateDocument(
        _ documentID: UUID,
        operationType: String,
        payload: JSONDocument
    ) async {
        guard let syncEngine else { return }
        do {
            _ = try await syncEngine.enqueueDocumentMutation(
                documentID: documentID,
                operationType: operationType,
                payload: payload
            )
            try await reloadLibrary()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    private func ingestPendingShareImports() async {
        guard let store else { return }
        let items: [PendingShareImport]
        do {
            items = try ShareInboxStore.listPending(allowFallback: true)
        } catch {
            return
        }
        for item in items {
            let page = PageSummary(
                pageID: UUID(),
                pageIndex: 0,
                width: 768,
                height: 1024,
                dpi: 132,
                coordinateSpace: "points"
            )
            let document = DocumentRecord(
                documentID: UUID(),
                title: item.originalFilename,
                sourceKind: item.isPDF ? "pdf_import" : "image_import",
                pages: [page],
                sourceMetadata: [
                    "share_import_id": .string(item.id.uuidString),
                    "uti": .string(item.uniformTypeIdentifier),
                    "relative_file_path": .string(item.relativeFilePath),
                ]
            )
            do {
                try await store.upsertDocuments([document])
                try ShareInboxStore.markConsumed(item, allowFallback: true)
            } catch {
                continue
            }
        }
    }
}

enum Connectivity {
    static func allowsUnmeteredUploads() async -> Bool {
        #if canImport(Network)
        let path = await currentPath()
        if path.usesInterfaceType(.wifi)
            || path.usesInterfaceType(.wiredEthernet)
            || path.usesInterfaceType(.loopback) {
            return true
        }
        return !path.isExpensive && path.status == .satisfied
        #else
        return true
        #endif
    }

    #if canImport(Network)
    private static func currentPath() async -> NWPath {
        await withCheckedContinuation { continuation in
            let monitor = NWPathMonitor()
            let queue = DispatchQueue(label: "personalized-notes.connectivity")
            monitor.pathUpdateHandler = { path in
                monitor.cancel()
                continuation.resume(returning: path)
            }
            monitor.start(queue: queue)
        }
    }
    #endif
}
