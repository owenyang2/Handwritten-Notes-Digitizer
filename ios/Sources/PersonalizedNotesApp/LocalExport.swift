import Foundation
import CryptoKit
import PersonalizedNotesKit
import SwiftUI
import UniformTypeIdentifiers

#if canImport(PDFKit) && canImport(PencilKit) && canImport(UIKit)
import PDFKit
import PencilKit
import UIKit
#endif

enum LocalExportError: LocalizedError {
    case unsupported
    case sourceUnavailable
    case exportFailed(String)

    var errorDescription: String? {
        switch self {
        case .unsupported:
            return "This export is unavailable on this platform."
        case .sourceUnavailable:
            return "The imported source is no longer available on this iPad."
        case let .exportFailed(message):
            return "The export could not be created: \(message)"
        }
    }
}

enum LocalPageImageFormat: String {
    case png
    case jpeg

    var fileExtension: String { rawValue }
}

enum LocalPDFExporter {
    static func export(document: DocumentRecord, page: PageSummary) throws -> URL {
        #if canImport(PDFKit) && canImport(PencilKit) && canImport(UIKit)
        let directory = try OfflineStoreBootstrap.applicationSupportDirectory()
            .appendingPathComponent("exports", isDirectory: true)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        let safeTitle = sanitized(document.title)
        let url = directory.appendingPathComponent(
            "\(safeTitle)-page-\(page.pageIndex + 1)-\(UUID().uuidString).pdf"
        )
        let bounds = CGRect(
            x: 0,
            y: 0,
            width: CGFloat(max(page.width, 1)),
            height: CGFloat(max(page.height, 1))
        )
        try validateSource(document: document, page: page)
        let renderer = UIGraphicsPDFRenderer(bounds: bounds)
        var sourceError: LocalExportError?
        do {
            try renderer.writePDF(to: url) { context in
                context.beginPage()
                UIColor.white.setFill()
                context.cgContext.fill(bounds)
                PagePaperTemplateRenderer.draw(
                    template: page.paperTemplate,
                    in: bounds,
                    context: context.cgContext
                )
                do {
                    try drawSource(document: document, page: page, in: bounds, context: context.cgContext)
                } catch let error as LocalExportError {
                    sourceError = error
                } catch {
                    sourceError = .sourceUnavailable
                }
                drawInk(pageID: page.pageID, bounds: bounds)
            }
        } catch {
            try? FileManager.default.removeItem(at: url)
            throw LocalExportError.exportFailed(error.localizedDescription)
        }
        if let sourceError {
            try? FileManager.default.removeItem(at: url)
            throw sourceError
        }
        try? FileManager.default.setAttributes(
            [.protectionKey: FileProtectionType.completeUntilFirstUserAuthentication],
            ofItemAtPath: url.path
        )
        return url
        #else
        throw LocalExportError.unsupported
        #endif
    }

