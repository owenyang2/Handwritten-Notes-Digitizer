import Foundation
import CryptoKit
import PersonalizedNotesKit
import ShareInbox

/// Opens the on-device SQLite store and related local caches. Network is never
/// required for this path — a cold launch must still show the last library.
enum OfflineStoreBootstrap {
    static let clientIDDefaultsKey = "personalized-notes.client-id"
    static let backendURLDefaultsKey = "personalized-notes.backend-url"
    static let defaultBackendURL = URL(string: "http://127.0.0.1:8000")!

    static var isUITesting: Bool {
        #if DEBUG
        return ProcessInfo.processInfo.arguments.contains("--ui-testing")
        #else
        return false
        #endif
    }

    static func applicationSupportDirectory() throws -> URL {
        let directoryName: String
        #if DEBUG
        if let runID = ProcessInfo.processInfo.environment["HND_UI_TEST_RUN_ID"],
           !runID.isEmpty {
            let safeRunID = runID.filter { $0.isLetter || $0.isNumber || $0 == "-" }
            directoryName = safeRunID.isEmpty ? "PersonalizedNotesUITests" : "PersonalizedNotesUITests-\(safeRunID)"
        } else if isUITesting {
            directoryName = "PersonalizedNotesUITests"
        } else {
            directoryName = "PersonalizedNotes"
        }
        #else
        directoryName = "PersonalizedNotes"
        #endif
        let directory = FileManager.default
            .urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent(directoryName, isDirectory: true)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        return directory
    }

    static func openStore() throws -> SQLiteLocalStore {
        try SQLiteLocalStore(applicationSupportDirectory: try applicationSupportDirectory())
    }

    static func clientID() -> String {
        let defaults = UserDefaults.standard
        if let existing = defaults.string(forKey: clientIDDefaultsKey), !existing.isEmpty {
            return existing
        }
        let created = "ipad-\(UUID().uuidString.lowercased())"
        defaults.set(created, forKey: clientIDDefaultsKey)
        return created
    }

    static func backendURL() -> URL {
        let defaults = UserDefaults.standard
        if let raw = defaults.string(forKey: backendURLDefaultsKey),
           let url = validatedBackendURL(raw) {
            return url
        }
        if let bundled = Bundle.main.object(forInfoDictionaryKey: "HNDBackendURL") as? String,
           let url = validatedBackendURL(bundled) {
            return url
        }
        return defaultBackendURL
    }

    static func validatedBackendURL(_ rawValue: String) -> URL? {
        let value = rawValue.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !value.isEmpty,
              let url = URL(string: value),
              let scheme = url.scheme?.lowercased(),
              scheme == "http" || scheme == "https",
              let host = url.host,
              !host.isEmpty,
              url.user == nil,
              url.password == nil,
              url.query == nil,
              url.fragment == nil else {
            return nil
        }
        #if !DEBUG
        let isLoopback = host == "localhost"
            || host == "127.0.0.1"
            || host == "::1"
        guard scheme == "https" || isLoopback else { return nil }
        #endif
        return url
    }

    static func saveBackendURL(_ url: URL) {
        UserDefaults.standard.set(url.absoluteString, forKey: backendURLDefaultsKey)
    }

    static func sessionVault() -> any SessionVault {
        #if canImport(Security)
        return KeychainSessionVault()
        #else
        return InMemorySessionVault()
        #endif
    }
}

/// Local PKDrawing / ink_v1 files. Commits stay on disk until a later sync
/// pass decides an upload is allowed (for example Wi-Fi only).
struct LocalInkRevisionMetadata: Sendable {
    let revisionID: UUID
    let pkDrawingPath: String
    let pkDrawingSHA256: String
    let inkBundlePath: String
    let inkBundleSHA256: String
    let createdAt: Date
}

enum LocalInkCache {
    enum CommitFailurePoint: String, CaseIterable, Sendable {
        case beforeWritingDrawingTemporary
        case afterWritingDrawingTemporary
        case afterWritingBundleTemporary
        case afterPublishingDrawing
        case afterPublishingBundle
        case afterWritingManifest
    }

#if DEBUG
    private static let testCommitFailureDefaultsKey = "personalized-notes.test-commit-failure-point"

