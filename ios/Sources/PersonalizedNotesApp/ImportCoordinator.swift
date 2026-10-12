import Foundation
import PersonalizedNotesKit
import ShareInbox
import SwiftUI

#if os(iOS) && canImport(PhotosUI)
import PhotosUI
#endif

/// A source that has crossed the process/security-scope boundary and is ready
/// for durable document creation. Every import entry point uses this value so
/// the host app never creates a notebook from a transient Files or share URL.
struct CoordinatedImportSource {
    let sourceURL: URL
    let originalFilename: String
    let shareImportID: UUID?
    let uniformTypeIdentifier: String?
}

enum ImportCoordinator {
    static func stageFile(_ sourceURL: URL) throws -> CoordinatedImportSource {
        let filename = sourceURL.lastPathComponent
        return CoordinatedImportSource(
            sourceURL: try DurableImportStore.copy(
                sourceURL: sourceURL,
                originalFilename: filename
            ),
            originalFilename: filename,
            shareImportID: nil,
            uniformTypeIdentifier: nil
        )
    }

    static func stageShare(_ item: PendingShareImport) throws -> CoordinatedImportSource {
        CoordinatedImportSource(
            sourceURL: try DurableImportStore.copy(item: item),
            originalFilename: item.originalFilename,
            shareImportID: item.id,
            uniformTypeIdentifier: item.uniformTypeIdentifier
        )
    }

    static func makeDocument(from source: CoordinatedImportSource) throws -> DocumentRecord {
        if isNativeArchive(sourceURL: source.sourceURL, originalFilename: source.originalFilename) {
            return try NativeArchiveImporter.makeDocument(
                archiveURL: source.sourceURL,
                shareImportID: source.shareImportID,
                uniformTypeIdentifier: source.uniformTypeIdentifier
            )
        }
        return try ImportedSourceFactory.makeDocument(
            sourceURL: source.sourceURL,
            originalFilename: source.originalFilename,
            shareImportID: source.shareImportID,
            uniformTypeIdentifier: source.uniformTypeIdentifier
        )
    }

    static func makeDocument(
        sourceURL: URL,
        originalFilename: String
    ) throws -> DocumentRecord {
        if isNativeArchive(sourceURL: sourceURL, originalFilename: originalFilename) {
            return try NativeArchiveImporter.makeDocument(archiveURL: sourceURL)
        }
        return try ImportedSourceFactory.makeDocument(
            sourceURL: sourceURL,
            originalFilename: originalFilename
        )
    }

    static func cleanupPreparedDocument(_ document: DocumentRecord) {
        for page in document.pages {
            LocalInkCache.remove(pageID: page.pageID)
        }
        guard document.sourceMetadata?["imported_archive_document_id"] != nil,
              case let .string(path)? = document.sourceMetadata?["durable_file_path"] else {
            return
        }
        try? FileManager.default.removeItem(at: URL(fileURLWithPath: path))
    }

    private static func isNativeArchive(sourceURL: URL, originalFilename: String) -> Bool {
        let filename = URL(fileURLWithPath: originalFilename).lastPathComponent
        return sourceURL.pathExtension.caseInsensitiveCompare("hndnote") == .orderedSame
            || filename.hasSuffix(".hndnote")
            || filename.hasSuffix(".HNDNOTE")
    }

    #if os(iOS) && canImport(PhotosUI)
    static func stagePhotos(_ items: [PhotosPickerItem]) async throws -> [URL] {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("personalized-notes-photo-imports", isDirectory: true)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)

        var stagedURLs: [URL] = []
        do {
            for (index, item) in items.enumerated() {
                try Task.checkCancellation()
                guard let data = try await item.loadTransferable(type: Data.self) else {
                    continue
                }
                let url = directory.appendingPathComponent(
                    "photo-\(index + 1)-\(UUID().uuidString).jpg"
                )
                try data.write(to: url, options: .atomic)
                stagedURLs.append(url)
            }
        } catch {
            stagedURLs.forEach { try? FileManager.default.removeItem(at: $0) }
            throw error
        }
        return stagedURLs
    }
    #endif
}