    static func export(document: DocumentRecord) throws -> URL {
        #if canImport(PDFKit) && canImport(PencilKit) && canImport(UIKit)
        let pages = document.pages.sorted { $0.pageIndex < $1.pageIndex }
        guard let firstPage = pages.first else {
            throw LocalExportError.exportFailed("The notebook does not contain any pages.")
        }
        let directory = try OfflineStoreBootstrap.applicationSupportDirectory()
            .appendingPathComponent("exports", isDirectory: true)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        let safeTitle = sanitized(document.title)
        let url = directory.appendingPathComponent(
            "\(safeTitle)-notebook-\(UUID().uuidString).pdf"
        )
        let firstBounds = CGRect(
            x: 0,
            y: 0,
            width: CGFloat(max(firstPage.width, 1)),
            height: CGFloat(max(firstPage.height, 1))
        )
        for page in pages {
            try validateSource(document: document, page: page)
        }

        let renderer = UIGraphicsPDFRenderer(bounds: firstBounds)
        var sourceError: LocalExportError?
        do {
            try renderer.writePDF(to: url) { context in
                for page in pages {
                    let bounds = CGRect(
                        x: 0,
                        y: 0,
                        width: CGFloat(max(page.width, 1)),
                        height: CGFloat(max(page.height, 1))
                    )
                    context.beginPage(withBounds: bounds, pageInfo: [:])
                    UIColor.white.setFill()
                    context.cgContext.fill(bounds)
                    PagePaperTemplateRenderer.draw(
                        template: page.paperTemplate,
                        in: bounds,
                        context: context.cgContext
                    )
                    do {
                        try drawSource(document: document, page: page, in: bounds, context: context.cgContext)
                    } catch let error as LocalExportError {
                        sourceError = error
                    } catch {
                        sourceError = .sourceUnavailable
                    }
                    drawInk(pageID: page.pageID, bounds: bounds)
                }
            }
        } catch {
            try? FileManager.default.removeItem(at: url)
            throw LocalExportError.exportFailed(error.localizedDescription)
        }
        if let sourceError {
            try? FileManager.default.removeItem(at: url)
            throw sourceError
        }
        try? FileManager.default.setAttributes(
            [.protectionKey: FileProtectionType.completeUntilFirstUserAuthentication],
            ofItemAtPath: url.path
        )
        return url
        #else
        throw LocalExportError.unsupported
        #endif
    }

    static func exportPageImage(
        document: DocumentRecord,
        page: PageSummary,
        format: LocalPageImageFormat
    ) throws -> URL {
        #if canImport(PDFKit) && canImport(PencilKit) && canImport(UIKit)
        let directory = try OfflineStoreBootstrap.applicationSupportDirectory()
            .appendingPathComponent("exports", isDirectory: true)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        let safeTitle = sanitized(document.title)
        let url = directory.appendingPathComponent(
            "\(safeTitle)-page-\(page.pageIndex + 1)-\(UUID().uuidString).\(format.fileExtension)"
        )
        let bounds = CGRect(
            x: 0,
            y: 0,
            width: CGFloat(max(page.width, 1)),
            height: CGFloat(max(page.height, 1))
        )
        try validateSource(document: document, page: page)

        // Export dimensions are part of the page contract, not a reflection of
        // the current display scale. A fixed 1x renderer keeps PNG/JPEG output
        // deterministic across iPad models and simulator configurations.
        let rendererFormat = UIGraphicsImageRendererFormat()
        rendererFormat.scale = 1
        rendererFormat.opaque = true
        let renderer = UIGraphicsImageRenderer(size: bounds.size, format: rendererFormat)
        var sourceError: LocalExportError?
        let image = renderer.image { rendererContext in
            UIColor.white.setFill()
            rendererContext.cgContext.fill(bounds)
            PagePaperTemplateRenderer.draw(
                template: page.paperTemplate,
                in: bounds,
                context: rendererContext.cgContext
            )
            do {
                try drawSource(document: document, page: page, in: bounds, context: rendererContext.cgContext)
            } catch let error as LocalExportError {
                sourceError = error
            } catch {
                sourceError = .sourceUnavailable
            }
            drawInk(pageID: page.pageID, bounds: bounds)
        }
        if let sourceError { throw sourceError }

        let data: Data?
        switch format {
        case .png:
            data = image.pngData()
        case .jpeg:
            data = image.jpegData(compressionQuality: 0.92)
        }
        guard let data else {
            throw LocalExportError.exportFailed("The page image could not be encoded.")
        }
        do {
            try data.write(to: url, options: .atomic)
        } catch {
            try? FileManager.default.removeItem(at: url)
            throw LocalExportError.exportFailed(error.localizedDescription)
        }
        protect(url)
        return url
        #else
        throw LocalExportError.unsupported
        #endif
    }