    static func setTestCommitFailurePoint(_ point: CommitFailurePoint?) {
        if let point {
            UserDefaults.standard.set(point.rawValue, forKey: testCommitFailureDefaultsKey)
        } else {
            UserDefaults.standard.removeObject(forKey: testCommitFailureDefaultsKey)
        }
    }
#endif

    private struct CommitManifest: Codable {
        let revisionID: UUID
        let pkDrawingSHA256: String
        let inkBundleSHA256: String
        let createdAt: Date?
        var uploadedContentHash: String?
        var uploadIdempotencyKey: UUID?
    }

    static func directory() throws -> URL {
        let directory = try OfflineStoreBootstrap.applicationSupportDirectory()
            .appendingPathComponent("ink", isDirectory: true)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        return directory
    }

    static func pkDrawingURL(pageID: UUID) throws -> URL {
        try directory().appendingPathComponent("\(pageID.uuidString).pkdrawing")
    }

    static func inkBundleURL(pageID: UUID) throws -> URL {
        try directory().appendingPathComponent("\(pageID.uuidString).ink.json")
    }

    static func readPKDrawing(pageID: UUID) -> Data? {
        if let commit = readValidatedCommit(pageID: pageID) {
            return commit.drawingData.isEmpty ? nil : commit.drawingData
        }
        guard let url = try? pkDrawingURL(pageID: pageID) else { return nil }
        return try? Data(contentsOf: url)
    }

    static func readInkBundle(pageID: UUID) -> InkBundle? {
        if let commit = readValidatedCommit(pageID: pageID) {
            return commit.bundle
        }
        guard let url = try? inkBundleURL(pageID: pageID),
              let data = try? Data(contentsOf: url) else { return nil }
        return try? InkV1Codec.decode(data)
    }

    static func writePKDrawing(_ data: Data, pageID: UUID) throws {
        guard !data.isEmpty else { return }
        try data.write(to: pkDrawingURL(pageID: pageID), options: [.atomic])
    }

    static func writeInkBundle(_ bundle: InkBundle) throws {
        try InkV1Codec.write(bundle, to: inkBundleURL(pageID: bundle.pageID))
    }

    /// Commits a native drawing and its canonical representation as a single
    /// versioned pair. The manifest is published last, so a crash can leave
    /// the previous manifest or the complete new revision, never a half-written
    /// pair selected for reopening.
    @discardableResult
    static func commit(
        pageID: UUID,
        pkDrawingData: Data,
        bundle: InkBundle,
        revisionID: UUID = UUID(),
        createdAt: Date = Date(),
        uploadedContentHash: String? = nil
    ) throws -> LocalInkRevisionMetadata {
        let directory = try Self.directory()
        let drawingURL = directory.appendingPathComponent("\(pageID.uuidString).\(revisionID.uuidString).pkdrawing")
        let bundleURL = directory.appendingPathComponent("\(pageID.uuidString).\(revisionID.uuidString).ink.json")
        let drawingTemporaryURL = directory.appendingPathComponent(".\(revisionID.uuidString).pkdrawing.tmp")
        let bundleTemporaryURL = directory.appendingPathComponent(".\(revisionID.uuidString).ink.json.tmp")

        // A force-quit can strand temporary files. They are never selected by
        // the manifest reader, and this cleanup also keeps injected test
        // failures from accumulating junk in the local cache.
        defer {
            try? FileManager.default.removeItem(at: drawingTemporaryURL)
            try? FileManager.default.removeItem(at: bundleTemporaryURL)
        }

        let bundleData = try InkV1Codec.encode(bundle)
        try failIfTestCommitFailure(at: .beforeWritingDrawingTemporary)
        try pkDrawingData.write(to: drawingTemporaryURL, options: [.atomic])
        try failIfTestCommitFailure(at: .afterWritingDrawingTemporary)
        try bundleData.write(to: bundleTemporaryURL, options: [.atomic])
        try failIfTestCommitFailure(at: .afterWritingBundleTemporary)
        try FileManager.default.moveItem(at: drawingTemporaryURL, to: drawingURL)
        try failIfTestCommitFailure(at: .afterPublishingDrawing)
        try FileManager.default.moveItem(at: bundleTemporaryURL, to: bundleURL)
        try failIfTestCommitFailure(at: .afterPublishingBundle)
        protect(drawingURL)
        protect(bundleURL)

        let manifest = CommitManifest(
            revisionID: revisionID,
            pkDrawingSHA256: Self.sha256(pkDrawingData),
            inkBundleSHA256: Self.sha256(bundleData),
            createdAt: createdAt,
            uploadedContentHash: uploadedContentHash,
            uploadIdempotencyKey: nil
        )
        let manifestData = try APIJSON.encoder().encode(manifest)
        let manifestURL = directory.appendingPathComponent("\(pageID.uuidString).manifest.json")
        try manifestData.write(to: manifestURL, options: [.atomic])
        try failIfTestCommitFailure(at: .afterWritingManifest)
        protect(manifestURL)
        return LocalInkRevisionMetadata(
            revisionID: revisionID,
            pkDrawingPath: drawingURL.path,
            pkDrawingSHA256: manifest.pkDrawingSHA256,
            inkBundlePath: bundleURL.path,
            inkBundleSHA256: manifest.inkBundleSHA256,
            createdAt: manifest.createdAt ?? Date()
        )
    }

