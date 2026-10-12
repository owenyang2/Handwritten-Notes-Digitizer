import CryptoKit
import Foundation
import PersonalizedNotesKit

#if canImport(PDFKit) && canImport(PencilKit) && canImport(UIKit)
import PDFKit
import PencilKit
import UIKit
#endif

enum NativeArchiveImportError: LocalizedError {
    case unsupported
    case invalid(String)
    case checksumMismatch(String)
    case sourceUnavailable

    var errorDescription: String? {
        switch self {
        case .unsupported:
            return "Native notebook archives are only available on iPadOS."
        case let .invalid(reason):
            return "The native notebook archive is invalid: \(reason)"
        case let .checksumMismatch(path):
            return "The native notebook archive failed its integrity check for \(path)."
        case .sourceUnavailable:
            return "The native notebook archive contains an unavailable or unreadable source asset."
        }
    }
}

enum NativeArchiveImporter {
    static func makeDocument(
        archiveURL: URL,
        shareImportID: UUID? = nil,
        uniformTypeIdentifier: String? = nil
    ) throws -> DocumentRecord {
        #if canImport(PDFKit) && canImport(PencilKit) && canImport(UIKit)
        let reader = try NativeArchiveReader(rootURL: archiveURL)
        let manifest = reader.manifest
        let orderedPages = manifest.pages.sorted { $0.pageIndex < $1.pageIndex }
        let newDocumentID = UUID()
        var pageIDs: [UUID: UUID] = [:]
        for page in orderedPages {
            pageIDs[page.pageID] = UUID()
        }

        var pages: [PageSummary] = []
        var pendingInk: [(pageID: UUID, drawingData: Data, bundle: InkBundle)] = []
        for (index, archivePage) in orderedPages.enumerated() {
            guard let pageID = pageIDs[archivePage.pageID] else {
                throw NativeArchiveImportError.invalid("A page identity could not be remapped.")
            }
            let page = PageSummary(
                pageID: pageID,
                pageIndex: index,
                title: archivePage.title,
                width: archivePage.width,
                height: archivePage.height,
                dpi: archivePage.dpi,
                coordinateSpace: archivePage.coordinateSpace,
                paperTemplate: archivePage.paperTemplate
            )
            pages.append(page)

            let nativeData = try archivePage.nativeDrawing.map {
                try reader.read($0, expectedDirectory: "pages")
            }
            let canonicalBundle = try archivePage.canonicalInk.map {
                let data = try reader.read($0, expectedDirectory: "pages")
                do {
                    return try InkV1Codec.decode(data)
                } catch {
                    throw NativeArchiveImportError.invalid(
                        "The canonical ink for page \(archivePage.pageIndex + 1) could not be decoded."
                    )
                }
            }

            if let canonicalBundle {
                guard canonicalBundle.pageID == archivePage.pageID else {
                    throw NativeArchiveImportError.invalid(
                        "Canonical ink page identity does not match page \(archivePage.pageIndex + 1)."
                    )
                }
                guard abs(canonicalBundle.coordinateSpace.width - Double(archivePage.width)) < 0.5,
                      abs(canonicalBundle.coordinateSpace.height - Double(archivePage.height)) < 0.5 else {
                    throw NativeArchiveImportError.invalid(
                        "Canonical ink coordinates do not match page \(archivePage.pageIndex + 1)."
                    )
                }
                if let nativeData,
                   let embeddedNative = canonicalBundle.nativeDrawing,
                   embeddedNative.data != nativeData {
                    throw NativeArchiveImportError.invalid(
                        "Native and canonical ink disagree for page \(archivePage.pageIndex + 1)."
                    )
                }
                try canonicalBundle.validate()
            }

            let embeddedNativeData = canonicalBundle?.nativeDrawing?.data
            let drawingData = nativeData ?? embeddedNativeData ?? Data()
            if !drawingData.isEmpty {
                guard (try? PKDrawing(data: drawingData)) != nil else {
                    throw NativeArchiveImportError.invalid(
                        "The native drawing for page \(archivePage.pageIndex + 1) could not be opened."
                    )
                }
            }

            let bundle: InkBundle?
            if let canonicalBundle {
                bundle = InkBundle(
                    pageID: pageID,
                    coordinateSpace: canonicalBundle.coordinateSpace,
                    strokes: canonicalBundle.strokes,
                    capture: canonicalBundle.capture,
                    nativeDrawing: drawingData.isEmpty
                        ? nil
                        : NativeDrawing(data: drawingData)
                )
            } else if !drawingData.isEmpty,
                      let drawing = try? PKDrawing(data: drawingData) {
                let generated = try PencilKitInkAdapter.makeBundle(
                    drawing: drawing,
                    pageID: pageID,
                    width: Double(archivePage.width),
                    height: Double(archivePage.height)
                )
                bundle = InkBundle(
                    pageID: pageID,
                    coordinateSpace: generated.coordinateSpace,
                    strokes: generated.strokes,
                    capture: generated.capture,
                    nativeDrawing: NativeDrawing(data: drawingData)
                )
            } else {
                bundle = nil
            }

            if let bundle {
                pendingInk.append((pageID: pageID, drawingData: drawingData, bundle: bundle))
            }
        }

        var sourceMetadata: JSONDocument?
        var copiedSourceURLs: [URL] = []
        do {
            let hasPageSources = orderedPages.contains { $0.source != nil }
            if hasPageSources {
                var copiedSources: [String: (url: URL, kind: String, filename: String)] = [:]
                var pageSources: JSONDocument = [:]
                for archivePage in orderedPages {
                    guard let source = archivePage.source,
                          let sourceKind = archivePage.sourceKind,
                          sourceKind == "pdf_import" || sourceKind == "image_import" else {
                        throw NativeArchiveImportError.invalid(
                            "Every page source must declare a supported PDF or image kind."
                        )
                    }
                    guard let remappedPageID = pageIDs[archivePage.pageID] else {
                        throw NativeArchiveImportError.invalid(
                            "A page identity could not be remapped."
                        )
                    }
                    let archiveSourceURL = try reader.url(for: source, expectedDirectory: "source")
                    let copied: (url: URL, kind: String, filename: String)
                    if let existing = copiedSources[source.path] {
                        copied = existing
                    } else {
                        let filename = archiveSourceURL.lastPathComponent
                        let durableSource = try DurableImportStore.copy(
                            sourceURL: archiveSourceURL,
                            originalFilename: filename
                        )
                        copiedSourceURLs.append(durableSource)
                        try validatePageSource(
                            durableSource,
                            kind: sourceKind,
                            pageIndex: archivePage.pageIndex
                        )
                        let value = (url: durableSource, kind: sourceKind, filename: filename)
                        copiedSources[source.path] = value
                        copied = value
                    }
                    pageSources[remappedPageID.uuidString] = PageSourceMetadata.entry(
                        sourceURL: copied.url,
                        sourceKind: copied.kind,
                        sourcePageIndex: copied.kind == "pdf_import"
                            ? archivePage.pageIndex
                            : 0,
                        originalFilename: copied.filename
                    )
                }
                sourceMetadata = [
                    "imported_archive_document_id": .string(manifest.documentID.uuidString),
                    "imported_archive_version": .number(Double(manifest.version)),
                    PageSourceMetadata.key: .object(pageSources),
                ]
                if let first = copiedSources.values.first, copiedSources.count == 1 {
                    sourceMetadata?["original_filename"] = .string(first.filename)
                    sourceMetadata?["durable_file_path"] = .string(first.url.path)
                }
            } else if let source = manifest.source {
                guard manifest.sourceKind == "pdf_import" || manifest.sourceKind == "image_import" else {
                    throw NativeArchiveImportError.invalid(
                        "A source asset is only supported for PDF or image notebooks."
                    )
                }
                let sourceURL = try reader.url(for: source, expectedDirectory: "source")
                let filename = sourceURL.lastPathComponent
                let durableSource = try DurableImportStore.copy(
                    sourceURL: sourceURL,
                    originalFilename: filename
                )
                copiedSourceURLs.append(durableSource)
                try validateSource(
                    durableSource,
                    kind: manifest.sourceKind,
                    pageCount: pages.count
                )
                sourceMetadata = [
                    "original_filename": .string(filename),
                    "durable_file_path": .string(durableSource.path),
                    "imported_archive_document_id": .string(manifest.documentID.uuidString),
                    "imported_archive_version": .number(Double(manifest.version)),
                ]
                var pageSources: JSONDocument = [:]
                for archivePage in orderedPages {
                    guard let remappedPageID = pageIDs[archivePage.pageID] else {
                        throw NativeArchiveImportError.invalid(
                            "A page identity could not be remapped."
                        )
                    }
                    pageSources[remappedPageID.uuidString] = PageSourceMetadata.entry(
                        sourceURL: durableSource,
                        sourceKind: manifest.sourceKind,
                        sourcePageIndex: manifest.sourceKind == "pdf_import"
                            ? archivePage.pageIndex
                            : 0,
                        originalFilename: filename
                    )
                }
                sourceMetadata?[PageSourceMetadata.key] = .object(pageSources)
            } else if manifest.sourceKind != "native_ink" {
                throw NativeArchiveImportError.invalid(
                    "The manifest declares \(manifest.sourceKind) but contains no source asset."
                )
            }

            var committedPageIDs: [UUID] = []
            do {
                for ink in pendingInk {
                    try LocalInkCache.commit(
                        pageID: ink.pageID,
                        pkDrawingData: ink.drawingData,
                        bundle: ink.bundle
                    )
                    committedPageIDs.append(ink.pageID)
                }
            } catch {
                committedPageIDs.forEach(LocalInkCache.remove)
                throw error
            }

            var metadata = sourceMetadata
            if let shareImportID {
                metadata = metadata ?? [:]
                metadata?["share_import_id"] = .string(shareImportID.uuidString)
            }
            if let uniformTypeIdentifier {
                metadata = metadata ?? [:]
                metadata?["uti"] = .string(uniformTypeIdentifier)
            }
            let title = manifest.title.trimmingCharacters(in: .whitespacesAndNewlines)
            return DocumentRecord(
                documentID: newDocumentID,
                title: title.isEmpty ? "Imported notebook" : String(title.prefix(255)),
                sourceKind: manifest.sourceKind,
                pages: pages,
                sourceMetadata: metadata
            )
        } catch {
            copiedSourceURLs.forEach { try? FileManager.default.removeItem(at: $0) }
            throw error
        }
        #else
        throw NativeArchiveImportError.unsupported
        #endif
    }