    private static func sanitized(_ value: String) -> String {
        let allowed = CharacterSet.alphanumerics.union(CharacterSet(charactersIn: "-_."))
        let result = value.unicodeScalars.map { allowed.contains($0) ? Character($0) : Character("-") }
        return String(result).isEmpty ? "note" : String(result)
    }

    #if canImport(PDFKit) && canImport(PencilKit) && canImport(UIKit)
    private static func protect(_ url: URL) {
        try? FileManager.default.setAttributes(
            [.protectionKey: FileProtectionType.completeUntilFirstUserAuthentication],
            ofItemAtPath: url.path
        )
    }

    private static func validateSource(document: DocumentRecord, page: PageSummary) throws {
        guard let source = PageSourceMetadata.descriptor(for: document, page: page) else {
            return
        }
        let sourceURL = source.url
        guard FileManager.default.fileExists(atPath: sourceURL.path) else {
            throw LocalExportError.sourceUnavailable
        }
        if source.kind == "pdf_import" {
            guard let pdf = PDFDocument(url: sourceURL),
                  let pdfPage = pdf.page(at: source.sourcePageIndex) else {
                throw LocalExportError.sourceUnavailable
            }
            let sourceBounds = pdfPage.bounds(for: .mediaBox)
            guard sourceBounds.width > 0, sourceBounds.height > 0 else {
                throw LocalExportError.sourceUnavailable
            }
        } else {
            guard UIImage(contentsOfFile: sourceURL.path) != nil else {
                throw LocalExportError.sourceUnavailable
            }
        }
    }

    private static func drawSource(
        document: DocumentRecord,
        page: PageSummary,
        in bounds: CGRect,
        context: CGContext
    ) throws {
        guard let source = PageSourceMetadata.descriptor(for: document, page: page) else {
            return
        }
        let sourceURL = source.url
        guard FileManager.default.fileExists(atPath: sourceURL.path) else {
            throw LocalExportError.sourceUnavailable
        }
        if source.kind == "pdf_import" {
            guard let pdf = PDFDocument(url: sourceURL),
                  let pdfPage = pdf.page(at: source.sourcePageIndex) else {
                throw LocalExportError.sourceUnavailable
            }
            let sourceBounds = pdfPage.bounds(for: .mediaBox)
            guard sourceBounds.width > 0, sourceBounds.height > 0 else {
                throw LocalExportError.sourceUnavailable
            }
            context.saveGState()
            context.translateBy(x: 0, y: bounds.height)
            context.scaleBy(x: 1, y: -1)
            context.scaleBy(
                x: bounds.width / sourceBounds.width,
                y: bounds.height / sourceBounds.height
            )
            pdfPage.draw(with: .mediaBox, to: context)
            context.restoreGState()
        } else {
            guard let image = UIImage(contentsOfFile: sourceURL.path) else {
                throw LocalExportError.sourceUnavailable
            }
            image.draw(in: bounds)
        }
    }

    private static func drawInk(pageID: UUID, bounds: CGRect) {
        guard let data = LocalInkCache.readPKDrawing(pageID: pageID),
              let drawing = try? PKDrawing(data: data) else { return }
        let image = drawing.image(from: bounds, scale: 1)
        image.draw(in: bounds)
    }
    #endif
}