    /// Publishes a server revision into the same crash-safe local commit path
    /// used for Pencil input. The native bytes are required because canonical
    /// strokes alone cannot reproduce every PencilKit rendering detail.
    static func installRemoteBundle(
        _ bundle: InkBundle,
        revision: InkBundleRevision
    ) throws -> LocalInkRevisionMetadata {
        guard bundle.pageID == revision.pageID,
              bundle.contentHash == revision.contentHash,
              let nativeDrawing = bundle.nativeDrawing,
              nativeDrawing.platform == "apple.pkdrawing",
              !nativeDrawing.data.isEmpty else {
            throw InkCodecError.invalidNativeDrawing
        }
        #if canImport(PencilKit)
        _ = try PencilKitDrawingCodec.decode(nativeDrawing)
        #endif
        return try commit(
            pageID: bundle.pageID,
            pkDrawingData: nativeDrawing.data,
            bundle: bundle,
            revisionID: revision.bundleID,
            createdAt: revision.createdAt,
            uploadedContentHash: bundle.contentHash
        )
    }

    static func revisionMetadata(pageID: UUID) -> LocalInkRevisionMetadata? {
        guard let directory = try? directory() else {
            return nil
        }
        let manifestURL = directory.appendingPathComponent("\(pageID.uuidString).manifest.json")
        guard let manifestData = try? Data(contentsOf: manifestURL),
              let manifest = try? APIJSON.decoder().decode(CommitManifest.self, from: manifestData) else {
            return nil
        }
        let drawingURL = directory.appendingPathComponent("\(pageID.uuidString).\(manifest.revisionID.uuidString).pkdrawing")
        let bundleURL = directory.appendingPathComponent("\(pageID.uuidString).\(manifest.revisionID.uuidString).ink.json")
        guard FileManager.default.fileExists(atPath: drawingURL.path),
              FileManager.default.fileExists(atPath: bundleURL.path) else {
            return nil
        }
        let fallbackCreatedAt = ((try? FileManager.default
            .attributesOfItem(atPath: bundleURL.path)[.creationDate]) as? Date) ?? Date()
        return LocalInkRevisionMetadata(
            revisionID: manifest.revisionID,
            pkDrawingPath: drawingURL.path,
            pkDrawingSHA256: manifest.pkDrawingSHA256,
            inkBundlePath: bundleURL.path,
            inkBundleSHA256: manifest.inkBundleSHA256,
            createdAt: manifest.createdAt ?? fallbackCreatedAt
        )
    }