    #if canImport(PDFKit) && canImport(PencilKit) && canImport(UIKit)
    private static func validatePageSource(
        _ url: URL,
        kind: String,
        pageIndex: Int
    ) throws {
        if kind == "pdf_import" {
            guard let pdf = PDFDocument(url: url),
                  let page = pdf.page(at: pageIndex) else {
                throw NativeArchiveImportError.sourceUnavailable
            }
            let bounds = page.bounds(for: .mediaBox)
            guard bounds.width > 0, bounds.height > 0,
                  bounds.width.isFinite, bounds.height.isFinite else {
                throw NativeArchiveImportError.sourceUnavailable
            }
        } else if kind == "image_import" {
            guard UIImage(contentsOfFile: url.path) != nil else {
                throw NativeArchiveImportError.sourceUnavailable
            }
        } else {
            throw NativeArchiveImportError.invalid("Unsupported page source kind \(kind).")
        }
    }

    private static func validateSource(
        _ url: URL,
        kind: String,
        pageCount: Int
    ) throws {
        if kind == "pdf_import" {
            guard let pdf = PDFDocument(url: url), pdf.pageCount == pageCount else {
                throw NativeArchiveImportError.sourceUnavailable
            }
            for index in 0..<pdf.pageCount {
                guard let page = pdf.page(at: index) else {
                    throw NativeArchiveImportError.sourceUnavailable
                }
                let bounds = page.bounds(for: .mediaBox)
                guard bounds.width > 0, bounds.height > 0,
                      bounds.width.isFinite, bounds.height.isFinite else {
                    throw NativeArchiveImportError.sourceUnavailable
                }
            }
        } else if kind == "image_import" {
            guard pageCount == 1, UIImage(contentsOfFile: url.path) != nil else {
                throw NativeArchiveImportError.sourceUnavailable
            }
        } else {
            throw NativeArchiveImportError.invalid("Unsupported source kind \(kind).")
        }
    }
    #endif
}