enum LocalSourceExporter {
    static func exportOriginal(document: DocumentRecord) throws -> URL {
        guard let page = document.pages.sorted(by: { $0.pageIndex < $1.pageIndex }).first,
              let source = PageSourceMetadata.descriptor(for: document, page: page) else {
            throw LocalExportError.sourceUnavailable
        }
        let sourceURL = source.url
        guard FileManager.default.fileExists(atPath: sourceURL.path) else {
            throw LocalExportError.sourceUnavailable
        }
        let directory = try OfflineStoreBootstrap.applicationSupportDirectory()
            .appendingPathComponent("exports", isDirectory: true)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        let metadataName: String?
        if let value = source.originalFilename {
            metadataName = URL(fileURLWithPath: value).lastPathComponent
        } else if case let .string(value)? = document.sourceMetadata?["original_filename"] {
            metadataName = URL(fileURLWithPath: value).lastPathComponent
        } else {
            metadataName = nil
        }
        let requestedName = metadataName?.isEmpty == false
            ? metadataName!
            : sourceURL.lastPathComponent
        let baseName = URL(fileURLWithPath: requestedName).deletingPathExtension().lastPathComponent
        let ext = URL(fileURLWithPath: requestedName).pathExtension
        let safeBase = baseName.isEmpty ? "original-source" : sanitized(baseName)
        let suffix = ext.isEmpty ? "" : ".\(sanitized(ext))"
        let url = directory.appendingPathComponent(
            "\(safeBase)-\(UUID().uuidString)\(suffix)"
        )
        do {
            try FileManager.default.copyItem(at: sourceURL, to: url)
        } catch {
            throw LocalExportError.exportFailed(error.localizedDescription)
        }
        protect(url)
        return url
    }