    static func copyDrawing(from sourcePageID: UUID, to destinationPageID: UUID) throws {
        guard let validatedCommit = readValidatedCommit(pageID: sourcePageID) else {
            guard let drawingData = readPKDrawing(pageID: sourcePageID) else { return }
            if let bundle = readInkBundle(pageID: sourcePageID) {
                try commit(
                    pageID: destinationPageID,
                    pkDrawingData: drawingData,
                    bundle: bundle.copying(pageID: destinationPageID)
                )
            } else {
                try writePKDrawing(drawingData, pageID: destinationPageID)
            }
            return
        }
        try commit(
            pageID: destinationPageID,
            pkDrawingData: validatedCommit.drawingData,
            bundle: validatedCommit.bundle.copying(pageID: destinationPageID)
        )
    }

    static func copyInkBundle(from sourcePageID: UUID, to destinationPageID: UUID) throws {
        guard let bundle = readInkBundle(pageID: sourcePageID) else { return }
        if let drawingData = readPKDrawing(pageID: destinationPageID) {
            try commit(
                pageID: destinationPageID,
                pkDrawingData: drawingData,
                bundle: bundle.copying(pageID: destinationPageID)
            )
        } else {
            try writeInkBundle(bundle.copying(pageID: destinationPageID))
        }
    }

    static func remove(pageID: UUID) {
        guard let directory = try? directory(),
              let files = try? FileManager.default.contentsOfDirectory(
                  at: directory,
                  includingPropertiesForKeys: nil
              ) else { return }
        let prefix = "\(pageID.uuidString)."
        for file in files where file.lastPathComponent.hasPrefix(prefix) {
            try? FileManager.default.removeItem(at: file)
        }
    }

    static func isInkUploaded(pageID: UUID, contentHash: String) -> Bool {
        guard let url = try? directory().appendingPathComponent("\(pageID.uuidString).manifest.json"),
              let data = try? Data(contentsOf: url),
              let manifest = try? APIJSON.decoder().decode(CommitManifest.self, from: data) else {
            return false
        }
        return manifest.uploadedContentHash == contentHash
    }

    static func shouldRestoreRemoteRevision(
        pageID: UUID,
        localBundle: InkBundle?,
        activeRevision: InkBundleRevision
    ) -> Bool {
        guard let localBundle else { return true }
        guard localBundle.contentHash != activeRevision.contentHash else { return false }
        return isInkUploaded(pageID: pageID, contentHash: localBundle.contentHash)
    }

    static func markInkUploaded(pageID: UUID, contentHash: String) throws {
        let directory = try Self.directory()
        let manifestURL = directory.appendingPathComponent("\(pageID.uuidString).manifest.json")
        guard let data = try? Data(contentsOf: manifestURL),
              var manifest = try? APIJSON.decoder().decode(CommitManifest.self, from: data) else {
            return
        }
        manifest.uploadedContentHash = contentHash
        manifest.uploadIdempotencyKey = nil
        try APIJSON.encoder().encode(manifest).write(to: manifestURL, options: [.atomic])
        protect(manifestURL)
    }

    /// Returns the stable key for the current local revision, persisting it
    /// before the network request so a crash can resume the same server
    /// session instead of creating a duplicate immutable revision.
    static func resumableUploadIdempotencyKey(
        pageID: UUID,
        contentHash: String
    ) throws -> UUID {
        let fallback = Self.stableUploadKey(pageID: pageID, contentHash: contentHash)
        let directory = try Self.directory()
        let manifestURL = directory.appendingPathComponent("\(pageID.uuidString).manifest.json")
        guard let data = try? Data(contentsOf: manifestURL),
              var manifest = try? APIJSON.decoder().decode(CommitManifest.self, from: data) else {
            return fallback
        }
        if manifest.uploadedContentHash == contentHash {
            return manifest.uploadIdempotencyKey ?? fallback
        }
        if let existing = manifest.uploadIdempotencyKey {
            return existing
        }
        let key = fallback
        manifest.uploadIdempotencyKey = key
        try APIJSON.encoder().encode(manifest).write(to: manifestURL, options: [.atomic])
        protect(manifestURL)
        return key
    }

