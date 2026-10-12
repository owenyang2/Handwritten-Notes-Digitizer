import XCTest
import PDFKit
import PencilKit
import UIKit

@testable import PersonalizedNotes
import PersonalizedNotesKit

final class LocalExportRuntimeTests: XCTestCase {
    func testPagePDFAndRasterExportsAreReadableWithExactPageDimensions() throws {
        let page = makePage(index: 0, width: 320, height: 480)
        let document = DocumentRecord(
            documentID: UUID(),
            title: "Runtime export / page",
            sourceKind: "native_ink",
            pages: [page]
        )

        let pdfURL = try LocalPDFExporter.export(document: document, page: page)
        let pngURL = try LocalPDFExporter.exportPageImage(document: document, page: page, format: .png)
        let jpegURL = try LocalPDFExporter.exportPageImage(document: document, page: page, format: .jpeg)
        defer {
            try? FileManager.default.removeItem(at: pdfURL)
            try? FileManager.default.removeItem(at: pngURL)
            try? FileManager.default.removeItem(at: jpegURL)
        }

        let pdf = try XCTUnwrap(PDFDocument(url: pdfURL))
        XCTAssertEqual(pdf.pageCount, 1)
        let pdfBounds = try XCTUnwrap(pdf.page(at: 0)).bounds(for: .mediaBox)
        XCTAssertEqual(pdfBounds.width, CGFloat(page.width), accuracy: 0.5)
        XCTAssertEqual(pdfBounds.height, CGFloat(page.height), accuracy: 0.5)
        XCTAssertGreaterThan(try Data(contentsOf: pdfURL).count, 100)

        let png = try XCTUnwrap(UIImage(contentsOfFile: pngURL.path))
        XCTAssertEqual(png.size.width, CGFloat(page.width), accuracy: 1)
        XCTAssertEqual(png.size.height, CGFloat(page.height), accuracy: 1)
        XCTAssertEqual(try Data(contentsOf: pngURL).prefix(8), Data([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A]))

        let jpeg = try XCTUnwrap(UIImage(contentsOfFile: jpegURL.path))
        XCTAssertEqual(jpeg.size.width, CGFloat(page.width), accuracy: 1)
        XCTAssertEqual(jpeg.size.height, CGFloat(page.height), accuracy: 1)
        XCTAssertEqual(try Data(contentsOf: jpegURL).prefix(2), Data([0xFF, 0xD8]))
    }

    func testNotebookPDFPreservesPageOrderAndDimensions() throws {
        let pages = [
            makePage(index: 0, width: 768, height: 1024),
            makePage(index: 1, width: 1024, height: 768),
        ]
        let document = DocumentRecord(
            documentID: UUID(),
            title: "Two page runtime export",
            sourceKind: "native_ink",
            pages: pages.reversed()
        )

        let url = try LocalPDFExporter.export(document: document)
        defer { try? FileManager.default.removeItem(at: url) }

        let pdf = try XCTUnwrap(PDFDocument(url: url))
        XCTAssertEqual(pdf.pageCount, 2)
        XCTAssertEqual(
            try XCTUnwrap(pdf.page(at: 0)).bounds(for: .mediaBox).size,
            CGSize(width: 768, height: 1024)
        )
        XCTAssertEqual(
            try XCTUnwrap(pdf.page(at: 1)).bounds(for: .mediaBox).size,
            CGSize(width: 1024, height: 768)
        )
    }

    func testNotebookPDFSupports100PagesAndRotatedDimensions() throws {
        let pages = (0..<100).map { index in
            let isLandscape = index.isMultiple(of: 2)
            return makePage(
                index: index,
                width: isLandscape ? 1024 : 768,
                height: isLandscape ? 768 : 1024
            )
        }
        let document = DocumentRecord(
            documentID: UUID(),
            title: "One hundred page export",
            sourceKind: "native_ink",
            pages: pages
        )

        let url = try LocalPDFExporter.export(document: document)
        defer { try? FileManager.default.removeItem(at: url) }

        let pdf = try XCTUnwrap(PDFDocument(url: url))
        XCTAssertEqual(pdf.pageCount, 100)
        XCTAssertEqual(
            try XCTUnwrap(pdf.page(at: 0)).bounds(for: .mediaBox).size,
            CGSize(width: 1024, height: 768)
        )
        XCTAssertEqual(
            try XCTUnwrap(pdf.page(at: 1)).bounds(for: .mediaBox).size,
            CGSize(width: 768, height: 1024)
        )
        XCTAssertEqual(
            try XCTUnwrap(pdf.page(at: 99)).bounds(for: .mediaBox).size,
            CGSize(width: 768, height: 1024)
        )
        XCTAssertGreaterThan(try Data(contentsOf: url).count, 10_000)
    }