#if canImport(PDFKit) && canImport(PencilKit) && canImport(UIKit)
private struct NativeArchiveReader {
    let rootURL: URL
    let manifest: NativeArchiveManifest

    init(rootURL: URL) throws {
        self.rootURL = rootURL
        let values = try rootURL.resourceValues(forKeys: [.isDirectoryKey])
        guard values.isDirectory == true else {
            throw NativeArchiveImportError.invalid("The package is not a directory archive.")
        }
        let manifestURL = rootURL.appendingPathComponent("manifest.json")
        guard FileManager.default.fileExists(atPath: manifestURL.path),
              let data = try? Data(contentsOf: manifestURL),
              data.count <= 1_024 * 1_024 else {
            throw NativeArchiveImportError.invalid("The manifest is missing or too large.")
        }
        do {
            self.manifest = try APIJSON.decoder().decode(NativeArchiveManifest.self, from: data)
        } catch {
            throw NativeArchiveImportError.invalid("The manifest could not be decoded.")
        }
        try validateManifest(self.manifest)
    }

    func url(for file: NativeArchiveFile, expectedDirectory: String) throws -> URL {
        let url = try safeURL(for: file.path, expectedDirectory: expectedDirectory)
        try validateFile(file, at: url)
        return url
    }

    func read(_ file: NativeArchiveFile, expectedDirectory: String) throws -> Data {
        let url = try url(for: file, expectedDirectory: expectedDirectory)
        do {
            return try Data(contentsOf: url, options: .mappedIfSafe)
        } catch {
            throw NativeArchiveImportError.invalid("The file \(file.path) could not be read.")
        }
    }