    private static func readValidatedCommit(pageID: UUID) -> (drawingData: Data, bundle: InkBundle)? {
        guard let directory = try? directory(),
              let manifestData = try? Data(contentsOf: directory.appendingPathComponent("\(pageID.uuidString).manifest.json")),
              let manifest = try? APIJSON.decoder().decode(CommitManifest.self, from: manifestData) else {
            return nil
        }
        let drawingURL = directory.appendingPathComponent("\(pageID.uuidString).\(manifest.revisionID.uuidString).pkdrawing")
        let bundleURL = directory.appendingPathComponent("\(pageID.uuidString).\(manifest.revisionID.uuidString).ink.json")
        guard let drawingData = try? Data(contentsOf: drawingURL),
              let bundleData = try? Data(contentsOf: bundleURL),
              Self.sha256(drawingData) == manifest.pkDrawingSHA256,
              Self.sha256(bundleData) == manifest.inkBundleSHA256,
              let bundle = try? InkV1Codec.decode(bundleData),
              bundle.pageID == pageID else {
            return nil
        }
        if let native = bundle.nativeDrawing, native.data != drawingData {
            return nil
        }
        return (drawingData, bundle)
    }

    private static func sha256(_ data: Data) -> String {
        SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
    }

    private static func stableUploadKey(pageID: UUID, contentHash: String) -> UUID {
        let digest = Array(SHA256.hash(data: Data("\(pageID.uuidString.lowercased()):\(contentHash)".utf8)))
        return UUID(uuid: (
            digest[0], digest[1], digest[2], digest[3],
            digest[4], digest[5], digest[6], digest[7],
            digest[8], digest[9], digest[10], digest[11],
            digest[12], digest[13], digest[14], digest[15]
        ))
    }

    private static func protect(_ url: URL) {
        #if os(iOS)
        try? FileManager.default.setAttributes(
            [.protectionKey: FileProtectionType.completeUntilFirstUserAuthentication],
            ofItemAtPath: url.path
        )
        #endif
    }

    private static func failIfTestCommitFailure(at point: CommitFailurePoint) throws {
#if DEBUG
        guard UserDefaults.standard.string(forKey: testCommitFailureDefaultsKey) == point.rawValue else {
            return
        }
        UserDefaults.standard.removeObject(forKey: testCommitFailureDefaultsKey)
        throw LocalInkCommitTestFailure(point: point)
#else
        _ = point
#endif
    }
}

#if DEBUG
struct LocalInkCommitTestFailure: Error {
    let point: LocalInkCache.CommitFailurePoint
}
#endif

private extension InkBundle {
    func copying(pageID: UUID) -> InkBundle {
        InkBundle(
            pageID: pageID,
            coordinateSpace: coordinateSpace,
            strokes: strokes,
            capture: capture,
            nativeDrawing: nativeDrawing
        )
    }
}

struct InkSaveFailure: Codable, Equatable, Hashable, Sendable, Identifiable {
    let pageID: UUID
    let byteCount: Int
    let error: String
    let recordedAt: Date

    var id: UUID { pageID }
}

enum LocalConversionRunStore {
    private static let defaultsKey = "personalized-notes.conversion-runs"

    static func load() -> [UUID: RecognitionRun] {
        guard let data = UserDefaults.standard.data(forKey: defaultsKey),
              let runs = try? APIJSON.decoder().decode([RecognitionRun].self, from: data) else {
            return [:]
        }
        var result: [UUID: RecognitionRun] = [:]
        for run in runs {
            result[run.pageID] = run
        }
        return result
    }

    static func save(_ runs: [RecognitionRun]) {
        guard let data = try? APIJSON.encoder().encode(
            runs.sorted { $0.createdAt > $1.createdAt }
        ) else {
            return
        }
        UserDefaults.standard.set(data, forKey: defaultsKey)
    }
}

enum LocalInkFailureJournal {
    private static let journalFilename = "save-failures.json"

