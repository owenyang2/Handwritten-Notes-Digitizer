import Combine
import Foundation
import PersonalizedNotesKit
import ShareInbox
import SwiftUI

#if canImport(PencilKit) && canImport(UIKit)
import PencilKit
import UIKit
#endif

#if canImport(PDFKit) && canImport(UIKit) && canImport(UniformTypeIdentifiers)
import PDFKit
import UniformTypeIdentifiers
#endif

#if canImport(Network)
import Network
#endif

#if os(iOS) && canImport(PhotosUI)
import PhotosUI
#endif

enum ImportDestination: Equatable, Sendable {
    case newNotebook
    case existingNotebook(UUID)
}

struct NotebookConversionProgress: Equatable, Sendable {
    let documentID: UUID
    var completed: Int
    let total: Int
    var failed: Int

    var summary: String {
        if failed == 0 {
            return "Converting \(completed) of \(total) pages…"
        }
        return "Converted \(completed) of \(total) pages · \(failed) failed"
    }
}

@MainActor
final class AppModel: ObservableObject {
    enum Route: Equatable {
        case loading
        case welcome
        case library
        case notebook(UUID)
    }

    @Published var route: Route = .loading
    @Published var documents: [DocumentRecord] = []
    @Published var libraryView: DocumentLibraryView = .active
    @Published var searchQuery: String = ""
    @Published var syncState: SyncState = .idle
    @Published var pendingCount: Int = 0
    @Published private(set) var hasMoreDocuments = false
    @Published private(set) var isLoadingMoreDocuments = false
    @Published private(set) var failedOperations: [PendingOperation] = []
    @Published private(set) var syncConflicts: [StoredSyncConflict] = []
    @Published var errorMessage: String?
    @Published var settings: AppSettings
    @Published var bootstrapError: String?
    @Published private(set) var isGuest = true
    @Published private(set) var pendingGuestMergeCount = 0
    @Published private(set) var isAuthenticating = false
    @Published private(set) var isRecovering = false
    @Published private(set) var isImporting = false
    @Published private(set) var importProgress: ImportProgress?
    @Published private(set) var importFailures: [ImportFailure] = []
    @Published private(set) var saveState: LocalSaveState = .saved
    @Published private(set) var conversionRuns: [UUID: RecognitionRun] = [:]
    @Published private(set) var notebookConversionProgress: NotebookConversionProgress?
    @Published private(set) var backendConnectionState: BackendConnectionState = .idle
    @Published private(set) var saveFailures: [InkSaveFailure] = []
    @Published private(set) var requestedPageID: UUID?