    private func safeURL(for path: String, expectedDirectory: String) throws -> URL {
        let components = path.split(separator: "/", omittingEmptySubsequences: false).map(String.init)
        guard components.count == 2,
              components[0] == expectedDirectory,
              !components[1].isEmpty,
              components[1] != ".",
              components[1] != "..",
              !components[1].contains("\\"),
              !path.hasPrefix("/") else {
            throw NativeArchiveImportError.invalid("The archive contains an unsafe path \(path).")
        }
        let root = rootURL.standardizedFileURL.path
        let url = rootURL
            .appendingPathComponent(components[0], isDirectory: true)
            .appendingPathComponent(components[1], isDirectory: false)
            .standardizedFileURL
        guard url.path.hasPrefix(root + "/") else {
            throw NativeArchiveImportError.invalid("The archive contains an unsafe path \(path).")
        }
        return url
    }

    private func validateFile(_ file: NativeArchiveFile, at url: URL) throws {
        guard file.byteCount >= 0,
              file.byteCount <= NativeArchiveFormat.maximumBytes,
              file.sha256.count == 64,
              file.sha256.allSatisfy({ $0.isHexDigit }),
              FileManager.default.fileExists(atPath: url.path) else {
            throw NativeArchiveImportError.invalid("The archive file \(file.path) is missing or malformed.")
        }
        let values = try url.resourceValues(forKeys: [.isDirectoryKey])
        guard values.isDirectory != true else {
            throw NativeArchiveImportError.invalid("The archive file \(file.path) is a directory.")
        }
        guard let data = try? Data(contentsOf: url, options: .mappedIfSafe),
              data.count == file.byteCount else {
            throw NativeArchiveImportError.checksumMismatch(file.path)
        }
        let digest = SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
        guard digest.caseInsensitiveCompare(file.sha256) == .orderedSame else {
            throw NativeArchiveImportError.checksumMismatch(file.path)
        }
    }