    static func list() -> [InkSaveFailure] {
        guard let url = try? journalURL(),
              let data = try? Data(contentsOf: url),
              let failures = try? APIJSON.decoder().decode([InkSaveFailure].self, from: data) else {
            return []
        }
        return failures.sorted { $0.recordedAt > $1.recordedAt }
    }

    static func recordFailure(
        pageID: UUID,
        pkDrawingData: Data,
        bundle: InkBundle,
        error: Error
    ) throws {
        let directory = try recoveryDirectory()
        let drawingURL = directory.appendingPathComponent("\(pageID.uuidString).pkdrawing")
        let bundleURL = directory.appendingPathComponent("\(pageID.uuidString).ink.json")
        let drawingTemporaryURL = directory.appendingPathComponent(".\(pageID.uuidString).pkdrawing.tmp")
        let bundleTemporaryURL = directory.appendingPathComponent(".\(pageID.uuidString).ink.json.tmp")

        try pkDrawingData.write(to: drawingTemporaryURL, options: [.atomic])
        try InkV1Codec.encode(bundle).write(to: bundleTemporaryURL, options: [.atomic])
        try? FileManager.default.removeItem(at: drawingURL)
        try? FileManager.default.removeItem(at: bundleURL)
        try FileManager.default.moveItem(at: drawingTemporaryURL, to: drawingURL)
        try FileManager.default.moveItem(at: bundleTemporaryURL, to: bundleURL)
        protect(drawingURL)
        protect(bundleURL)

        var failures = list().filter { $0.pageID != pageID }
        failures.append(
            InkSaveFailure(
                pageID: pageID,
                byteCount: pkDrawingData.count,
                error: String(error.localizedDescription.prefix(512)),
                recordedAt: Date()
            )
        )
        try write(failures)
    }

    static func recoverySnapshot(pageID: UUID) -> (drawingData: Data, bundle: InkBundle)? {
        guard let directory = try? recoveryDirectory(),
              let drawingData = try? Data(
                  contentsOf: directory.appendingPathComponent("\(pageID.uuidString).pkdrawing")
              ),
              let bundleData = try? Data(
                  contentsOf: directory.appendingPathComponent("\(pageID.uuidString).ink.json")
              ),
              let bundle = try? InkV1Codec.decode(bundleData),
              bundle.pageID == pageID,
              bundle.nativeDrawing == nil || bundle.nativeDrawing?.data == drawingData else {
            return nil
        }
        return (drawingData, bundle)
    }

    static func clear(pageID: UUID) {
        if let directory = try? recoveryDirectory() {
            try? FileManager.default.removeItem(
                at: directory.appendingPathComponent("\(pageID.uuidString).pkdrawing")
            )
            try? FileManager.default.removeItem(
                at: directory.appendingPathComponent("\(pageID.uuidString).ink.json")
            )
        }
        try? write(list().filter { $0.pageID != pageID })
    }

    private static func recoveryDirectory() throws -> URL {
        let directory = try OfflineStoreBootstrap.applicationSupportDirectory()
            .appendingPathComponent("ink-recovery", isDirectory: true)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        return directory
    }

    private static func journalURL() throws -> URL {
        try recoveryDirectory().appendingPathComponent(journalFilename)
    }

    private static func write(_ failures: [InkSaveFailure]) throws {
        let url = try journalURL()
        if failures.isEmpty {
            try? FileManager.default.removeItem(at: url)
            return
        }
        try APIJSON.encoder().encode(failures).write(to: url, options: [.atomic])
        protect(url)
    }

    private static func protect(_ url: URL) {
        #if os(iOS)
        try? FileManager.default.setAttributes(
            [.protectionKey: FileProtectionType.completeUntilFirstUserAuthentication],
            ofItemAtPath: url.path
        )
        #endif
    }
}