    private var importTask: Task<Void, Never>?

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
        self.requestedPageID = nil
        if let store {
            self.syncEngine = SyncEngine(localStore: store, service: client, clientID: clientID)
        } else {
            self.syncEngine = nil
        }
    }

    func start() async {
        if bootstrapError != nil {
            route = .welcome
            return
        }
        conversionRuns = LocalConversionRunStore.load()
        saveFailures = LocalInkFailureJournal.list()
        importFailures = LocalImportFailureJournal.list()
        if let failure = saveFailures.first {
            saveState = .failed(failure.error)
        }
        do {
            let session = OfflineStoreBootstrap.isUITesting
                ? nil
                : try await sessionController.restore()
            isGuest = session == nil
            await ingestPendingShareImports()
            try await reloadLibrary()
            await drainLocalPurgeQueue()
            route = session == nil ? .welcome : .library
            await refreshSyncStatus()
            if session != nil {
                resumeConversionRuns()
            }
        } catch {
            errorMessage = error.localizedDescription
            route = .welcome
        }
    }

    func login(username: String, password: String) async {
        await authenticate {
            try await self.client.login(username: username, password: password)
        }
    }

    func register(username: String, email: String, password: String) async {
        await authenticate {
            try await self.client.register(username: username, email: email, password: password)
        }
    }

    func requestPasswordReset(email: String) async -> PasswordResetResponse? {
        errorMessage = nil
        isRecovering = true
        defer { isRecovering = false }
        do {
            return try await client.requestPasswordReset(email: email)
        } catch {
            errorMessage = "Could not start password recovery: \(error.localizedDescription)"
            return nil
        }
    }

    func confirmPasswordReset(token: String, newPassword: String) async -> Bool {
        errorMessage = nil
        isRecovering = true
        defer { isRecovering = false }
        do {
            _ = try await client.confirmPasswordReset(token: token, newPassword: newPassword)
            return true
        } catch {
            errorMessage = "Could not reset the password: \(error.localizedDescription)"
            return false
        }
    }

    func continueOffline() async {
        isGuest = true
        errorMessage = nil
        do {
            await ingestPendingShareImports()
            try await reloadLibrary()
            route = .library
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    private func authenticate(
        _ operation: @escaping () async throws -> AccountSession
    ) async {
        errorMessage = nil
        let wasGuest = isGuest
        isAuthenticating = true
        defer { isAuthenticating = false }
        do {
            let session = try await operation()
            try await sessionController.install(session)
            isGuest = false
            await ingestPendingShareImports()
            try await reloadLibrary()
            route = .library
            if wasGuest, !documents.isEmpty {
                pendingGuestMergeCount = documents.count
            } else {
                await syncNow()
            }
            resumeConversionRuns()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func logout() async {
        try? await sessionController.logout()
        isGuest = true
        pendingGuestMergeCount = 0
        route = .welcome
    }

    func acceptGuestMerge() async {
        pendingGuestMergeCount = 0
        await syncNow()
    }

    func deferGuestMerge() {
        pendingGuestMergeCount = 0
    }

    func reloadLibrary() async throws {
        guard let store else { return }
        let query = DocumentListQuery(query: searchQuery.isEmpty ? nil : searchQuery, view: libraryView)
        let page = try await store.listDocuments(query)
        documents = page
        hasMoreDocuments = page.count == query.limit
        if let syncEngine {
            pendingCount = try await syncEngine.pendingOperations().count
            failedOperations = (try? await syncEngine.failedOperations()) ?? []
            syncConflicts = (try? await syncEngine.localConflicts()) ?? []
            syncState = await syncEngine.currentState()
        }
    }

    func loadMoreLibrary() async {
        guard let store, hasMoreDocuments, !isLoadingMoreDocuments else { return }
        isLoadingMoreDocuments = true
        defer { isLoadingMoreDocuments = false }
        do {
            let query = DocumentListQuery(
                query: searchQuery.isEmpty ? nil : searchQuery,
                view: libraryView,
                limit: 50,
                offset: documents.count
            )
            let page = try await store.listDocuments(query)
            let existing = Set(documents.map(\.documentID))
            documents.append(contentsOf: page.filter { !existing.contains($0.documentID) })
            hasMoreDocuments = page.count == query.limit
        } catch {
            errorMessage = error.localizedDescription
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
            if let syncEngine {
                _ = try await syncEngine.enqueueDocumentMutation(
                    documentID: document.documentID,
                    operationType: "document.create",
                    payload: documentCreationPayload(for: document)
                )
            }
            try await reloadLibrary()
            openNotebook(document.documentID)
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func beginImport(
        _ result: Result<[URL], Error>,
        destination: ImportDestination = .newNotebook
    ) {
        beginImport(result, cleanupURLs: [], destination: destination)
    }

    #if os(iOS) && canImport(PhotosUI)
    func importPhotos(
        _ items: [PhotosPickerItem],
        destination: ImportDestination = .newNotebook
    ) async {
        guard !items.isEmpty else { return }
        do {
            let stagedURLs = try await ImportCoordinator.stagePhotos(items)
            guard !stagedURLs.isEmpty else {
                errorMessage = "The selected photos could not be read."
                return
            }
            beginImport(
                .success(stagedURLs),
                cleanupURLs: stagedURLs,
                destination: destination
            )
        } catch is CancellationError {
            return
        } catch {
            errorMessage = "Could not prepare the selected photos: \(error.localizedDescription)"
        }
    }
    #endif

    private func beginImport(
        _ result: Result<[URL], Error>,
        cleanupURLs: [URL],
        destination: ImportDestination
    ) {
        guard importTask == nil else { return }
        importTask = Task { [weak self] in
            defer {
                cleanupURLs.forEach { try? FileManager.default.removeItem(at: $0) }
                self?.clearImportTask()
            }
            await self?.importFiles(result, destination: destination)
        }
    }

    func cancelImport() {
        importTask?.cancel()
    }

    private func clearImportTask() {
        importTask = nil
    }

    private func importFiles(
        _ result: Result<[URL], Error>,
        destination: ImportDestination
    ) async {
        guard let store else {
            errorMessage = bootstrapError ?? "Local storage is unavailable."
            return
        }
        let urls: [URL]
        switch result {
        case let .success(selected):
            urls = selected
        case let .failure(error):
            // The system file importer reports cancellation as an error. It is
            // a normal user action and should not interrupt the note editor.
            if (error as NSError).code == NSUserCancelledError { return }
            errorMessage = error.localizedDescription
            return
        }
        guard !urls.isEmpty else { return }

        isImporting = true
        importProgress = ImportProgress(total: urls.count)
        defer {
            isImporting = false
            importProgress = nil
        }
        var failures: [String] = []
        for url in urls {
            var durableURL: URL?
            var preparedDocument: DocumentRecord?
            var importWasCommitted = false
            do {
                try Task.checkCancellation()
                importProgress?.currentFilename = url.lastPathComponent
                let source = try ImportCoordinator.stageFile(url)
                durableURL = source.sourceURL
                try Task.checkCancellation()
                let document = try ImportCoordinator.makeDocument(from: source)
                preparedDocument = document
                try Task.checkCancellation()
                switch destination {
                case .newNotebook:
                    try await store.upsertDocuments([document])
                    if let syncEngine {
                        try Task.checkCancellation()
                        _ = try await syncEngine.enqueueDocumentMutation(
                            documentID: document.documentID,
                            operationType: "document.create",
                            payload: documentCreationPayload(for: document)
                        )
                    }
                case let .existingNotebook(documentID):
                    try await insertImportedPages(
                        from: document,
                        into: documentID,
                        store: store
                    )
                }
                importWasCommitted = true
                importProgress?.completed += 1
            } catch is CancellationError {
                if !importWasCommitted, let preparedDocument, case .newNotebook = destination {
                    ImportCoordinator.cleanupPreparedDocument(preparedDocument)
                }
                if !importWasCommitted, case .newNotebook = destination, let durableURL {
                    try? FileManager.default.removeItem(at: durableURL)
                }
                break
            } catch {
                if !importWasCommitted, case .newNotebook = destination, let preparedDocument {
                    ImportCoordinator.cleanupPreparedDocument(preparedDocument)
                }
                if !importWasCommitted, case .newNotebook = destination, let durableURL {
                    try? LocalImportFailureJournal.record(
                        sourceURL: durableURL,
                        originalFilename: url.lastPathComponent,
                        reason: error.localizedDescription
                    )
                    refreshImportFailures()
                }
                failures.append("\(url.lastPathComponent): \(error.localizedDescription)")
                importProgress?.failed += 1
            }
        }
        do {
            try await reloadLibrary()
        } catch {
            failures.append(error.localizedDescription)
        }
        if !failures.isEmpty {
            errorMessage = failures.joined(separator: "\n")
        }
    }

    private func insertImportedPages(
        from importedDocument: DocumentRecord,
        into destinationID: UUID,
        store: SQLiteLocalStore
    ) async throws {
        guard var destination = try await store.document(id: destinationID) else {
            throw LocalStoreError.documentNotFound
        }
        let existingPages = destination.pages.sorted { $0.pageIndex < $1.pageIndex }
        let importedPages = importedDocument.pages.sorted { $0.pageIndex < $1.pageIndex }
        guard !importedPages.isEmpty else { return }

        let shiftedPages = importedPages.enumerated().map { offset, page in
            PageSummary(
                pageID: page.pageID,
                pageIndex: existingPages.count + offset,
                title: page.title,
                imageID: page.imageID,
                width: page.width,
                height: page.height,
                dpi: page.dpi,
                coordinateSpace: page.coordinateSpace,
                paperTemplate: page.paperTemplate
            )
        }
        var metadata = destination.sourceMetadata ?? [:]
        if let importedSources = importedDocument.sourceMetadata?[PageSourceMetadata.key] {
            metadata[PageSourceMetadata.key] = PageSourceMetadata.merge(
                metadata[PageSourceMetadata.key],
                with: importedSources
            )
        }
        destination.pages = (existingPages + shiftedPages).enumerated().map { index, page in
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
        destination.sourceMetadata = metadata.isEmpty ? nil : metadata

        // Persist the complete local projection before queueing remote page
        // mutations. If the app is terminated between these steps, the source
        // and page identities remain recoverable and the queue can resume.
        try await store.upsertDocuments([destination])
        if let syncEngine {
            for page in shiftedPages {
                try Task.checkCancellation()
                _ = try await syncEngine.enqueueDocumentMutation(
                    documentID: destinationID,
                    operationType: "page.add",
                    payload: [
                        "page": pagePayload(page),
                        "page_index": .number(Double(page.pageIndex)),
                    ]
                )
            }
        }
    }

    func retryImport(_ failure: ImportFailure) {
        guard importTask == nil else { return }
        importTask = Task { [weak self] in
            defer { self?.clearImportTask() }
            await self?.retryImportFile(failure)
        }
    }

    func discardImportFailure(_ failure: ImportFailure) {
        try? FileManager.default.removeItem(at: failure.sourceURL)
        LocalImportFailureJournal.remove(id: failure.id)
        refreshImportFailures()
    }

    private func retryImportFile(_ failure: ImportFailure) async {
        guard let store else {
            errorMessage = bootstrapError ?? "Local storage is unavailable."
            return
        }
        guard FileManager.default.fileExists(atPath: failure.sourceURL.path) else {
            LocalImportFailureJournal.remove(id: failure.id)
            refreshImportFailures()
            errorMessage = "The staged source for \(failure.originalFilename) is no longer available."
            return
        }

        isImporting = true
        importProgress = ImportProgress(total: 1, currentFilename: failure.originalFilename)
        defer {
            isImporting = false
            importProgress = nil
        }

        var preparedDocument: DocumentRecord?
        var documentWasStored = false
        do {
            try Task.checkCancellation()
            let document = try ImportCoordinator.makeDocument(
                sourceURL: failure.sourceURL,
                originalFilename: failure.originalFilename
            )
            preparedDocument = document
            try Task.checkCancellation()
            try await store.upsertDocuments([document])
            documentWasStored = true
            if let syncEngine {
                try Task.checkCancellation()
                _ = try await syncEngine.enqueueDocumentMutation(
                    documentID: document.documentID,
                    operationType: "document.create",
                    payload: documentCreationPayload(for: document)
                )
            }
            LocalImportFailureJournal.remove(id: failure.id)
            refreshImportFailures()
            importProgress?.completed = 1
            try await reloadLibrary()
        } catch is CancellationError {
            if !documentWasStored, let preparedDocument {
                ImportCoordinator.cleanupPreparedDocument(preparedDocument)
            }
            return
        } catch {
            if !documentWasStored, let preparedDocument {
                ImportCoordinator.cleanupPreparedDocument(preparedDocument)
            }
            try? LocalImportFailureJournal.update(id: failure.id, reason: error.localizedDescription)
            refreshImportFailures()
            importProgress?.failed = 1
            errorMessage = "Could not retry \(failure.originalFilename): \(error.localizedDescription)"
        }
    }

    private func refreshImportFailures() {
        importFailures = LocalImportFailureJournal.list()
    }

    func archiveDocument(_ documentID: UUID, archived: Bool) async {
        await mutateDocument(
            documentID,
            operationType: "document.archive",
            payload: ["archived": .bool(archived)]
        )
    }

    func renameDocument(_ documentID: UUID, title: String) async {
        let trimmed = title.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else {
            errorMessage = "A notebook title cannot be empty."
            return
        }
        await mutateDocument(
            documentID,
            operationType: "document.rename",
            payload: ["title": .string(String(trimmed.prefix(255)))]
        )
    }

    func toggleFavorite(_ documentID: UUID) async {
        guard let document = document(id: documentID) else { return }
        await mutateDocument(
            documentID,
            operationType: "document.favorite",
            payload: ["favorite": .bool(!document.isFavorite)]
        )
    }

    func updateTags(_ documentID: UUID, rawValue: String) async {
        let tags = rawValue
            .split(separator: ",")
            .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
            .filter { !$0.isEmpty }
            .reduce(into: [String]()) { result, value in
                let normalized = String(value.prefix(64))
                guard !result.contains(where: { $0.caseInsensitiveCompare(normalized) == .orderedSame }) else {
                    return
                }
                result.append(normalized)
            }
        let limitedTags = Array(tags.prefix(20))
        await mutateDocument(
            documentID,
            operationType: "document.tags",
            payload: [
                "tags": .array(limitedTags.map(JSONValue.string)),
            ]
        )
    }

    func moveToFolder(_ documentID: UUID, rawValue: String) async {
        let parts = rawValue
            .split(separator: "/")
            .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
            .filter { !$0.isEmpty }
        guard parts.count <= 4,
              parts.allSatisfy({ $0.count <= 64 }),
              parts.joined(separator: "/").count <= 128 else {
            errorMessage = "Folders support up to four levels of at most 64 characters."
            return
        }
        let folderID = parts.joined(separator: "/")
        await mutateDocument(
            documentID,
            operationType: "document.move",
            payload: [
                "folder_id": folderID.isEmpty ? .null : .string(String(folderID.prefix(128))),
            ]
        )
    }

    func duplicateNotebook(_ documentID: UUID) async {
        guard let store, let source = document(id: documentID) else { return }
        let sortedPages = source.pages.sorted { $0.pageIndex < $1.pageIndex }
        var copiedSourceMetadata = source.sourceMetadata

        do {
            if let sourcePath = source.sourceMetadata?["durable_file_path"],
               case let .string(path) = sourcePath,
               FileManager.default.fileExists(atPath: path) {
                let durableCopy = try DurableImportStore.copy(
                    sourceURL: URL(fileURLWithPath: path),
                    originalFilename: source.sourceMetadata?["original_filename"].flatMap {
                        if case let .string(filename) = $0 { return filename }
                        return nil
                    } ?? URL(fileURLWithPath: path).lastPathComponent
                )
                copiedSourceMetadata?["durable_file_path"] = .string(durableCopy.path)
                copiedSourceMetadata?["server_source_uploaded"] = nil
                copiedSourceMetadata?["server_source_sha256"] = nil
            }

            let copiedPages = sortedPages.enumerated().map { index, page in
                PageSummary(
                    pageID: UUID(),
                    pageIndex: index,
                    title: page.title,
                    imageID: page.imageID,
                    width: page.width,
                    height: page.height,
                    dpi: page.dpi,
                    coordinateSpace: page.coordinateSpace
                )
            }
            for (sourcePage, copiedPage) in zip(sortedPages, copiedPages) {
                try LocalInkCache.copyDrawing(from: sourcePage.pageID, to: copiedPage.pageID)
                try LocalInkCache.copyInkBundle(from: sourcePage.pageID, to: copiedPage.pageID)
            }

            var title = "Copy of \(source.title)"
            if title.count > 255 {
                title = String(title.prefix(255))
            }
            let copy = DocumentRecord(
                documentID: UUID(),
                title: title,
                imageIDs: [],
                sourceKind: source.sourceKind,
                pages: copiedPages,
                defaultLanguage: source.defaultLanguage,
                courseContext: source.courseContext,
                sourceMetadata: copiedSourceMetadata
            )
            try await store.upsertDocuments([copy])
            if let syncEngine {
                _ = try await syncEngine.enqueueDocumentMutation(
                    documentID: copy.documentID,
                    operationType: "document.create",
                    payload: documentCreationPayload(for: copy)
                )
            }
            try await reloadLibrary()
        } catch {
            errorMessage = "Could not duplicate notebook: \(error.localizedDescription)"
        }
    }

    func trashDocument(_ documentID: UUID) async {
        await mutateDocument(documentID, operationType: "document.trash", payload: [:])
    }

    func permanentlyDeleteDocument(_ documentID: UUID) async {
        guard let document = document(id: documentID) else { return }
        guard document.trashedAt != nil else {
            errorMessage = "Move the notebook to Trash before deleting it permanently."
            return
        }
        await mutateDocument(documentID, operationType: "document.delete", payload: [:])
    }

    func archiveDocuments(_ documentIDs: [UUID], archived: Bool) async {
        for documentID in documentIDs {
            await archiveDocument(documentID, archived: archived)
        }
    }

    func trashDocuments(_ documentIDs: [UUID]) async {
        for documentID in documentIDs {
            await trashDocument(documentID)
        }
    }

    func permanentlyDeleteDocuments(_ documentIDs: [UUID]) async {
        for documentID in documentIDs {
            await permanentlyDeleteDocument(documentID)
        }
    }

    func toggleFavorites(_ documentIDs: [UUID]) async {
        for documentID in documentIDs {
            await toggleFavorite(documentID)
        }
    }

    func restoreDocument(_ documentID: UUID) async {
        await mutateDocument(documentID, operationType: "document.restore", payload: [:])
    }

    func openNotebook(_ documentID: UUID, pageID: UUID? = nil) {
        requestedPageID = pageID
        route = .notebook(documentID)
    }

    func closeNotebook() {
        requestedPageID = nil
        route = .library
        Task { await refreshLibrary() }
    }

    func document(id: UUID) -> DocumentRecord? {
        documents.first { $0.documentID == id }
    }

    /// Persist both the lossless native drawing and the app-neutral canonical
    /// bundle locally. Callers never await network work from the Pencil path.
    func commitDrawingLocally(pageID: UUID, pkDrawingData: Data, width: Double, height: Double) {
        saveState = .saving
        Task.detached(priority: .utility) { [weak self] in
            do {
                #if canImport(PencilKit) && canImport(UIKit)
                let drawing = try PKDrawing(data: pkDrawingData)
                let bundle = try PencilKitInkAdapter.makeBundle(
                    drawing: drawing,
                    pageID: pageID,
                    width: max(width, 1),
                    height: max(height, 1),
                    nativeDrawingData: pkDrawingData
                )
                #else
                let bundle = InkBundle(
                    pageID: pageID,
                    coordinateSpace: InkCoordinateSpace(
                        width: max(width, 1),
                        height: max(height, 1)
                    ),
                    strokes: [],
                    capture: InkCaptureCapabilities(source: "unknown"),
                    nativeDrawing: pkDrawingData.isEmpty ? nil : NativeDrawing(data: pkDrawingData)
                )
                #endif
                let metadata = try await InkCommitQueue.shared.commit(
                    pageID: pageID,
                    pkDrawingData: pkDrawingData,
                    bundle: bundle
                )
                await self?.recordLocalRevision(
                    pageID: pageID,
                    metadata: metadata
                )
                await self?.markSaveSucceeded()
            } catch {
                await self?.refreshSaveFailures()
                await self?.setSaveState(.failed(error.localizedDescription))
            }
        }
    }

    private func setSaveState(_ state: LocalSaveState) {
        saveState = state
    }

    private func refreshSaveFailures() {
        saveFailures = LocalInkFailureJournal.list()
    }

    private func markSaveSucceeded() {
        refreshSaveFailures()
        if let failure = saveFailures.first {
            saveState = .failed(failure.error)
        } else {
            saveState = .saved
        }
    }

    private func recordLocalRevision(
        pageID: UUID,
        metadata: LocalInkRevisionMetadata
    ) async {
        guard let store,
              let documentID = try? await store.documentID(containingPage: pageID) else {
            return
        }
        let committedAt = Date()
        let revision = LocalPageRevisionRecord(
            revisionID: metadata.revisionID,
            documentID: documentID,
            pageID: pageID,
            pkDrawingPath: metadata.pkDrawingPath,
            pkDrawingSHA256: metadata.pkDrawingSHA256,
            inkBundlePath: metadata.inkBundlePath,
            inkBundleSHA256: metadata.inkBundleSHA256,
            createdAt: metadata.createdAt,
            committedAt: committedAt,
            uploadState: .pending
        )
        let transfer = LocalTransferJob(
            transferID: metadata.revisionID,
            documentID: documentID,
            pageID: pageID,
            revisionID: metadata.revisionID,
            kind: "ink",
            sourcePath: metadata.inkBundlePath,
            createdAt: committedAt,
            updatedAt: committedAt
        )
        try? await store.recordPageRevisionAndEnqueueTransfer(revision, transfer: transfer)
    }

    func retryLocalSave(pageID: UUID) async {
        guard let page = documents
            .flatMap(\.pages)
            .first(where: { $0.pageID == pageID }) else {
            errorMessage = "The page for this save failure is no longer available."
            return
        }
        guard let snapshot = LocalInkFailureJournal.recoverySnapshot(pageID: pageID)
                ?? LocalInkCache.readPKDrawing(pageID: pageID).flatMap({ drawingData in
                    guard let bundle = LocalInkCache.readInkBundle(pageID: pageID) else { return nil }
                    return (drawingData, bundle)
                }) else {
            errorMessage = "The failed save snapshot is no longer available. Write a new stroke to retry."
            return
        }
        commitDrawingLocally(
            pageID: pageID,
            pkDrawingData: snapshot.drawingData,
            width: Double(page.width),
            height: Double(page.height)
        )
    }

    func syncNow() async {
        guard let syncEngine else { return }
        guard await sessionController.session() != nil else {
            syncState = .offline(reason: "Sign in to sync this iPad")
            return
        }
        if settings.wifiOnlyUploads, await !Connectivity.allowsUnmeteredUploads() {
            syncState = .offline(reason: "Waiting for Wi-Fi")
            return
        }
        do {
            syncState = .syncing
            _ = try await syncEngine.syncNow()
            await drainLocalPurgeQueue()
            try await restoreMissingRemoteDocuments()
            try await restoreRemoteInk()
            try await restoreMissingSources()
            try await uploadPendingSources()
            try await uploadPendingInk()
            try await reloadLibrary()
            await drainLocalPurgeQueue()
        } catch {
            await drainLocalPurgeQueue()
            await refreshSyncStatus()
            if let apiError = error as? APIError,
               case .server(statusCode: 401, _, _) = apiError {
                try? await sessionController.logout()
                isGuest = true
                syncState = .offline(reason: "Sign-in expired; notes remain on this iPad")
            } else {
                syncState = .failed(reason: error.localizedDescription)
            }
            errorMessage = error.localizedDescription
        }
    }

    /// Reclaims filesystem assets only after SQLite has durably recorded that
    /// the server accepted (or another device announced) a permanent delete.
    /// The queue is intentionally drained on launch and after every sync so a
    /// crash between database acknowledgement and file removal is harmless.
    private func drainLocalPurgeQueue() async {
        guard let store else { return }
        do {
            for record in try await store.pendingPurgeRecords() {
                for pageID in record.pageIDs {
                    LocalInkCache.remove(pageID: pageID)
                    LocalInkFailureJournal.clear(pageID: pageID)
                }
                if let sourcePath = record.sourcePath {
                    try DurableImportStore.removeOwnedFile(at: sourcePath)
                }
                try await store.acknowledgePurge(documentID: record.documentID)
            }
            for record in try await store.pendingPagePurgeRecords() {
                LocalInkCache.remove(pageID: record.pageID)
                LocalInkFailureJournal.clear(pageID: record.pageID)
                try await store.acknowledgePagePurge(pageID: record.pageID)
            }
            refreshSaveFailures()
        } catch {
            // Keep the queue entry for the next launch/sync if the filesystem
            // is temporarily unavailable. The notebook is already gone from
            // the local projection, so this is a cleanup retry, not data loss.
        }
    }

    func refreshSyncStatus() async {
        guard let syncEngine else { return }
        syncState = await syncEngine.currentState()
        pendingCount = (try? await syncEngine.pendingOperations().count) ?? pendingCount
        failedOperations = (try? await syncEngine.failedOperations()) ?? failedOperations
        syncConflicts = (try? await syncEngine.localConflicts()) ?? syncConflicts
    }

    func updateBackendURL(_ rawValue: String) async -> Bool {
        guard let url = OfflineStoreBootstrap.validatedBackendURL(rawValue) else {
            backendConnectionState = .failed(
                "Enter a valid http(s) server URL without credentials, a query, or a fragment."
            )
            return false
        }
        await client.setBaseURL(url)
        OfflineStoreBootstrap.saveBackendURL(url)
        backendConnectionState = .idle
        return true
    }

    func testBackendConnection() async {
        backendConnectionState = .testing
        do {
            let health = try await client.health()
            backendConnectionState = .connected(
                modelStatus: health.modelStatus ?? "unknown"
            )
        } catch {
            backendConnectionState = .failed(error.localizedDescription)
        }
    }

    /// Starts a page conversion only after the exact current canonical ink
    /// revision has been uploaded. The source drawing stays local and is never
    /// replaced by recognition output.
    func requestPageConversion(documentID: UUID, pageID: UUID, mode: String = "formula") async {
        guard !isGuest else {
            errorMessage = "Sign in to convert handwriting. Your local notes remain available offline."
            return
        }
        guard document(id: documentID)?.pages.contains(where: { $0.pageID == pageID }) == true else {
            errorMessage = "This page is no longer available in the local notebook."
            return
        }
        if let existing = conversionRuns[pageID], !existing.isTerminal {
            return
        }
        // Recognition is source-linked: do not let the backend infer from an
        // older server revision while a newer local drawing is still queued.
        // Imported pages without local ink use the attached raster source as a
        // deliberate fallback; native blank pages still fail with a useful
        // local error instead of creating an empty recognition job.
        await syncNow()
        let uploadedBundle = LocalInkCache.readInkBundle(pageID: pageID)
        if uploadedBundle?.strokes.isEmpty != false {
            guard
                let currentDocument = document(id: documentID),
                let currentPage = currentDocument.pages.first(where: { $0.pageID == pageID }),
                currentDocument.sourceKind == "pdf_import" || currentDocument.sourceKind == "image_import",
                case .bool(true)? = currentDocument.sourceMetadata?["server_source_uploaded"],
                PageSourceMetadata.descriptor(for: currentDocument, page: currentPage) != nil
            else {
                errorMessage = "Add handwriting or sync an imported page before converting it."
                return
            }
            do {
                let run = try await client.createRecognitionRun(
                    pageID: pageID,
                    request: RecognitionRunRequest(
                        scope: "page",
                        mode: mode,
                        idempotencyKey: UUID()
                    )
                )
                conversionRuns[pageID] = run
                persistConversionRuns()
                await monitorRecognitionRun(run)
            } catch {
                errorMessage = "Could not start raster conversion: \(error.localizedDescription)"
            }
            return
        }
        guard let uploadedBundle,
              LocalInkCache.isInkUploaded(
                  pageID: pageID,
                  contentHash: uploadedBundle.contentHash
              ) else {
            errorMessage = "Sync this page before converting it so the exact ink revision is preserved."
            return
        }

        do {
            let manifest = try await client.inkBundleManifest(pageID: pageID)
            guard let revision = manifest.revisions.first(where: { $0.contentHash == uploadedBundle.contentHash }) else {
                errorMessage = "The synced ink revision is not available on the server yet. Try again in a moment."
                return
            }
            let run = try await client.createRecognitionRun(
                pageID: pageID,
                request: RecognitionRunRequest(
                    scope: "page",
                    mode: mode,
                    idempotencyKey: UUID(),
                    inkBundleID: revision.bundleID
                )
            )
            conversionRuns[pageID] = run
            persistConversionRuns()
            await monitorRecognitionRun(run)
        } catch {
            errorMessage = "Could not start handwriting conversion: \(error.localizedDescription)"
        }
    }

    /// Starts a bounded notebook conversion by submitting each page through
    /// the same exact-revision path as the single-page action. The limit keeps
    /// an accidental tap from creating an unbounded foreground job, while the
    /// per-page persisted runs allow relaunch/resume and preserve partial
    /// success when a model or page fails.
    func requestNotebookConversion(documentID: UUID) async {
        guard notebookConversionProgress == nil else { return }
        guard document(id: documentID) != nil else {
            errorMessage = "This notebook is no longer available locally."
            return
        }
        guard !isGuest else {
            errorMessage = "Sign in to convert handwriting. Your local notes remain available offline."
            return
        }

        // Upload any durable imported source once before selecting the work
        // list. This lets a raster-only notebook participate in the same
        // bounded per-page workflow as ink pages.
        await syncNow()
        guard let document = document(id: documentID) else {
            errorMessage = "This notebook is no longer available locally."
            return
        }
        let pages = document.pages.sorted { $0.pageIndex < $1.pageIndex }
        guard pages.count <= 100 else {
            errorMessage = "Convert at most 100 pages at a time. Select a smaller notebook before trying again."
            return
        }
        let convertiblePages = pages.filter { page in
            if let bundle = LocalInkCache.readInkBundle(pageID: page.pageID), !bundle.strokes.isEmpty {
                return true
            }
            return
                (document.sourceKind == "pdf_import" || document.sourceKind == "image_import")
                && (document.sourceMetadata?["server_source_uploaded"] == .bool(true))
                && PageSourceMetadata.descriptor(for: document, page: page) != nil
        }
        guard !convertiblePages.isEmpty else {
            errorMessage = "Add handwriting to at least one page before converting the notebook."
            return
        }

        var progress = NotebookConversionProgress(
            documentID: documentID,
            completed: 0,
            total: convertiblePages.count,
            failed: 0
        )
        notebookConversionProgress = progress
        defer { notebookConversionProgress = nil }

        for page in convertiblePages {
            guard !Task.isCancelled else { return }
            await requestPageConversion(documentID: documentID, pageID: page.pageID)
            if let run = conversionRuns[page.pageID], run.state.lowercased() == "failed" {
                progress.failed += 1
            }
            progress.completed += 1
            notebookConversionProgress = progress
        }
    }

    private func monitorRecognitionRun(_ initialRun: RecognitionRun) async {
        var latestRun = initialRun
        // The current backend may leave work queued for an external worker. A
        // bounded poll keeps the editor responsive while preserving the real
        // server state if the worker takes longer than this foreground window.
        for _ in 0..<30 {
            guard !Task.isCancelled, !latestRun.isTerminal else { return }
            do {
                try await Task.sleep(nanoseconds: 1_000_000_000)
                latestRun = try await client.getRecognitionRun(runID: latestRun.runID)
                conversionRuns[latestRun.pageID] = latestRun
                persistConversionRuns()
            } catch is CancellationError {
                return
            } catch {
                errorMessage = "Could not refresh conversion status: \(error.localizedDescription)"
                return
            }
        }
    }

    private func persistConversionRuns() {
        LocalConversionRunStore.save(Array(conversionRuns.values))
    }

    func retainConversionRun(_ run: RecognitionRun) {
        conversionRuns[run.pageID] = run
        persistConversionRuns()
    }

    private func resumeConversionRuns() {
        guard !isGuest else { return }
        for run in conversionRuns.values where !run.isTerminal {
            Task { [weak self] in
                await self?.monitorRecognitionRun(run)
            }
        }
    }

    func retrySyncOperation(_ operationID: UUID) async {
        guard let syncEngine else { return }
        do {
            try await syncEngine.retry(operationID: operationID)
            await refreshSyncStatus()
            await syncNow()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func resolveSyncConflict(_ conflictID: UUID, decision: SyncConflictDecision) async {
        guard let syncEngine else { return }
        do {
            try await syncEngine.resolveConflict(conflictID: conflictID, decision: decision)
            await refreshSyncStatus()
            if decision == .retryAsNew {
                await syncNow()
            }
        } catch {
            errorMessage = error.localizedDescription
        }
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

    func addPage(to documentID: UUID, after pageID: UUID? = nil) async {
        guard let document = document(id: documentID) else { return }
        let pages = document.pages.sorted { $0.pageIndex < $1.pageIndex }
        let insertionIndex: Int
        if let pageID, let index = pages.firstIndex(where: { $0.pageID == pageID }) {
            insertionIndex = index + 1
        } else {
            insertionIndex = pages.count
        }
        let template = pages.first ?? PageSummary(
            pageID: UUID(), pageIndex: 0, width: 768, height: 1024, dpi: 132, coordinateSpace: "points"
        )
        let page = PageSummary(
            pageID: UUID(),
            pageIndex: insertionIndex,
            title: nil,
            width: template.width,
            height: template.height,
            dpi: template.dpi,
            coordinateSpace: template.coordinateSpace,
            paperTemplate: template.paperTemplate
        )
        await enqueuePageMutation(
            documentID: documentID,
            operationType: "page.add",
            page: page,
            targetIndex: insertionIndex
        )
    }

    func duplicatePage(documentID: UUID, pageID: UUID) async {
        guard let document = document(id: documentID),
              let sourceIndex = document.pages.firstIndex(where: { $0.pageID == pageID }) else { return }
        let source = document.pages[sourceIndex]
        let copy = PageSummary(
            pageID: UUID(),
            pageIndex: source.pageIndex + 1,
            title: source.title,
            width: source.width,
            height: source.height,
            dpi: source.dpi,
            coordinateSpace: source.coordinateSpace,
            paperTemplate: source.paperTemplate
        )
        do {
            try LocalInkCache.copyDrawing(from: source.pageID, to: copy.pageID)
            try LocalInkCache.copyInkBundle(from: source.pageID, to: copy.pageID)
        } catch {
            errorMessage = "Could not duplicate the page safely: \(error.localizedDescription)"
            return
        }
        await enqueuePageMutation(
            documentID: documentID,
            operationType: "page.add",
            page: copy,
            targetIndex: copy.pageIndex
        )
    }

    func deletePage(documentID: UUID, pageID: UUID) async {
        guard let document = document(id: documentID), document.pages.count > 1 else {
            errorMessage = "A notebook must keep at least one page."
            return
        }
        await mutateDocument(
            documentID,
            operationType: "page.delete",
            payload: ["page_id": .string(pageID.uuidString)]
        )
    }

    func movePage(documentID: UUID, pageID: UUID, by offset: Int) async {
        guard let document = document(id: documentID) else { return }
        let pages = document.pages.sorted { $0.pageIndex < $1.pageIndex }
        guard let current = pages.firstIndex(where: { $0.pageID == pageID }) else { return }
        let target = min(max(current + offset, 0), pages.count - 1)
        guard current != target else { return }
        await mutateDocument(
            documentID,
            operationType: "page.reorder",
            payload: [
                "page_id": .string(pageID.uuidString),
                "page_index": .number(Double(target)),
            ]
        )
    }

    func setPageTemplate(documentID: UUID, pageID: UUID, template: PagePaperTemplate?) async {
        await mutateDocument(
            documentID,
            operationType: "page.template",
            payload: [
                "page_id": .string(pageID.uuidString),
                "paper_template": template.map { .string($0.rawValue) } ?? .null,
            ]
        )
    }

    func renamePage(documentID: UUID, pageID: UUID, title: String?) async {
        let normalizedTitle = title?.trimmingCharacters(in: .whitespacesAndNewlines)
        guard normalizedTitle == nil || (normalizedTitle?.isEmpty == false) else {
            errorMessage = "Page title cannot be blank."
            return
        }
        guard (normalizedTitle?.count ?? 0) <= 255 else {
            errorMessage = "Page title must be 255 characters or fewer."
            return
        }
        await mutateDocument(
            documentID,
            operationType: "page.rename",
            payload: [
                "page_id": .string(pageID.uuidString),
                "title": normalizedTitle.map(JSONValue.string) ?? .null,
            ]
        )
    }

    func resizePage(documentID: UUID, pageID: UUID, width: Int, height: Int) async {
        guard (128...32_768).contains(width), (128...32_768).contains(height) else {
            errorMessage = "Page dimensions must be between 128 and 32,768 points."
            return
        }
        await mutateDocument(
            documentID,
            operationType: "page.resize",
            payload: [
                "page_id": .string(pageID.uuidString),
                "width": .number(Double(width)),
                "height": .number(Double(height)),
            ]
        )
    }

    private func enqueuePageMutation(
        documentID: UUID,
        operationType: String,
        page: PageSummary,
        targetIndex: Int
    ) async {
        await mutateDocument(
            documentID,
            operationType: operationType,
            payload: [
                "page": pagePayload(page),
                "page_index": .number(Double(targetIndex)),
            ]
        )
    }

    private func documentCreationPayload(for document: DocumentRecord) -> JSONDocument {
        var payload: JSONDocument = [
            "title": .string(document.title),
            "default_language": .string(document.defaultLanguage),
            "source_kind": .string(document.sourceKind),
            "is_favorite": .bool(document.isFavorite),
            "pages": .array(document.pages.sorted { $0.pageIndex < $1.pageIndex }.map(pagePayload)),
        ]
        if let metadata = document.sourceMetadata {
            let shareableKeys = [
                "original_filename",
                "uti",
                "share_import_id",
                "tags",
                "folder_id",
            ]
            let shareableMetadata = metadata.filter { shareableKeys.contains($0.key) }
            if !shareableMetadata.isEmpty {
                payload["source_metadata"] = .object(shareableMetadata)
            }
        }
        return payload
    }

    private func pagePayload(_ page: PageSummary) -> JSONValue {
        .object([
            "page_id": .string(page.pageID.uuidString),
            "page_index": .number(Double(page.pageIndex)),
            "title": page.title.map { .string($0) } ?? .null,
            "width": .number(Double(page.width)),
            "height": .number(Double(page.height)),
            "dpi": .number(Double(page.dpi)),
            "coordinate_space": .string(page.coordinateSpace),
            "paper_template": page.paperTemplate.map { .string($0) } ?? .null,
        ])
    }

    private func uploadPendingInk() async throws {
        guard let store else { return }
        var allDocuments: [UUID: DocumentRecord] = [:]
        for view in DocumentLibraryView.syncViews {
            var offset = 0
            while true {
                let batch = try await store.listDocuments(
                    DocumentListQuery(view: view, limit: 100, offset: offset)
                )
                for document in batch {
                    allDocuments[document.documentID] = document
                }
                guard batch.count == 100 else { break }
                offset += batch.count
            }
        }
        for document in allDocuments.values {
            for page in document.pages {
                guard let bundle = LocalInkCache.readInkBundle(pageID: page.pageID) else {
                    continue
                }
                let alreadyUploaded = LocalInkCache.isInkUploaded(
                    pageID: page.pageID,
                    contentHash: bundle.contentHash
                )
                let metadata = LocalInkCache.revisionMetadata(pageID: page.pageID)
                if let metadata {
                    let existingRevision = try await store.pageRevisions(pageID: page.pageID)
                        .first(where: { $0.revisionID == metadata.revisionID })
                    let completedAt = alreadyUploaded ? Date() : nil
                    let transfer = LocalTransferJob(
                        transferID: metadata.revisionID,
                        documentID: document.documentID,
                        pageID: page.pageID,
                        revisionID: metadata.revisionID,
                        kind: "ink",
                        sourcePath: metadata.inkBundlePath
                    )
                    if existingRevision == nil {
                        try await store.recordPageRevisionAndEnqueueTransfer(LocalPageRevisionRecord(
                            revisionID: metadata.revisionID,
                            documentID: document.documentID,
                            pageID: page.pageID,
                            pkDrawingPath: metadata.pkDrawingPath,
                            pkDrawingSHA256: metadata.pkDrawingSHA256,
                            inkBundlePath: metadata.inkBundlePath,
                            inkBundleSHA256: metadata.inkBundleSHA256,
                            createdAt: metadata.createdAt,
                            committedAt: completedAt ?? metadata.createdAt,
                            uploadedAt: completedAt,
                            uploadState: alreadyUploaded ? .complete : .pending
                        ), transfer: transfer)
                    } else {
                        try await store.enqueueTransfer(transfer)
                    }
                    if alreadyUploaded {
                        try await store.markPageRevisionUploaded(
                            revisionID: metadata.revisionID,
                            at: completedAt ?? Date()
                        )
                        try await store.updateTransfer(
                            transferID: metadata.revisionID,
                            state: .complete,
                            error: nil,
                            nextAttemptAt: nil,
                            incrementAttempt: false
                        )
                        continue
                    }
                    try await store.updateTransfer(
                        transferID: metadata.revisionID,
                        state: .running,
                        error: nil,
                        nextAttemptAt: nil,
                        incrementAttempt: true
                    )
                } else if alreadyUploaded {
                    continue
                }
                let idempotencyKey = try await InkCommitQueue.shared.resumableUploadIdempotencyKey(
                    pageID: page.pageID,
                    contentHash: bundle.contentHash
                )
                do {
                    _ = try await client.uploadInkBundleResumable(
                        pageID: page.pageID,
                        bundle: bundle,
                        idempotencyKey: idempotencyKey
                    )
                    try await InkCommitQueue.shared.markInkUploaded(pageID: page.pageID, contentHash: bundle.contentHash)
                    if let metadata {
                        try await store.markPageRevisionUploaded(
                            revisionID: metadata.revisionID,
                            at: Date()
                        )
                        try await store.updateTransfer(
                            transferID: metadata.revisionID,
                            state: .complete,
                            error: nil,
                            nextAttemptAt: nil,
                            incrementAttempt: false
                        )
                    }
                } catch {
                    if let metadata {
                        try? await store.updateTransfer(
                            transferID: metadata.revisionID,
                            state: .retryableFailed,
                            error: error.localizedDescription,
                            nextAttemptAt: nil,
                            incrementAttempt: false
                        )
                    }
                    throw error
                }
            }
        }
    }

    /// REST imports predate the operation log. Bootstrap missing aggregates
    /// from the owner-scoped library without overwriting any local document
    /// (which may contain pending offline edits or conflict projections).
    private func restoreMissingRemoteDocuments() async throws {
        guard let store else { return }
        for view in [DocumentLibraryView.active, .archived, .trash] {
            var offset = 0
            while true {
                let batch = try await client.listDocuments(DocumentListQuery(view: view, limit: 100, offset: offset))
                var missing: [DocumentRecord] = []
                for document in batch {
                    if try await store.document(id: document.documentID) == nil { missing.append(document) }
                }
                if !missing.isEmpty { try await store.upsertDocuments(missing) }
                guard batch.count == 100 else { break }
                offset += batch.count
            }
        }
    }

    /// Hydrates the active immutable ink revision after metadata bootstrap.
    /// A pending local revision always wins: remote content is downloaded only
    /// when the page has no local ink or its current local hash was already
    /// acknowledged by the server. This prevents reconnect from overwriting
    /// offline Pencil work while still allowing a second client to reopen the
    /// exact native drawing uploaded by the first client.
    private func restoreRemoteInk() async throws {
        guard let store else { return }
        var allDocuments: [UUID: DocumentRecord] = [:]
        for view in DocumentLibraryView.syncViews {
            var offset = 0
            while true {
                let batch = try await store.listDocuments(
                    DocumentListQuery(view: view, limit: 100, offset: offset)
                )
                for document in batch {
                    allDocuments[document.documentID] = document
                }
                guard batch.count == 100 else { break }
                offset += batch.count
            }
        }

        for document in allDocuments.values {
            for page in document.pages {
                let localBundle = LocalInkCache.readInkBundle(pageID: page.pageID)
                let localRevisionID = LocalInkCache.revisionMetadata(pageID: page.pageID)?.revisionID
                let manifest = try await client.inkBundleManifest(pageID: page.pageID)
                guard let activeID = manifest.activeBundleID,
                      let activeRevision = manifest.revisions.first(where: { $0.bundleID == activeID }) else {
                    continue
                }
                if localBundle?.contentHash == activeRevision.contentHash {
                    try await InkCommitQueue.shared.markInkUploaded(
                        pageID: page.pageID,
                        contentHash: activeRevision.contentHash
                    )
                    continue
                }
                guard LocalInkCache.shouldRestoreRemoteRevision(
                    pageID: page.pageID,
                    localBundle: localBundle,
                    activeRevision: activeRevision
                ) else {
                    // Preserve the unsynced local revision. uploadPendingInk()
                    // will publish it after this bootstrap pass.
                    continue
                }

                let remoteBundle = try await client.downloadInkBundle(
                    pageID: page.pageID,
                    bundleID: activeRevision.bundleID
                )
                guard let metadata = try await InkCommitQueue.shared.installRemoteBundle(
                    remoteBundle,
                    revision: activeRevision,
                    expectedLocalRevisionID: localRevisionID,
                    expectedLocalContentHash: localBundle?.contentHash
                ) else { continue }
                let restoredAt = Date()
                let transfer = LocalTransferJob(
                    transferID: activeRevision.bundleID,
                    documentID: document.documentID,
                    pageID: page.pageID,
                    revisionID: activeRevision.bundleID,
                    kind: "ink",
                    sourcePath: metadata.inkBundlePath,
                    state: .complete,
                    createdAt: activeRevision.createdAt,
                    updatedAt: restoredAt
                )
                try await store.recordPageRevisionAndEnqueueTransfer(
                    LocalPageRevisionRecord(
                        revisionID: activeRevision.bundleID,
                        documentID: document.documentID,
                        pageID: page.pageID,
                        pkDrawingPath: metadata.pkDrawingPath,
                        pkDrawingSHA256: metadata.pkDrawingSHA256,
                        inkBundlePath: metadata.inkBundlePath,
                        inkBundleSHA256: metadata.inkBundleSHA256,
                        createdAt: activeRevision.createdAt,
                        committedAt: restoredAt,
                        uploadedAt: restoredAt,
                        uploadState: .complete,
                        baseServerRevision: activeRevision.revisionNumber
                    ),
                    transfer: transfer
                )
            }
        }
    }

    private func uploadPendingSources() async throws {
        guard let store else { return }
        var allDocuments: [UUID: DocumentRecord] = [:]
        for view in DocumentLibraryView.syncViews {
            var offset = 0
            while true {
                let batch = try await store.listDocuments(
                    DocumentListQuery(view: view, limit: 100, offset: offset)
                )
                for document in batch {
                    allDocuments[document.documentID] = document
                }
                guard batch.count == 100 else { break }
                offset += batch.count
            }
        }

        for document in allDocuments.values {
            guard document.sourceKind == "pdf_import" || document.sourceKind == "image_import",
                  case let .string(path)? = document.sourceMetadata?["durable_file_path"],
                  FileManager.default.fileExists(atPath: path) else {
                continue
            }
            if case .bool(true)? = document.sourceMetadata?["server_source_uploaded"] {
                try await store.enqueueTransfer(LocalTransferJob(
                    transferID: document.documentID,
                    documentID: document.documentID,
                    kind: "source",
                    sourcePath: path
                ))
                try await store.updateTransfer(
                    transferID: document.documentID,
                    state: .complete,
                    error: nil,
                    nextAttemptAt: nil,
                    incrementAttempt: false
                )
                continue
            }

            let sourceURL = URL(fileURLWithPath: path)
            let originalFilename: String
            if case let .string(filename)? = document.sourceMetadata?["original_filename"], !filename.isEmpty {
                originalFilename = filename
            } else {
                originalFilename = sourceURL.lastPathComponent
            }
            let mimeType = document.sourceKind == "pdf_import"
                ? "application/pdf"
                : "image/\(sourceURL.pathExtension.lowercased() == "png" ? "png" : "jpeg")"
            try await store.enqueueTransfer(LocalTransferJob(
                transferID: document.documentID,
                documentID: document.documentID,
                kind: "source",
                sourcePath: path
            ))
            try await store.updateTransfer(
                transferID: document.documentID,
                state: .running,
                error: nil,
                nextAttemptAt: nil,
                incrementAttempt: true
            )
            do {
                let response = try await client.uploadDocumentSource(
                    documentID: document.documentID,
                    fileURL: sourceURL,
                    originalFilename: originalFilename,
                    mimeType: mimeType
                )
                let localPages = document.pages.sorted(by: { $0.pageIndex < $1.pageIndex })
                guard response.documentID == document.documentID,
                      response.pages.map(\.pageID) == localPages.map(\.pageID) else {
                    throw APIError.decoding("The server returned a different page identity set for the imported notebook.")
                }
                var updated = document
                updated.pages = response.pages.sorted { $0.pageIndex < $1.pageIndex }.map {
                    PageSummary(
                        pageID: $0.pageID,
                        pageIndex: $0.pageIndex,
                        title: $0.title,
                        imageID: $0.imageID,
                        width: $0.width,
                        height: $0.height,
                        dpi: $0.dpi,
                        coordinateSpace: $0.coordinateSpace,
                        paperTemplate: $0.paperTemplate
                    )
                }
                var metadata = updated.sourceMetadata ?? [:]
                metadata["server_source_uploaded"] = .bool(true)
                metadata["source_sha256"] = .string(response.sourceSHA256)
                updated.sourceMetadata = metadata
                try await store.upsertDocuments([updated])
                try await store.updateTransfer(
                    transferID: document.documentID,
                    state: .complete,
                    error: nil,
                    nextAttemptAt: nil,
                    incrementAttempt: false
                )
            } catch {
                try? await store.updateTransfer(
                    transferID: document.documentID,
                    state: .retryableFailed,
                    error: error.localizedDescription,
                    nextAttemptAt: nil,
                    incrementAttempt: false
                )
                throw error
            }
        }
    }

    private func restoreMissingSources() async throws {
        guard let store else { return }
        var allDocuments: [UUID: DocumentRecord] = [:]
        for view in DocumentLibraryView.syncViews {
            var offset = 0
            while true {
                let batch = try await store.listDocuments(
                    DocumentListQuery(view: view, limit: 100, offset: offset)
                )
                for document in batch {
                    allDocuments[document.documentID] = document
                }
                guard batch.count == 100 else { break }
                offset += batch.count
            }
        }

        for document in allDocuments.values {
            guard document.sourceKind == "pdf_import" || document.sourceKind == "image_import" else {
                continue
            }
            if case let .string(path)? = document.sourceMetadata?["durable_file_path"],
               FileManager.default.fileExists(atPath: path) {
                continue
            }

            let serverDocument = try await client.getDocument(document.documentID)
            guard serverDocument.sourceKind == "pdf_import" || serverDocument.sourceKind == "image_import",
                  case .bool(true)? = serverDocument.sourceMetadata?["source_attached"] else {
                continue
            }
            let downloadedURL = try await client.downloadDocumentSource(documentID: document.documentID)
            defer { try? FileManager.default.removeItem(at: downloadedURL) }
            let filename: String
            if case let .string(value)? = serverDocument.sourceMetadata?["original_filename"], !value.isEmpty {
                filename = value
            } else {
                filename = serverDocument.sourceKind == "pdf_import" ? "imported-source.pdf" : "imported-source"
            }
            let durableURL = try DurableImportStore.copy(
                sourceURL: downloadedURL,
                originalFilename: filename
            )
            let localPages = document.pages.sorted(by: { $0.pageIndex < $1.pageIndex })
            let serverPages = serverDocument.pages.sorted(by: { $0.pageIndex < $1.pageIndex })
            guard localPages.map(\.pageID) == serverPages.map(\.pageID) else {
                throw APIError.decoding("The server returned a different page identity set for the imported notebook.")
            }
            var updated = document
            updated.pages = serverPages
            updated.sourceKind = serverDocument.sourceKind
            var metadata = document.sourceMetadata ?? [:]
            for (key, value) in serverDocument.sourceMetadata ?? [:] {
                metadata[key] = value
            }
            metadata["durable_file_path"] = .string(durableURL.path)
            metadata["server_source_uploaded"] = .bool(true)
            updated.sourceMetadata = metadata
            try await store.upsertDocuments([updated])
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
            if (try? await hasImportedShareItem(item.id)) == true {
                try? ShareInboxStore.markConsumed(item, allowFallback: true)
                continue
            }
            guard let source = try? ImportCoordinator.stageShare(item) else { continue }
            var preparedDocument: DocumentRecord?
            var documentWasStored = false
            do {
                let document = try ImportCoordinator.makeDocument(from: source)
                preparedDocument = document
                try await store.upsertDocuments([document])
                documentWasStored = true
                if let syncEngine {
                    _ = try await syncEngine.enqueueDocumentMutation(
                        documentID: document.documentID,
                        operationType: "document.create",
                        payload: documentCreationPayload(for: document)
                    )
                }
                try ShareInboxStore.markConsumed(item, allowFallback: true)
            } catch {
                if !documentWasStored, let preparedDocument {
                    ImportCoordinator.cleanupPreparedDocument(preparedDocument)
                }
                continue
            }
        }
    }

    private func hasImportedShareItem(_ shareImportID: UUID) async throws -> Bool {
        guard let store else { return false }
        for view in DocumentLibraryView.syncViews {
            var offset = 0
            while true {
                let batch = try await store.listDocuments(
                    DocumentListQuery(view: view, limit: 100, offset: offset)
                )
                if batch.contains(where: { document in
                    guard case let .string(value)? = document.sourceMetadata?["share_import_id"] else {
                        return false
                    }
                    return value == shareImportID.uuidString
                }) {
                    return true
                }
                guard batch.count == 100 else { break }
                offset += batch.count
            }
        }
        return false
    }
}

actor InkCommitQueue {
    static let shared = InkCommitQueue()

    func installRemoteBundle(
        _ bundle: InkBundle,
        revision: InkBundleRevision,
        expectedLocalRevisionID: UUID?,
        expectedLocalContentHash: String?
    ) throws -> LocalInkRevisionMetadata? {
        // Recheck after the download, inside the same queue as Pencil commits.
        // This also protects a page whose first local drawing arrived in flight.
        let currentBundle = LocalInkCache.readInkBundle(pageID: bundle.pageID)
        guard LocalInkCache.revisionMetadata(pageID: bundle.pageID)?.revisionID == expectedLocalRevisionID,
              currentBundle?.contentHash == expectedLocalContentHash,
              LocalInkCache.shouldRestoreRemoteRevision(
                pageID: bundle.pageID, localBundle: currentBundle, activeRevision: revision
              ) else { return nil }
        return try LocalInkCache.installRemoteBundle(bundle, revision: revision)
    }

    func markInkUploaded(pageID: UUID, contentHash: String) throws {
        guard LocalInkCache.readInkBundle(pageID: pageID)?.contentHash == contentHash else { return }
        try LocalInkCache.markInkUploaded(pageID: pageID, contentHash: contentHash)
    }

    func resumableUploadIdempotencyKey(pageID: UUID, contentHash: String) throws -> UUID {
        try LocalInkCache.resumableUploadIdempotencyKey(pageID: pageID, contentHash: contentHash)
    }

    func commit(pageID: UUID, pkDrawingData: Data, bundle: InkBundle) throws -> LocalInkRevisionMetadata {
        // Serializing commits prevents a slower earlier drawing snapshot from
        // publishing its manifest after a newer snapshot.
        do {
            let metadata = try LocalInkCache.commit(
                pageID: pageID,
                pkDrawingData: pkDrawingData,
                bundle: bundle
            )
            LocalInkFailureJournal.clear(pageID: pageID)
            return metadata
        } catch {
            try? LocalInkFailureJournal.recordFailure(
                pageID: pageID,
                pkDrawingData: pkDrawingData,
                bundle: bundle,
                error: error
            )
            throw error
        }
    }
}

enum ImportedSourceFactory {
    static func makeDocument(
        sourceURL: URL,
        originalFilename: String,
        shareImportID: UUID? = nil,
        uniformTypeIdentifier: String? = nil
    ) throws -> DocumentRecord {
        #if canImport(PDFKit) && canImport(UIKit) && canImport(UniformTypeIdentifiers)
        let filename = URL(fileURLWithPath: originalFilename).lastPathComponent
        let title = URL(fileURLWithPath: filename).deletingPathExtension().lastPathComponent
            .trimmingCharacters(in: .whitespacesAndNewlines)
        let isPDF = uniformTypeIdentifier == UTType.pdf.identifier
            || UTType(filenameExtension: sourceURL.pathExtension)?.conforms(to: .pdf) == true

        try ImportSourcePreflight.validate(
            sourceURL: sourceURL,
            originalFilename: filename,
            uniformTypeIdentifier: uniformTypeIdentifier,
            isPDF: isPDF
        )

        var pages: [PageSummary] = []
        if isPDF {
            guard let document = PDFDocument(url: sourceURL), document.pageCount > 0 else {
                throw ImportedSourceError.unreadable
            }
            guard document.pageCount <= 50 else {
                throw ImportedSourceError.tooManyPages
            }
            pages = (0..<document.pageCount).compactMap { index in
                guard let pdfPage = document.page(at: index) else { return nil }
                let bounds = pdfPage.bounds(for: .mediaBox)
                let width = max(1, Int(ceil(abs(bounds.width))))
                let height = max(1, Int(ceil(abs(bounds.height))))
                return PageSummary(
                    pageID: UUID(),
                    pageIndex: index,
                    width: width,
                    height: height,
                    dpi: 72,
                    coordinateSpace: "pdf-points"
                )
            }
            guard pages.count == document.pageCount else { throw ImportedSourceError.unreadable }
        } else {
            guard let image = UIImage(contentsOfFile: sourceURL.path),
                  let cgImage = image.cgImage else {
                throw ImportedSourceError.unsupported
            }
            pages = [PageSummary(
                pageID: UUID(),
                pageIndex: 0,
                width: max(1, cgImage.width),
                height: max(1, cgImage.height),
                dpi: max(1, Int((image.scale * 72).rounded())),
                coordinateSpace: "image-pixels"
            )]
        }

        var metadata: JSONDocument = [
            "original_filename": .string(filename),
            "durable_file_path": .string(sourceURL.path),
            "page_sources": PageSourceMetadata.entries(
                sourceURL: sourceURL,
                sourceKind: isPDF ? "pdf_import" : "image_import",
                pages: pages,
                originalFilename: filename
            ),
        ]
        if let shareImportID {
            metadata["share_import_id"] = .string(shareImportID.uuidString)
        }
        if let uniformTypeIdentifier {
            metadata["uti"] = .string(uniformTypeIdentifier)
        }
        return DocumentRecord(
            documentID: UUID(),
            title: title.isEmpty ? "Imported note" : title,
            sourceKind: isPDF ? "pdf_import" : "image_import",
            pages: pages,
            sourceMetadata: metadata
        )
        #else
        throw ImportedSourceError.unsupported
        #endif
    }
}

enum ImportedSourceError: LocalizedError {
    case unsupported
    case unreadable
    case tooManyPages
    case tooLarge
    case insufficientStorage

    var errorDescription: String? {
        switch self {
        case .unsupported:
            return "This file is not a readable PDF or image."
        case .unreadable:
            return "The selected file could not be read."
        case .tooManyPages:
            return "This import has more than 50 pages. Split it into smaller files and try again."
        case .tooLarge:
            return "This file is larger than the 10 MB server upload limit."
        case .insufficientStorage:
            return "There is not enough free space on this iPad to stage this import safely."
        }
    }
}

struct ImportProgress: Equatable, Sendable {
    let total: Int
    var completed = 0
    var failed = 0
    var currentFilename: String?

    init(total: Int, currentFilename: String? = nil) {
        self.total = total
        self.currentFilename = currentFilename
    }

    var processed: Int { completed + failed }

    var detail: String {
        if let currentFilename {
            return "\(currentFilename) · \(processed) of \(total) processed"
        }
        return "\(processed) of \(total) processed"
    }
}

enum LocalSaveState: Equatable {
    case saved
    case saving
    case failed(String)

    var title: String {
        switch self {
        case .saved: return "Saved on iPad"
        case .saving: return "Saving…"
        case .failed: return "Needs attention"
        }
    }
}

enum BackendConnectionState: Equatable {
    case idle
    case testing
    case connected(modelStatus: String)
    case failed(String)
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