    private func validateManifest(_ manifest: NativeArchiveManifest) throws {
        guard manifest.schema == NativeArchiveFormat.schema,
              manifest.version == NativeArchiveFormat.version else {
            throw NativeArchiveImportError.invalid(
                "Unsupported archive schema \(manifest.schema) version \(manifest.version)."
            )
        }
        guard !manifest.pages.isEmpty,
              manifest.pages.count <= 200,
              manifest.sourceKind == "native_ink"
                || manifest.sourceKind == "pdf_import"
                || manifest.sourceKind == "image_import"
                || manifest.sourceKind == "mixed_import" else {
            throw NativeArchiveImportError.invalid("The manifest contains unsupported notebook metadata.")
        }

        let orderedPages = manifest.pages.sorted { $0.pageIndex < $1.pageIndex }
        guard orderedPages.enumerated().allSatisfy({ index, page in
            page.pageIndex == index
                && page.width > 0
                && page.height > 0
                && page.width <= 32_768
                && page.height <= 32_768
                && page.dpi > 0
                && !page.coordinateSpace.isEmpty
                && (page.paperTemplate == nil || PagePaperTemplate(rawValue: page.paperTemplate!) != nil)
        }) else {
            throw NativeArchiveImportError.invalid("Page indexes or dimensions are invalid.")
        }
        guard Set(orderedPages.map(\.pageID)).count == orderedPages.count else {
            throw NativeArchiveImportError.invalid("The manifest contains duplicate page identities.")
        }

        var paths = Set<String>()
        var sourcePaths = Set<String>()
        var declaredBytes: Int64 = 0
        for page in orderedPages {
            for file in [page.nativeDrawing, page.canonicalInk].compactMap({ $0 }) {
                guard file.byteCount >= 0,
                      file.byteCount <= NativeArchiveFormat.maximumBytes else {
                    throw NativeArchiveImportError.invalid("The archive contains an invalid file size.")
                }
                guard paths.insert(file.path).inserted else {
                    throw NativeArchiveImportError.invalid("The manifest references a file more than once.")
                }
                declaredBytes += Int64(file.byteCount)
                if declaredBytes > Int64(NativeArchiveFormat.maximumBytes) {
                    throw NativeArchiveImportError.invalid("The archive exceeds the supported size limit.")
                }
            }
            if let source = page.source {
                guard page.sourceKind == "pdf_import" || page.sourceKind == "image_import" else {
                    throw NativeArchiveImportError.invalid("A page source kind is unsupported.")
                }
                if sourcePaths.insert(source.path).inserted {
                    guard paths.insert(source.path).inserted else {
                        throw NativeArchiveImportError.invalid("The manifest reuses a page source path for another file.")
                    }
                    guard source.byteCount >= 0,
                          source.byteCount <= NativeArchiveFormat.maximumBytes else {
                        throw NativeArchiveImportError.invalid("The archive contains an invalid source size.")
                    }
                    declaredBytes += Int64(source.byteCount)
                    if declaredBytes > Int64(NativeArchiveFormat.maximumBytes) {
                        throw NativeArchiveImportError.invalid("The archive exceeds the supported size limit.")
                    }
                }
            }
        }
        if let source = manifest.source {
            guard source.byteCount >= 0,
                  source.byteCount <= NativeArchiveFormat.maximumBytes else {
                throw NativeArchiveImportError.invalid("The archive contains an invalid source size.")
            }
            guard paths.insert(source.path).inserted else {
                throw NativeArchiveImportError.invalid("The manifest references a file more than once.")
            }
            declaredBytes += Int64(source.byteCount)
        }
        guard declaredBytes <= Int64(NativeArchiveFormat.maximumBytes) else {
            throw NativeArchiveImportError.invalid("The archive exceeds the supported size limit.")
        }
        if manifest.sourceKind == "mixed_import" {
            guard orderedPages.contains(where: { $0.source != nil }) else {
                throw NativeArchiveImportError.invalid("A mixed notebook must declare page sources.")
            }
        }
    }
}
#endif