enum DurableImportStore {
    /// Removes a source only when it is inside this app's durable imports
    /// directory. Older/local data may contain user-selected paths, so a
    /// permanent notebook delete must never treat arbitrary metadata as an
    /// authorization to delete outside app-owned storage.
    static func removeOwnedFile(at path: String) throws {
        let directory = try OfflineStoreBootstrap.applicationSupportDirectory()
            .appendingPathComponent("imports", isDirectory: true)
        let rootPath = directory.standardizedFileURL.path
        let candidate = URL(fileURLWithPath: path).standardizedFileURL
        let candidatePath = candidate.path
        guard candidatePath != rootPath,
              candidatePath.hasPrefix(rootPath + "/") else {
            return
        }
        guard FileManager.default.fileExists(atPath: candidatePath) else { return }
        try FileManager.default.removeItem(at: candidate)
    }

    static func copy(item: PendingShareImport) throws -> URL {
        let source = try ShareInboxStore.fileURL(for: item, allowFallback: true)
        return try copy(
            sourceURL: source,
            originalFilename: item.originalFilename,
            identifier: item.id
        )
    }

    static func copy(sourceURL: URL, originalFilename: String? = nil) throws -> URL {
        try copy(
            sourceURL: sourceURL,
            originalFilename: originalFilename ?? sourceURL.lastPathComponent,
            identifier: UUID()
        )
    }

    private static func copy(
        sourceURL: URL,
        originalFilename: String,
        identifier: UUID
    ) throws -> URL {
        try validateImportSource(sourceURL)
        let directory = try OfflineStoreBootstrap.applicationSupportDirectory()
            .appendingPathComponent("imports", isDirectory: true)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)

        let filename = URL(fileURLWithPath: originalFilename).lastPathComponent
        let safeName = sanitizedFilename(filename.isEmpty ? "import" : filename)
        let destination = directory.appendingPathComponent("\(identifier.uuidString)-\(safeName)")
        if FileManager.default.fileExists(atPath: destination.path) {
            return destination
        }

        let temporary = directory.appendingPathComponent(".\(identifier.uuidString).tmp")
        try? FileManager.default.removeItem(at: temporary)
        let accessed = sourceURL.startAccessingSecurityScopedResource()
        defer {
            if accessed { sourceURL.stopAccessingSecurityScopedResource() }
        }
        try FileManager.default.copyItem(at: sourceURL, to: temporary)
        try FileManager.default.moveItem(at: temporary, to: destination)
        #if os(iOS)
        try? FileManager.default.setAttributes(
            [.protectionKey: FileProtectionType.completeUntilFirstUserAuthentication],
            ofItemAtPath: destination.path
        )
        #endif
        return destination
    }

    private static func validateImportSource(_ sourceURL: URL) throws {
        let accessed = sourceURL.startAccessingSecurityScopedResource()
        defer {
            if accessed { sourceURL.stopAccessingSecurityScopedResource() }
        }
        let isNativeArchive = sourceURL.pathExtension.caseInsensitiveCompare("hndnote") == .orderedSame
        let maximumBytes = isNativeArchive
            ? Int64(NativeArchiveFormat.maximumBytes)
            : Int64(10 * 1024 * 1024)
        let byteCount = try byteCount(of: sourceURL, maximum: maximumBytes)
        guard byteCount <= maximumBytes else {
            throw ImportedSourceError.tooLarge
        }

        let freeBytes = (try? FileManager.default.attributesOfFileSystem(
            forPath: OfflineStoreBootstrap.applicationSupportDirectory().path
        )[.systemFreeSize] as? NSNumber)?.int64Value
        let requiredBytes = Int64(byteCount) * 2 + 50 * 1024 * 1024
        if let freeBytes, freeBytes < requiredBytes {
            throw ImportedSourceError.insufficientStorage
        }
    }

    private static func byteCount(of sourceURL: URL, maximum: Int64) throws -> Int64 {
        let values = try sourceURL.resourceValues(
            forKeys: [.fileSizeKey, .isDirectoryKey, .isSymbolicLinkKey]
        )
        guard values.isSymbolicLink != true else {
            throw ImportedSourceError.unreadable
        }
        if values.isDirectory == true {
            guard sourceURL.pathExtension.caseInsensitiveCompare("hndnote") == .orderedSame,
                  let enumerator = FileManager.default.enumerator(
                      at: sourceURL,
                      includingPropertiesForKeys: [.fileSizeKey, .isDirectoryKey, .isSymbolicLinkKey],
                      options: []
                  ) else {
                throw ImportedSourceError.unreadable
            }
            var total: Int64 = 0
            for case let childURL as URL in enumerator {
                let childValues = try childURL.resourceValues(
                    forKeys: [.fileSizeKey, .isDirectoryKey, .isSymbolicLinkKey]
                )
                guard childValues.isSymbolicLink != true else {
                    throw ImportedSourceError.unreadable
                }
                guard childValues.isDirectory != true else { continue }
                guard let childSize = childValues.fileSize, childSize >= 0 else {
                    throw ImportedSourceError.unreadable
                }
                total += Int64(childSize)
                if total > maximum { throw ImportedSourceError.tooLarge }
            }
            return max(total, 1)
        }
        guard let fileSize = values.fileSize, fileSize >= 0 else {
            throw ImportedSourceError.unreadable
        }
        return Int64(fileSize)
    }

    private static func sanitizedFilename(_ filename: String) -> String {
        let allowed = CharacterSet.alphanumerics.union(CharacterSet(charactersIn: ".-_"))
        let filtered = filename.unicodeScalars.map { allowed.contains($0) ? Character($0) : Character("-") }
        let result = String(filtered)
        return result.isEmpty ? "import" : result
    }
}