    private static func sanitized(_ value: String) -> String {
        let allowed = CharacterSet.alphanumerics.union(CharacterSet(charactersIn: "-_."))
        let result = value.unicodeScalars.map { allowed.contains($0) ? Character($0) : Character("-") }
        return String(result).isEmpty ? "source" : String(result)
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

enum LocalCanonicalInkExporter {
    static func export(page: PageSummary, documentTitle: String) throws -> URL {
        guard let bundle = LocalInkCache.readInkBundle(pageID: page.pageID) else {
            throw LocalExportError.exportFailed("This page does not have a saved ink_v1 bundle yet.")
        }
        let directory = try OfflineStoreBootstrap.applicationSupportDirectory()
            .appendingPathComponent("exports", isDirectory: true)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        let safeTitle = sanitized(documentTitle)
        let url = directory.appendingPathComponent(
            "\(safeTitle)-page-\(page.pageIndex + 1)-ink-v1-\(UUID().uuidString).json"
        )
        do {
            try InkV1Codec.encode(bundle).write(to: url, options: .atomic)
        } catch {
            throw LocalExportError.exportFailed(error.localizedDescription)
        }
        #if os(iOS)
        try? FileManager.default.setAttributes(
            [.protectionKey: FileProtectionType.completeUntilFirstUserAuthentication],
            ofItemAtPath: url.path
        )
        #endif
        return url
    }

    private static func sanitized(_ value: String) -> String {
        let allowed = CharacterSet.alphanumerics.union(CharacterSet(charactersIn: "-_."))
        let result = value.unicodeScalars.map { allowed.contains($0) ? Character($0) : Character("-") }
        return String(result).isEmpty ? "note" : String(result)
    }
}

enum NativeArchiveFormat {
    static let schema = "hnd_notebook_archive"
    static let version = 1
    static let maximumBytes = 50 * 1024 * 1024
}

struct NativeArchiveFile: Codable {
    let path: String
    let byteCount: Int
    let sha256: String
}

struct NativeArchivePage: Codable {
    let pageID: UUID
    let pageIndex: Int
    let title: String?
    let width: Int
    let height: Int
    let dpi: Int
    let coordinateSpace: String
    let paperTemplate: String?
    let nativeDrawing: NativeArchiveFile?
    let canonicalInk: NativeArchiveFile?
    let source: NativeArchiveFile?
    let sourceKind: String?

    init(
        pageID: UUID,
        pageIndex: Int,
        title: String? = nil,
        width: Int,
        height: Int,
        dpi: Int,
        coordinateSpace: String,
        paperTemplate: String? = nil,
        nativeDrawing: NativeArchiveFile?,
        canonicalInk: NativeArchiveFile?,
        source: NativeArchiveFile? = nil,
        sourceKind: String? = nil
    ) {
        self.pageID = pageID
        self.pageIndex = pageIndex
        self.title = title
        self.width = width
        self.height = height
        self.dpi = dpi
        self.coordinateSpace = coordinateSpace
        self.paperTemplate = paperTemplate
        self.nativeDrawing = nativeDrawing
        self.canonicalInk = canonicalInk
        self.source = source
        self.sourceKind = sourceKind
    }
}

struct NativeArchiveManifest: Codable {
    let schema: String
    let version: Int
    let exportedAt: Date
    let documentID: UUID
    let title: String
    let sourceKind: String
    let source: NativeArchiveFile?
    let pages: [NativeArchivePage]
}

enum LocalNativeArchiveExporter {
    static func export(document: DocumentRecord) throws -> URL {
        let directory = try OfflineStoreBootstrap.applicationSupportDirectory()
            .appendingPathComponent("exports", isDirectory: true)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        let safeTitle = sanitized(document.title)
        let url = directory.appendingPathComponent(
            "\(safeTitle)-notebook-\(UUID().uuidString).hndnote",
            isDirectory: true
        )

        var pageWrappers: [String: FileWrapper] = [:]
        var sourceWrappers: [String: FileWrapper] = [:]
        var sourceRecords: [String: NativeArchiveFile] = [:]
        var archivePages: [NativeArchivePage] = []
        for page in document.pages.sorted(by: { $0.pageIndex < $1.pageIndex }) {
            let nativeName = "\(page.pageID.uuidString).pkdrawing"
            let inkName = "\(page.pageID.uuidString).ink.json"
            var nativeFile: NativeArchiveFile?
            var inkFile: NativeArchiveFile?

            if let nativeData = LocalInkCache.readPKDrawing(pageID: page.pageID), !nativeData.isEmpty {
                pageWrappers[nativeName] = FileWrapper(regularFileWithContents: nativeData)
                nativeFile = NativeArchiveFile(
                    path: "pages/\(nativeName)",
                    byteCount: nativeData.count,
                    sha256: sha256(nativeData)
                )
            }
            if let bundle = LocalInkCache.readInkBundle(pageID: page.pageID) {
                let inkData = try InkV1Codec.encode(bundle)
                pageWrappers[inkName] = FileWrapper(regularFileWithContents: inkData)
                inkFile = NativeArchiveFile(
                    path: "pages/\(inkName)",
                    byteCount: inkData.count,
                    sha256: sha256(inkData)
                )
            }
            var sourceFile: NativeArchiveFile?
            var sourceKind: String?
            if let source = PageSourceMetadata.descriptor(for: document, page: page) {
                guard let sourceData = try? Data(contentsOf: source.url), !sourceData.isEmpty else {
                    throw LocalExportError.sourceUnavailable
                }
                let sourceHash = sha256(sourceData)
                let sourceKey = "\(source.kind):\(sourceHash)"
                if let existing = sourceRecords[sourceKey] {
                    sourceFile = existing
                } else {
                    let originalName = source.originalFilename.map {
                        URL(fileURLWithPath: $0).lastPathComponent
                    } ?? source.url.lastPathComponent
                    let safeName = sanitized(originalName.isEmpty ? "source" : originalName)
                    let archiveName = "\(sourceHash.prefix(16))-\(safeName)"
                    let record = NativeArchiveFile(
                        path: "source/\(archiveName)",
                        byteCount: sourceData.count,
                        sha256: sourceHash
                    )
                    sourceRecords[sourceKey] = record
                    sourceWrappers[archiveName] = FileWrapper(regularFileWithContents: sourceData)
                    sourceFile = record
                }
                sourceKind = source.kind
            }
            archivePages.append(
                NativeArchivePage(
                    pageID: page.pageID,
                    pageIndex: page.pageIndex,
                    title: page.title,
                    width: page.width,
                    height: page.height,
                    dpi: page.dpi,
                    coordinateSpace: page.coordinateSpace,
                    paperTemplate: page.paperTemplate,
                    nativeDrawing: nativeFile,
                    canonicalInk: inkFile,
                    source: sourceFile,
                    sourceKind: sourceKind
                )
            )
        }

        let sourceFile = archivePages.contains(where: { $0.source != nil })
            ? nil
            : (sourceRecords.count == 1 ? sourceRecords.values.first : nil)
        let manifestSourceKind: String
        if sourceRecords.count > 1 {
            manifestSourceKind = "mixed_import"
        } else if let firstPageSourceKind = archivePages.compactMap(\.sourceKind).first {
            manifestSourceKind = firstPageSourceKind
        } else {
            manifestSourceKind = document.sourceKind
        }

        let manifest = NativeArchiveManifest(
            schema: NativeArchiveFormat.schema,
            version: NativeArchiveFormat.version,
            exportedAt: Date(),
            documentID: document.documentID,
            title: document.title,
            sourceKind: manifestSourceKind,
            source: sourceFile,
            pages: archivePages
        )
        let manifestData = try APIJSON.encoder().encode(manifest)
        var rootWrappers: [String: FileWrapper] = [
            "manifest.json": FileWrapper(regularFileWithContents: manifestData),
            "pages": FileWrapper(directoryWithFileWrappers: pageWrappers),
        ]
        if !sourceWrappers.isEmpty {
            rootWrappers["source"] = FileWrapper(directoryWithFileWrappers: sourceWrappers)
        }

        do {
            let root = FileWrapper(directoryWithFileWrappers: rootWrappers)
            try root.write(to: url, options: .atomic, originalContentsURL: nil)
        } catch {
            try? FileManager.default.removeItem(at: url)
            throw LocalExportError.exportFailed(error.localizedDescription)
        }
        #if os(iOS)
        try? FileManager.default.setAttributes(
            [.protectionKey: FileProtectionType.completeUntilFirstUserAuthentication],
            ofItemAtPath: url.path
        )
        #endif
        return url
    }

    private static func sha256(_ data: Data) -> String {
        SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
    }

    private static func sanitized(_ value: String) -> String {
        let allowed = CharacterSet.alphanumerics.union(CharacterSet(charactersIn: "-_."))
        let result = value.unicodeScalars.map { allowed.contains($0) ? Character($0) : Character("-") }
        return String(result).isEmpty ? "note" : String(result)
    }
}

enum LocalAccountExportWriter {
    static func write(_ response: AccountExportResponse) throws -> URL {
        let directory = try OfflineStoreBootstrap.applicationSupportDirectory()
            .appendingPathComponent("exports", isDirectory: true)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        let url = directory.appendingPathComponent(
            "account-export-\(response.exportedAt.formatted(.iso8601)).json"
        )
        let data = try APIJSON.encoder().encode(response)
        try data.write(to: url, options: .atomic)
        #if os(iOS)
        try? FileManager.default.setAttributes(
            [.protectionKey: FileProtectionType.completeUntilFirstUserAuthentication],
            ofItemAtPath: url.path
        )
        #endif
        return url
    }
}

struct ShareableExportFile: Identifiable {
    let id = UUID()
    let url: URL
}

struct LocalExportFileDocument: FileDocument, @unchecked Sendable {
    static var readableContentTypes: [UTType] { [.data] }

    private let wrapper: FileWrapper

    init(url: URL) throws {
        wrapper = try FileWrapper(url: url, options: .immediate)
    }

    init(configuration: ReadConfiguration) throws {
        wrapper = configuration.file
    }

    func fileWrapper(configuration: WriteConfiguration) throws -> FileWrapper {
        wrapper
    }
}

#if canImport(UIKit)
struct ActivityShareView: UIViewControllerRepresentable {
    let file: ShareableExportFile

    func makeUIViewController(context: Context) -> UIActivityViewController {
        UIActivityViewController(activityItems: [file.url], applicationActivities: nil)
    }

    func updateUIViewController(_ controller: UIActivityViewController, context: Context) {}
}
#endif