    func testRasterExportIsPixelDeterministicAndPreservesSourceAlignment() throws {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("hnd-export-pixel-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: directory) }

        let sourceURL = directory.appendingPathComponent("source.png")
        let sourceImage = UIGraphicsImageRenderer(size: CGSize(width: 40, height: 40)).image { context in
            UIColor.red.setFill()
            context.fill(CGRect(x: 0, y: 0, width: 40, height: 40))
        }
        try XCTUnwrap(sourceImage.pngData()).write(to: sourceURL)

        let page = PageSummary(
            pageID: UUID(),
            pageIndex: 0,
            width: 40,
            height: 40,
            dpi: 72,
            coordinateSpace: "image-pixels",
            paperTemplate: "ruled"
        )
        let document = DocumentRecord(
            documentID: UUID(),
            title: "Deterministic pixels",
            sourceKind: "image_import",
            pages: [page],
            sourceMetadata: [
                PageSourceMetadata.key: PageSourceMetadata.entries(
                    sourceURL: sourceURL,
                    sourceKind: "image_import",
                    pages: [page],
                    originalFilename: "source.png"
                ),
            ]
        )

        let firstURL = try LocalPDFExporter.exportPageImage(
            document: document,
            page: page,
            format: .png
        )
        let secondURL = try LocalPDFExporter.exportPageImage(
            document: document,
            page: page,
            format: .png
        )
        defer {
            try? FileManager.default.removeItem(at: firstURL)
            try? FileManager.default.removeItem(at: secondURL)
        }

        let firstData = try Data(contentsOf: firstURL)
        XCTAssertEqual(firstData, try Data(contentsOf: secondURL))
        let image = try XCTUnwrap(UIImage(contentsOfFile: firstURL.path))
        let center = try XCTUnwrap(pixel(in: image, x: 20, y: 20))
        XCTAssertGreaterThan(center.red, 200)
        XCTAssertLessThan(center.green, 80)
        XCTAssertLessThan(center.blue, 80)
    }

    func testCanonicalInkAndNativeArchiveExportsContainDurablePageContent() throws {
        let pages = [
            makePage(index: 0, width: 768, height: 1024),
            makePage(index: 1, width: 768, height: 1024),
        ]
        let document = DocumentRecord(
            documentID: UUID(),
            title: "Portable runtime notebook",
            sourceKind: "native_ink",
            pages: pages
        )
        let bundles = pages.map { page in
            InkBundle(
                pageID: page.pageID,
                coordinateSpace: InkCoordinateSpace(
                    width: Double(page.width),
                    height: Double(page.height)
                ),
                strokes: [
                    InkStroke(
                        points: [
                            InkPoint(x: 10, y: 12, timeOffset: 0),
                            InkPoint(x: 80, y: 90, timeOffset: 0.1),
                        ],
                        width: 2
                    ),
                ],
                nativeDrawing: NativeDrawing(data: PKDrawing().dataRepresentation())
            )
        }
        for (page, bundle) in zip(pages, bundles) {
            try LocalInkCache.commit(
                pageID: page.pageID,
                pkDrawingData: try XCTUnwrap(bundle.nativeDrawing?.data),
                bundle: bundle
            )
        }
        defer { pages.forEach { LocalInkCache.remove(pageID: $0.pageID) } }

        let canonicalURL = try LocalCanonicalInkExporter.export(
            page: pages[0],
            documentTitle: document.title
        )
        let archiveURL = try LocalNativeArchiveExporter.export(document: document)
        defer {
            try? FileManager.default.removeItem(at: canonicalURL)
            try? FileManager.default.removeItem(at: archiveURL)
        }

        let canonical = try InkV1Codec.decode(Data(contentsOf: canonicalURL))
        XCTAssertEqual(canonical, bundles[0])
        XCTAssertGreaterThan(try Data(contentsOf: canonicalURL).count, 100)

        let imported = try NativeArchiveImporter.makeDocument(archiveURL: archiveURL)
        XCTAssertEqual(imported.title, document.title)
        XCTAssertEqual(imported.pages.map { $0.pageIndex }, [0, 1])
        XCTAssertEqual(imported.pages.map { $0.paperTemplate }, pages.map { $0.paperTemplate })
        XCTAssertTrue(imported.pages.allSatisfy { LocalInkCache.readInkBundle(pageID: $0.pageID) != nil })
        imported.pages.forEach { LocalInkCache.remove(pageID: $0.pageID) }
    }

    func testOriginalSourceExportPreservesSourceBytesAndMissingSourceFailsCleanly() throws {
        let sourceURL = FileManager.default.temporaryDirectory
            .appendingPathComponent("hnd-export-source-\(UUID().uuidString).png")
        let sourceData = Data([0x89, 0x50, 0x4E, 0x47, 0x01, 0x02, 0x03])
        try sourceData.write(to: sourceURL)
        defer { try? FileManager.default.removeItem(at: sourceURL) }

        let page = makePage(index: 0, width: 100, height: 100)
        let document = DocumentRecord(
            documentID: UUID(),
            title: "Source export",
            sourceKind: "image_import",
            pages: [page],
            sourceMetadata: [
                PageSourceMetadata.key: PageSourceMetadata.entries(
                    sourceURL: sourceURL,
                    sourceKind: "image_import",
                    pages: [page],
                    originalFilename: "lecture.png"
                ),
            ]
        )

        let exportedURL = try LocalSourceExporter.exportOriginal(document: document)
        defer { try? FileManager.default.removeItem(at: exportedURL) }
        XCTAssertEqual(try Data(contentsOf: exportedURL), sourceData)
        XCTAssertEqual(exportedURL.pathExtension, "png")

        try FileManager.default.removeItem(at: sourceURL)
        XCTAssertThrowsError(try LocalSourceExporter.exportOriginal(document: document)) { error in
            guard case LocalExportError.sourceUnavailable = error else {
                return XCTFail("Expected sourceUnavailable, got \(error)")
            }
        }
    }

    func testSourceUnavailableAndPartiallySavedInkFailuresAreExplicit() throws {
        let missingSourceURL = FileManager.default.temporaryDirectory
            .appendingPathComponent("hnd-missing-source-\(UUID().uuidString).pdf")
        let page = makePage(index: 0, width: 200, height: 300)
        let document = DocumentRecord(
            documentID: UUID(),
            title: "Missing source runtime export",
            sourceKind: "pdf_import",
            pages: [page],
            sourceMetadata: [
                PageSourceMetadata.key: PageSourceMetadata.entries(
                    sourceURL: missingSourceURL,
                    sourceKind: "pdf_import",
                    pages: [page],
                    originalFilename: "missing.pdf"
                ),
            ]
        )

        XCTAssertThrowsError(try LocalPDFExporter.export(document: document, page: page)) { error in
            assertSourceUnavailable(error)
        }
        for format in [LocalPageImageFormat.png, .jpeg] {
            XCTAssertThrowsError(
                try LocalPDFExporter.exportPageImage(document: document, page: page, format: format)
            ) { error in
                assertSourceUnavailable(error)
            }
        }
        XCTAssertThrowsError(try LocalNativeArchiveExporter.export(document: document)) { error in
            assertSourceUnavailable(error)
        }
        XCTAssertThrowsError(try LocalSourceExporter.exportOriginal(document: document)) { error in
            assertSourceUnavailable(error)
        }

        let partialPage = makePage(index: 0, width: 200, height: 300)
        let partialDrawing = Data("partial-native-drawing".utf8)
        try LocalInkCache.writePKDrawing(partialDrawing, pageID: partialPage.pageID)
        defer { LocalInkCache.remove(pageID: partialPage.pageID) }
        XCTAssertThrowsError(
            try LocalCanonicalInkExporter.export(
                page: partialPage,
                documentTitle: "Partially saved ink"
            )
        ) { error in
            guard case LocalExportError.exportFailed = error else {
                return XCTFail("Expected an explicit partial-ink export failure, got \(error)")
            }
        }
    }

    private func makePage(index: Int, width: Int, height: Int) -> PageSummary {
        PageSummary(
            pageID: UUID(),
            pageIndex: index,
            title: index == 0 ? "First" : "Second",
            width: width,
            height: height,
            dpi: 132,
            coordinateSpace: "points",
            paperTemplate: index == 0 ? "ruled" : "grid"
        )
    }

    private func assertSourceUnavailable(_ error: Error, file: StaticString = #filePath, line: UInt = #line) {
        guard case LocalExportError.sourceUnavailable = error else {
            XCTFail("Expected sourceUnavailable, got \(error)", file: file, line: line)
            return
        }
    }

    private func pixel(
        in image: UIImage,
        x: Int,
        y: Int
    ) -> (red: UInt8, green: UInt8, blue: UInt8, alpha: UInt8)? {
        guard let cgImage = image.cgImage,
              let providerData = cgImage.dataProvider?.data,
              let bytes = CFDataGetBytePtr(providerData),
              cgImage.bitsPerPixel >= 32,
              x >= 0,
              y >= 0,
              x < cgImage.width,
              y < cgImage.height else {
            return nil
        }
        let bytesPerPixel = cgImage.bitsPerPixel / 8
        let offset = y * cgImage.bytesPerRow + x * bytesPerPixel
        let order = cgImage.bitmapInfo.rawValue & CGBitmapInfo.byteOrderMask.rawValue
        if order == CGBitmapInfo.byteOrder32Little.rawValue {
            return (
                red: bytes[offset + 2],
                green: bytes[offset + 1],
                blue: bytes[offset],
                alpha: bytes[offset + 3]
            )
        }
        return (
            red: bytes[offset],
            green: bytes[offset + 1],
            blue: bytes[offset + 2],
            alpha: bytes[offset + 3]
        )
    }
}