struct ImportFailure: Codable, Equatable, Identifiable, Sendable {
    let id: UUID
    let sourcePath: String
    let originalFilename: String
    var reason: String
    let createdAt: Date

    var sourceURL: URL { URL(fileURLWithPath: sourcePath) }
}

enum LocalImportFailureJournal {
    private static let filename = "import-failures.json"

    static func list() -> [ImportFailure] {
        guard let url = try? journalURL(),
              let data = try? Data(contentsOf: url),
              let failures = try? APIJSON.decoder().decode([ImportFailure].self, from: data) else {
            return []
        }
        return failures
            .filter { FileManager.default.fileExists(atPath: $0.sourcePath) }
            .sorted { $0.createdAt > $1.createdAt }
    }

    static func record(sourceURL: URL, originalFilename: String, reason: String) throws {
        var failures = list()
        if let index = failures.firstIndex(where: { $0.sourcePath == sourceURL.path }) {
            failures[index].reason = reason
        } else {
            failures.append(
                ImportFailure(
                    id: UUID(),
                    sourcePath: sourceURL.path,
                    originalFilename: originalFilename,
                    reason: reason,
                    createdAt: Date()
                )
            )
        }
        try write(failures)
    }

    static func update(id: UUID, reason: String) throws {
        var failures = list()
        guard let index = failures.firstIndex(where: { $0.id == id }) else { return }
        failures[index].reason = reason
        try write(failures)
    }

    static func remove(id: UUID) {
        guard var failures = try? readAll() else { return }
        failures.removeAll { $0.id == id }
        try? write(failures)
    }

    private static func readAll() throws -> [ImportFailure] {
        let url = try journalURL()
        guard let data = try? Data(contentsOf: url) else { return [] }
        return try APIJSON.decoder().decode([ImportFailure].self, from: data)
    }

    private static func write(_ failures: [ImportFailure]) throws {
        let url = try journalURL()
        if failures.isEmpty {
            try? FileManager.default.removeItem(at: url)
            return
        }
        let data = try APIJSON.encoder().encode(failures)
        try data.write(to: url, options: .atomic)
        #if os(iOS)
        try? FileManager.default.setAttributes(
            [.protectionKey: FileProtectionType.completeUntilFirstUserAuthentication],
            ofItemAtPath: url.path
        )
        #endif
    }

    private static func journalURL() throws -> URL {
        let directory = try OfflineStoreBootstrap.applicationSupportDirectory()
            .appendingPathComponent("imports", isDirectory: true)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        return directory.appendingPathComponent(filename)
    }
}
