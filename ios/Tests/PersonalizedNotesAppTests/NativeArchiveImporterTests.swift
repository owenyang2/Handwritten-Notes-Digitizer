import XCTest

@testable import PersonalizedNotes
import PersonalizedNotesKit

#if canImport(UIKit)
import UIKit
#endif

final class NativeArchiveImporterTests: XCTestCase {
    func testNativeArchiveRoundTripRemapsDocumentAndPageIdentity() throws {
        let originalPageIDs = [UUID(), UUID()]
        let document = DocumentRecord(
            documentID: UUID(),
            title: "Portable notebook",
            sourceKind: "native_ink",
            pages: originalPageIDs.enumerated().map { index, pageID in
                PageSummary(
                    pageID: pageID,
                    pageIndex: index,
                    title: index == 0 ? "Overview" : "Worked examples",
                    width: 768,
                    height: 1024,
                    dpi: 132,
                    coordinateSpace: "points",
                    paperTemplate: index == 0 ? "ruled" : "blank"
                )
            }
        )

        let archiveURL = try LocalNativeArchiveExporter.export(document: document)
        defer { try? FileManager.default.removeItem(at: archiveURL) }

        let imported = try NativeArchiveImporter.makeDocument(archiveURL: archiveURL)

        XCTAssertEqual(imported.title, document.title)
        XCTAssertEqual(imported.sourceKind, document.sourceKind)
        XCTAssertEqual(imported.pages.map(\.pageIndex), [0, 1])
        XCTAssertEqual(imported.pages.map(\.width), [768, 768])
        XCTAssertEqual(imported.pages.map(\.height), [1024, 1024])
        XCTAssertEqual(imported.pages.map(\.paperTemplate), ["ruled", "blank"])
        XCTAssertEqual(imported.pages.map(\.title), ["Overview", "Worked examples"])
        XCTAssertNotEqual(imported.documentID, document.documentID)
        XCTAssertNotEqual(imported.pages.map(\.pageID), originalPageIDs)
    }

    func testNativeArchiveRejectsPathTraversal() throws {
        let root = try makeTemporaryArchiveRoot()
        defer { try? FileManager.default.removeItem(at: root) }

        let manifest = makeManifest(
            page: NativeArchivePage(
                pageID: UUID(),
                pageIndex: 0,
                width: 768,
                height: 1024,
                dpi: 132,
                coordinateSpace: "points",
                nativeDrawing: nil,
                canonicalInk: NativeArchiveFile(
                    path: "pages/../outside.ink.json",
                    byteCount: 0,
                    sha256: String(repeating: "0", count: 64)
                )
            )
        )
        try writeManifest(manifest, to: root)

        XCTAssertThrowsError(try NativeArchiveImporter.makeDocument(archiveURL: root)) { error in
            guard case let NativeArchiveImportError.invalid(reason) = error else {
                return XCTFail("Expected an invalid archive error, got \(error)")
            }
            XCTAssertTrue(reason.contains("unsafe path"))
        }
    }

    func testNativeArchiveRejectsChecksumMismatch() throws {
        let root = try makeTemporaryArchiveRoot()
        defer { try? FileManager.default.removeItem(at: root) }
        let pagesURL = root.appendingPathComponent("pages", isDirectory: true)
        try FileManager.default.createDirectory(at: pagesURL, withIntermediateDirectories: true)
        let fileURL = pagesURL.appendingPathComponent("ink.json")
        try Data().write(to: fileURL)

        let manifest = makeManifest(
            page: NativeArchivePage(
                pageID: UUID(),
                pageIndex: 0,
                width: 768,
                height: 1024,
                dpi: 132,
                coordinateSpace: "points",
                nativeDrawing: nil,
                canonicalInk: NativeArchiveFile(
                    path: "pages/ink.json",
                    byteCount: 1,
                    sha256: String(repeating: "0", count: 64)
                )
            )
        )
        try writeManifest(manifest, to: root)

        XCTAssertThrowsError(try NativeArchiveImporter.makeDocument(archiveURL: root)) { error in
            guard case let NativeArchiveImportError.checksumMismatch(path) = error else {
                return XCTFail("Expected a checksum error, got \(error)")
            }
            XCTAssertEqual(path, "pages/ink.json")
        }
    }

    func testLocalInkCacheRemoveDeletesPublishedRevisionFiles() throws {
        let pageID = UUID()
        let bundle = InkBundle(
            pageID: pageID,
            coordinateSpace: InkCoordinateSpace(width: 768, height: 1024),
            strokes: []
        )
        try LocalInkCache.commit(
            pageID: pageID,
            pkDrawingData: Data("drawing".utf8),
            bundle: bundle
        )
        let directory = try LocalInkCache.directory()
        let prefix = "\(pageID.uuidString)."
        let committedFiles = try FileManager.default.contentsOfDirectory(
            at: directory,
            includingPropertiesForKeys: nil
        ).filter { $0.lastPathComponent.hasPrefix(prefix) }
        XCTAssertFalse(committedFiles.isEmpty)

        LocalInkCache.remove(pageID: pageID)

        let remainingFiles = try FileManager.default.contentsOfDirectory(
            at: directory,
            includingPropertiesForKeys: nil
        ).filter { $0.lastPathComponent.hasPrefix(prefix) }
        XCTAssertTrue(remainingFiles.isEmpty)
    }

    #if canImport(UIKit)
    func testMixedPageSourcesSurviveNativeArchiveRoundTrip() throws {
        let sourceDirectory = FileManager.default.temporaryDirectory
            .appendingPathComponent("hnd-mixed-source-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: sourceDirectory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: sourceDirectory) }

        let firstSource = sourceDirectory.appendingPathComponent("first.png")
        let secondSource = sourceDirectory.appendingPathComponent("second.png")
        let firstImage = UIGraphicsImageRenderer(size: CGSize(width: 40, height: 40)).image { context in
            UIColor.systemBlue.setFill()
            context.fill(CGRect(x: 0, y: 0, width: 40, height: 40))
        }
        let secondImage = UIGraphicsImageRenderer(size: CGSize(width: 60, height: 30)).image { context in
            UIColor.systemOrange.setFill()
            context.fill(CGRect(x: 0, y: 0, width: 60, height: 30))
        }
        try XCTUnwrap(firstImage.pngData()).write(to: firstSource)
        try XCTUnwrap(secondImage.pngData()).write(to: secondSource)

        let firstPage = PageSummary(
            pageID: UUID(),
            pageIndex: 0,
            width: 40,
            height: 40,
            dpi: 72,
            coordinateSpace: "image-pixels"
        )
        let secondPage = PageSummary(
            pageID: UUID(),
            pageIndex: 1,
            width: 60,
            height: 30,
            dpi: 72,
            coordinateSpace: "image-pixels"
        )
        let document = DocumentRecord(
            documentID: UUID(),
            title: "Mixed sources",
            sourceKind: "native_ink",
            pages: [firstPage, secondPage],
            sourceMetadata: [
                PageSourceMetadata.key: PageSourceMetadata.merge(
                    PageSourceMetadata.entries(
                        sourceURL: firstSource,
                        sourceKind: "image_import",
                        pages: [firstPage],
                        originalFilename: "first.png"
                    ),
                    with: PageSourceMetadata.entries(
                        sourceURL: secondSource,
                        sourceKind: "image_import",
                        pages: [secondPage],
                        originalFilename: "second.png"
                    )
                ),
            ]
        )

        let archiveURL = try LocalNativeArchiveExporter.export(document: document)
        defer { try? FileManager.default.removeItem(at: archiveURL) }
        let imported = try NativeArchiveImporter.makeDocument(archiveURL: archiveURL)
        defer {
            for page in imported.pages {
                if let source = PageSourceMetadata.descriptor(for: imported, page: page) {
                    try? FileManager.default.removeItem(at: source.url)
                }
            }
        }

        let importedSources = imported.pages.sorted { $0.pageIndex < $1.pageIndex }.compactMap {
            PageSourceMetadata.descriptor(for: imported, page: $0)
        }
        XCTAssertEqual(imported.sourceKind, "mixed_import")
        XCTAssertEqual(importedSources.map(\.kind), ["image_import", "image_import"])
        XCTAssertEqual(importedSources.map(\.sourcePageIndex), [0, 0])
        XCTAssertTrue(importedSources.allSatisfy { FileManager.default.fileExists(atPath: $0.url.path) })
        XCTAssertNotEqual(importedSources[0].url, importedSources[1].url)
    }
    #endif

    private func makeManifest(page: NativeArchivePage) -> NativeArchiveManifest {
        NativeArchiveManifest(
            schema: NativeArchiveFormat.schema,
            version: NativeArchiveFormat.version,
            exportedAt: Date(),
            documentID: UUID(),
            title: "Archive fixture",
            sourceKind: "native_ink",
            source: nil,
            pages: [page]
        )
    }

    private func makeTemporaryArchiveRoot() throws -> URL {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("hnd-archive-test-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        return root
    }

    private func writeManifest(_ manifest: NativeArchiveManifest, to root: URL) throws {
        let data = try APIJSON.encoder().encode(manifest)
        try data.write(to: root.appendingPathComponent("manifest.json"), options: .atomic)
    }
}
